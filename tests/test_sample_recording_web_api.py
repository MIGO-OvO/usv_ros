import json
import tempfile
import unittest
from pathlib import Path

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
            self.assertEqual("recorded", updated["data"]["manual_result"]["status"])
            self.assertEqual(0.84, reloaded["sample_windows"][0]["manual_result"]["concentration"])

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


if __name__ == "__main__":
    unittest.main()
