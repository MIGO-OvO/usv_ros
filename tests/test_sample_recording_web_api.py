import json
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts.lib.sample_recording.storage import SampleRecordingStorage
from scripts.web_config_server import FLASK_AVAILABLE, MissionDataManager, String, WebConfigServer


def _msg(value):
    msg = String()
    msg.data = value
    return msg


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class SampleRecordingWebApiTest(unittest.TestCase):
    def test_sample_lifecycle_routes_and_manual_result_use_production_server_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = WebConfigServer(standalone=True)
            self.assertIsInstance(server.sample_storage, SampleRecordingStorage)

            server.data_manager = MissionDataManager(tmp)
            server.sample_storage = SampleRecordingStorage(tmp)
            server.current_position = {"wgs84": {"lat": 30.0, "lng": 120.0, "alt": 5.0}, "received_at": 1.0}
            server.current_waypoint_seq = 1
            server.latest_spectrometer_payload = {"absorbance": 0.3, "baseline_set": True}

            server._trigger_status_cb(_msg("sampling_started"))
            mission_id = server.data_manager.current_mission_data["mission_id"]
            self.assertIsNotNone(server.current_sample_window)
            for index, voltage in enumerate([1.2, 1.4, 1.6], start=1):
                server._spectrometer_raw_cb(_msg(json.dumps({
                    "timestamp_ms": index * 10,
                    "raw_code": 120 + index,
                    "voltage": voltage,
                    "valid": True,
                })))
            server.current_position = {"wgs84": {"lat": 30.1, "lng": 120.1, "alt": 6.0}, "received_at": 2.0}
            server._trigger_status_cb(_msg("sampling_stopped"))

            mission = server.data_manager.get_mission(mission_id)
            window = mission["sample_windows"][0]
            sample_id = window["sample_id"]
            raw_file = Path(tmp) / window["spectrometer"]["raw_file"]

            client = server.app.test_client()

            listed = client.get("/api/data/mission/%s/samples" % mission_id).get_json()
            detail = client.get("/api/data/mission/%s/sample/%s" % (mission_id, sample_id)).get_json()
            raw = client.get("/api/data/mission/%s/sample/%s/raw" % (mission_id, sample_id)).get_json()
            series = client.get("/api/data/voltage-series?mission_id=%s&sample_id=%s&max_points=4" % (mission_id, sample_id)).get_json()
            raw_csv = client.get("/api/data/mission/%s/sample/%s/raw.csv" % (mission_id, sample_id))
            archive = client.get("/api/data/mission/%s/archive" % mission_id)
            updated = client.post(
                "/api/data/mission/%s/sample/%s/manual-result" % (mission_id, sample_id),
                json={"analyte": "COD", "concentration": 0.84, "unit": "mg/L"},
            ).get_json()
            reloaded = server.data_manager.get_mission(mission_id)

            self.assertEqual(3, len(raw_file.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(1, len(listed["data"]["samples"]))
            self.assertEqual("closed", detail["data"]["state"])
            self.assertEqual({"lat": 30.0, "lng": 120.0, "alt": 5.0, "received_at": 1.0}, detail["data"]["gps_start"])
            self.assertEqual({"lat": 30.1, "lng": 120.1, "alt": 6.0, "received_at": 2.0}, detail["data"]["gps_end"])
            self.assertEqual(3, raw["data"]["count"])
            self.assertEqual(3, series["data"]["raw_count"])
            self.assertEqual(3, series["data"]["returned_count"])
            self.assertEqual(4, len(raw_csv.get_data(as_text=True).splitlines()))
            self.assertEqual(200, archive.status_code)
            with zipfile.ZipFile(io.BytesIO(archive.data)) as archive_file:
                root = "mission_%s" % mission_id
                names = set(archive_file.namelist())
                self.assertIn(root + "/mission.json", names)
                self.assertIn(root + "/summary.csv", names)
                self.assertIn(root + "/raw/%s.jsonl" % sample_id, names)
                self.assertIn(root + "/raw_csv/%s.csv" % sample_id, names)
                manifest = json.loads(archive_file.read(root + "/mission.json").decode("utf-8"))
                raw_csv_text = archive_file.read(root + "/raw_csv/%s.csv" % sample_id).decode("utf-8")
                self.assertEqual("completed", manifest["state"])
                self.assertEqual(4, len(raw_csv_text.splitlines()))
            archive.close()
            self.assertEqual("recorded", updated["data"]["manual_result"]["status"])
            self.assertEqual(0.84, reloaded["sample_windows"][0]["manual_result"]["concentration"])

    def test_standalone_server_uses_explicit_mission_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"USV_MISSION_DATA_DIR": tmp}):
                server = WebConfigServer(standalone=True)

            self.assertEqual(str(Path(tmp).resolve()), server.data_manager.data_dir)

    def test_sample_routes_report_missing_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = WebConfigServer(standalone=True)
            server.data_manager = MissionDataManager(tmp)
            server.sample_storage = SampleRecordingStorage(tmp)
            server.data_manager.start_mission("test")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            server.data_manager.stop_mission()

            client = server.app.test_client()
            response = client.get("/api/data/mission/%s/sample/missing/raw" % mission_id)

            self.assertEqual(404, response.status_code)

    def test_web_start_path_opens_sample_window_and_persists_raw(self):
        """Web 自动化启动路径必须打开采样窗口，否则原始分光帧不会落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            server = WebConfigServer(standalone=True)
            server.data_manager = MissionDataManager(tmp)
            server.sample_storage = SampleRecordingStorage(tmp)
            server.current_position = {"wgs84": {"lat": 30.0, "lng": 120.0, "alt": 5.0}, "received_at": 1.0}
            server.current_waypoint_seq = 1
            server.latest_spectrometer_payload = {"absorbance": 0.3, "baseline_set": True}

            server._start_data_recording_if_needed(source="web")
            server._start_sample_window_if_needed()
            self.assertIsNotNone(server.current_sample_window)

            for index, voltage in enumerate([1.2, 1.4, 1.6], start=1):
                server._spectrometer_raw_cb(_msg(json.dumps({
                    "timestamp_ms": index * 10,
                    "raw_code": 120 + index,
                    "voltage": voltage,
                    "valid": True,
                })))

            mission = server.data_manager.get_mission(server.data_manager.current_mission_data["mission_id"])
            window = mission["sample_windows"][0]
            raw_file = Path(tmp) / window["spectrometer"]["raw_file"]
            self.assertEqual(3, len(raw_file.read_text(encoding="utf-8").splitlines()))
            server._stop_data_recording_if_active()

    def test_list_missions_uses_cache_when_directory_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = MissionDataManager(tmp)
            manager.start_mission("cached")
            mission_id = manager.current_mission_data["mission_id"]
            manager.stop_mission()

            first = manager.list_missions()
            self.assertEqual(1, len(first))
            with mock.patch("scripts.web_config_server.json.load", side_effect=AssertionError("should use cache")):
                second = manager.list_missions()
            self.assertEqual(first, second)

            # 文件内容变化后缓存失效
            path = Path(tmp) / ("mission_%s.json" % mission_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["name"] = "renamed"
            path.write_text(json.dumps(data), encoding="utf-8")
            third = manager.list_missions()
            self.assertEqual("renamed", third[0]["name"])


if __name__ == "__main__":
    unittest.main()
