import copy
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from scripts.lib.sample_recording.downsampling import MinMaxSeries
from scripts.lib.sample_recording.storage import SampleRecordingStorage
from scripts.web_config_server import FLASK_AVAILABLE, MissionDataManager, WebConfigServer, build_mission_summary


def fixture(mission_id="history", count=10000):
    return {
        "mission_id": mission_id, "name": "history", "end_time": "2026-01-01",
        "data_points": [{"timestamp": str(i), "voltage": 99 if i == 321 else -99 if i == 322 else i % 10}
                        for i in range(count)],
        "sample_windows": [{"sample_id": "sample", "manual_result": {}}],
        "track_points": [{"lat": 30, "lng": 120}],
    }


class HistoryCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = MissionDataManager(self.tmp.name, cache_capacity=2)
        self.path = Path(self.tmp.name) / "mission_history.json"
        self.path.write_text(json.dumps(fixture()), encoding="utf-8")

    def test_concurrent_requests_parse_and_summarize_once(self):
        with mock.patch("scripts.web_config_server.json.load", wraps=json.load) as load, \
                mock.patch("scripts.web_config_server.build_mission_summary", wraps=build_mission_summary) as summary:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(self.manager.get_mission, ["history"] * 16))
            self.assertEqual(1, load.call_count)
            self.assertEqual(1, summary.call_count)
            self.assertTrue(all(result is results[0] for result in results))

    def test_mtime_size_save_delete_and_recreate_invalidate(self):
        first = self.manager.get_mission("history")
        stat = self.path.stat()
        self.path.write_text(self.path.read_text().replace('"history"', '"changed"'), encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        self.assertEqual("changed", self.manager.get_mission("history")["name"])
        # Size alone invalidates even when mtime is restored.
        stat = self.path.stat()
        self.path.write_text(self.path.read_text() + " ", encoding="utf-8")
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with mock.patch("scripts.web_config_server.json.load", wraps=json.load) as load:
            self.manager.get_mission("history")
            self.assertEqual(1, load.call_count)
        updated = copy.deepcopy(first)
        updated["name"] = "saved"
        self.assertTrue(self.manager.save_mission("history", updated))
        self.assertEqual("saved", self.manager.get_mission("history")["name"])
        self.assertTrue(self.manager.delete_mission("history"))
        self.assertIsNone(self.manager.get_mission("history"))
        self.path.write_text(json.dumps(first), encoding="utf-8")
        self.assertEqual("history", self.manager.get_mission("history")["name"])

    def test_lru_and_byte_budget(self):
        for name in ("a", "b", "c"):
            (Path(self.tmp.name) / ("mission_%s.json" % name)).write_text(json.dumps(fixture(name, 1)))
        self.manager.get_mission("a")
        self.manager.get_mission("b")
        self.manager.get_mission("a")
        self.manager.get_mission("c")
        self.assertEqual(["a", "c"], list(self.manager._mission_cache))
        self.manager._cache_max_bytes = 1
        self.manager.get_mission("history")
        self.assertNotIn("history", self.manager._mission_cache)

    def test_replacement_during_parse_retries_and_invalid_json_is_not_cached(self):
        original = json.load
        replacement = fixture(count=2)
        replacement["name"] = "replacement"
        def replace_after_read(file):
            value = original(file)
            self.path.write_text(json.dumps(replacement), encoding="utf-8")
            return value
        # Replace only the first read; second read uses the new stable file.
        calls = 0
        def once(file):
            nonlocal calls
            calls += 1
            return replace_after_read(file) if calls == 1 else original(file)
        with mock.patch("scripts.web_config_server.json.load", side_effect=once):
            self.assertEqual("replacement", self.manager.get_mission("history")["name"])
        self.assertEqual(2, calls)
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.manager.get_mission("history")
        self.assertNotIn("history", self.manager._mission_cache)

    def test_live_data_bypasses_history_cache(self):
        self.manager.start_mission("live")
        mission_id = self.manager.current_mission_data["mission_id"]
        with mock.patch("scripts.web_config_server.json.load", side_effect=AssertionError("disk read")):
            self.manager.get_mission(mission_id)
            self.manager.current_mission_data["data_points"].append({"voltage": 3})
            self.assertEqual(1, len(self.manager.get_mission(mission_id)["data_points"]))
        self.assertNotIn(mission_id, self.manager._mission_cache)

    def test_list_reuses_detail_and_overview_is_cached_lossless_source(self):
        with mock.patch("scripts.web_config_server.json.load", wraps=json.load) as load:
            self.manager.list_missions()
            overview = self.manager.get_mission_overview("history")
            self.assertIs(overview, self.manager.get_mission_overview("history"))
            full = self.manager.get_mission("history")
            self.assertEqual(1, load.call_count)
        self.assertEqual(10000, len(full["data_points"]))
        self.assertLessEqual(len(overview["data_points"]), 1500)
        self.assertNotIn("track_points", overview)
        values = [point["voltage"] for point in overview["data_points"]]
        self.assertIn(99, values)
        self.assertIn(-99, values)
        self.assertEqual(full["data_points"][0], overview["data_points"][0])
        self.assertEqual(full["data_points"][-1], overview["data_points"][-1])


class SinglePassSeriesTest(unittest.TestCase):
    def test_single_scan_filter_endpoints_peaks_and_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = SampleRecordingStorage(tmp)
            path = Path(tmp) / "raw/history/sample.jsonl"
            path.parent.mkdir(parents=True)
            frames = [{"timestamp_ms": i, "voltage": 100 if i == 301 else -100 if i == 302 else i % 7}
                      for i in range(1000)]
            path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
            with mock.patch("builtins.open", wraps=open) as opened, \
                    mock.patch("scripts.lib.sample_recording.storage.json.loads", wraps=json.loads) as loads:
                result = storage.read_raw_series("history", "sample", from_ms=100, to_ms=900, max_points=40)
            self.assertEqual(1, opened.call_count)
            self.assertEqual(1000, loads.call_count)
            self.assertEqual(801, result["raw_count"])
            self.assertLessEqual(result["returned_count"], 40)
            self.assertEqual(100, result["samples"][0]["timestamp_ms"])
            self.assertEqual(900, result["samples"][-1]["timestamp_ms"])
            self.assertEqual({-100, 100}, {min(p["voltage"] for p in result["samples"]), max(p["voltage"] for p in result["samples"])})
            self.assertEqual("minmax", result["method"])
            self.assertFalse(result["covered"])
            small = storage.read_raw_series("history", "sample", from_ms=0, to_ms=3, max_points=4)
            self.assertEqual(frames[:4], small["samples"])
            self.assertTrue(small["covered"])
            self.assertEqual([], storage.read_raw_series("history", "sample", from_ms=2000)["samples"])

    def test_reducer_order_budget_invalid_values_and_small_inputs(self):
        for budget in (4, 5, 20, 100):
            reducer = MinMaxSeries(budget)
            frames = [{"index": i, "voltage": None if i % 5 == 0 else i % 17} for i in range(5000)]
            frames[101]["voltage"] = float("nan")
            frames[102]["voltage"] = float("inf")
            frames[110]["voltage"] = -20
            frames[111]["voltage"] = 20
            for frame in frames:
                reducer.add(frame)
                self.assertLessEqual(len(reducer.buckets), reducer.bucket_limit)
            samples = reducer.samples()
            self.assertLessEqual(len(samples), budget)
            self.assertEqual([0, 4999], [samples[0]["index"], samples[-1]["index"]])
            self.assertEqual(sorted(p["index"] for p in samples), [p["index"] for p in samples])
            self.assertIn(-20, [p["voltage"] for p in samples])
            self.assertIn(20, [p["voltage"] for p in samples])


@unittest.skipUnless(FLASK_AVAILABLE, "Flask unavailable")
class HistoryApiTest(unittest.TestCase):
    def test_manual_save_does_not_detach_running_sample_window(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"USV_MISSION_DATA_DIR": tmp}):
            server = WebConfigServer(standalone=True)
            server.data_manager.start_mission("live")
            data = server.data_manager.current_mission_data
            window = server.sample_storage.start_window(data, {"sample_id": "sample"})
            url = "/api/data/mission/%s/sample/sample/manual-result" % data["mission_id"]
            self.assertEqual(200, server.app.test_client().post(url, json={"concentration": 3}).status_code)
            self.assertIs(window, server.data_manager.current_mission_data["sample_windows"][0])
            server.sample_storage.append_raw_frame(window, {"timestamp_ms": 1, "voltage": 1})
            self.assertEqual(1, server.data_manager.get_mission(data["mission_id"])["sample_windows"][0]["spectrometer"]["frame_count"])
            server.sample_storage.close()

    def test_combined_api_lazy_raw_cache_and_failed_manual_save(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"USV_MISSION_DATA_DIR": tmp}):
            server = WebConfigServer(standalone=True)
            path = Path(tmp) / "mission_history.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            client = server.app.test_client()
            with mock.patch("scripts.web_config_server.json.load", wraps=json.load) as load, \
                    mock.patch.object(server.sample_storage, "read_raw_series", wraps=server.sample_storage.read_raw_series) as raw:
                overview = client.get("/api/data/mission/history?view=data-center").get_json()["data"]
                self.assertEqual(0, raw.call_count)
                self.assertEqual(1, len(overview["samples"]))
                self.assertLessEqual(len(overview["data_points"]), 1500)
                for url in ("/api/data/mission/history", "/api/data/mission/history/samples",
                            "/api/data/mission/history/sample/sample",
                            "/api/data/voltage-series?mission_id=history&sample_id=sample"):
                    self.assertEqual(200, client.get(url).status_code)
                self.assertEqual(1, load.call_count)
                self.assertEqual(1, raw.call_count)
            borrowed = server.data_manager.get_mission("history")
            with mock.patch.object(server.data_manager, "_atomic_write_json", side_effect=OSError("disk full")):
                response = client.post("/api/data/mission/history/sample/sample/manual-result", json={"concentration": 2})
            self.assertEqual(500, response.status_code)
            self.assertEqual({}, borrowed["sample_windows"][0]["manual_result"])
            self.assertEqual({}, server.data_manager.get_mission("history")["sample_windows"][0]["manual_result"])
            response = client.post("/api/data/mission/history/sample/sample/manual-result", json={"concentration": 3})
            self.assertEqual(200, response.status_code)
            self.assertEqual(3, client.get("/api/data/mission/history?view=data-center").get_json()["data"]["samples"][0]["manual_result"]["concentration"])
            legacy = fixture("legacy", 0)
            del legacy["sample_windows"]
            (Path(tmp) / "mission_legacy.json").write_text(json.dumps(legacy))
            self.assertEqual([], client.get("/api/data/mission/legacy?view=data-center").get_json()["data"]["samples"])


if __name__ == "__main__":
    unittest.main()
