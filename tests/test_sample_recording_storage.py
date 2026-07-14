import json
import tempfile
import unittest
from pathlib import Path

from scripts.lib.sample_recording.models import normalize_raw_frame
from scripts.lib.sample_recording.storage import SampleRecordingStorage


class SampleRecordingStorageTest(unittest.TestCase):
    def test_window_lifecycle_records_raw_summary_and_manual_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission = {"mission_id": "mission_test", "data_points": []}
            storage = SampleRecordingStorage(tmp)

            window = storage.start_window(
                mission,
                {"mode": "waypoint", "source": "fcu", "waypoint_seq": 3, "mavlink_sample_id": 18},
                {"lat": 30.0, "lng": 120.0, "alt": None, "received_at": 1.0},
            )
            storage.append_raw_frame(window, normalize_raw_frame({"seq": 7, "timestamp_ms": 1, "source_timestamp_ms": 1, "received_at_ms": 1001, "raw_code": 10, "voltage": 1.0, "valid": True}))
            storage.append_raw_frame(window, normalize_raw_frame({"timestamp_ms": 2, "raw_code": 12, "voltage": 2.0, "valid": False, "saturated": True}))
            closed = storage.close_window(mission, window, {"lat": 30.1, "lng": 120.1, "alt": None, "received_at": 2.0})
            updated = storage.update_manual_result(mission, closed["sample_id"], {"analyte": "COD", "concentration": "0.84", "unit": "mg/L"})

            raw_file = Path(tmp) / closed["spectrometer"]["raw_file"]
            self.assertEqual(2, len(raw_file.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(7, json.loads(raw_file.read_text(encoding="utf-8").splitlines()[0])["seq"])
            self.assertEqual({"lat": 30.0, "lng": 120.0, "alt": None, "received_at": 1.0}, closed["gps_start"])
            self.assertEqual({"lat": 30.1, "lng": 120.1, "alt": None, "received_at": 2.0}, closed["gps_end"])
            self.assertEqual(1, closed["spectrometer"]["valid_count"])
            self.assertEqual(1, closed["spectrometer"]["invalid_count"])
            self.assertEqual(1.5, closed["spectrometer"]["voltage_mean"])
            self.assertIn("saturated_seen", closed["spectrometer"]["quality_flags"])
            self.assertEqual("recorded", updated["manual_result"]["status"])
            self.assertEqual(0.84, updated["manual_result"]["concentration"])

    def test_read_raw_frames_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = SampleRecordingStorage(tmp)

            with self.assertRaises(ValueError):
                storage.read_raw_frames("mission_test", "../secret")

    def test_empty_window_has_no_frames_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission = {"mission_id": "mission_empty"}
            storage = SampleRecordingStorage(tmp)
            window = storage.start_window(mission, {"mode": "manual"})

            closed = storage.close_window(mission, window)

            self.assertEqual(0, closed["spectrometer"]["frame_count"])
            self.assertIn("no_frames", closed["spectrometer"]["quality_flags"])

    def test_raw_series_streaming_minmax_keeps_peak_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission = {"mission_id": "mission_series"}
            storage = SampleRecordingStorage(tmp)
            window = storage.start_window(mission, {"sample_id": "sample_series"})
            for index in range(1000):
                storage.append_raw_frame(window, {
                    "received_at_ms": index,
                    "voltage": 9.0 if index == 501 else -4.0 if index == 502 else 1.0,
                    "valid": True,
                })

            series = storage.read_raw_series("mission_series", window["sample_id"], max_points=100)

            self.assertEqual(1000, series["raw_count"])
            self.assertLessEqual(series["returned_count"], 100)
            self.assertFalse(series["covered"])
            self.assertIn(9.0, [frame["voltage"] for frame in series["samples"]])
            self.assertIn(-4.0, [frame["voltage"] for frame in series["samples"]])

    def test_window_lifecycle_accepts_web_position_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission = {"mission_id": "mission_web"}
            storage = SampleRecordingStorage(tmp)

            window = storage.start_window(
                mission,
                {"mode": "manual"},
                {"wgs84": {"lat": 31.2, "lng": 121.4, "alt": 5.0}, "received_at": 11.0},
            )
            closed = storage.close_window(
                mission,
                window,
                {"wgs84": {"lat": 31.3, "lng": 121.5, "alt": 6.0}, "received_at": 12.0},
            )

            self.assertEqual({"lat": 31.2, "lng": 121.4, "alt": 5.0, "received_at": 11.0}, closed["gps_start"])
            self.assertEqual({"lat": 31.3, "lng": 121.5, "alt": 6.0, "received_at": 12.0}, closed["gps_end"])
            self.assertEqual(closed["gps_end"], closed["gps_latest"])

    def test_close_window_rebuilds_summary_after_storage_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission = {"mission_id": "mission_restart"}
            storage = SampleRecordingStorage(tmp)
            window = storage.start_window(mission, {"mode": "manual"})
            storage.append_raw_frame(window, normalize_raw_frame({"timestamp_ms": 1, "raw_code": 10, "voltage": 1.0, "valid": True}))
            storage.append_raw_frame(window, normalize_raw_frame({"timestamp_ms": 2, "raw_code": 20, "voltage": 3.0, "valid": True}))

            restarted_storage = SampleRecordingStorage(tmp)
            closed = restarted_storage.close_window(mission, window)

            self.assertEqual(2, closed["spectrometer"]["frame_count"])
            self.assertEqual(2, closed["spectrometer"]["valid_count"])
            self.assertEqual(2.0, closed["spectrometer"]["voltage_mean"])
            self.assertEqual(10.0, closed["spectrometer"]["raw_code_min"])
            self.assertEqual(20.0, closed["spectrometer"]["raw_code_max"])


if __name__ == "__main__":
    unittest.main()
