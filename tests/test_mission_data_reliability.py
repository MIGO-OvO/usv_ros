import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.web_config_server import MissionDataManager


class MissionDataReliabilityTest(unittest.TestCase):
    def test_start_and_stop_persist_explicit_mission_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = MissionDataManager(tmp)

            manager.start_mission("field-run")
            mission_id = manager.current_mission_data["mission_id"]
            running = manager.get_mission(mission_id)
            manager.stop_mission()
            completed = manager.get_mission(mission_id)

            self.assertEqual("running", running["state"])
            self.assertIsNone(running["end_time"])
            self.assertEqual("completed", completed["state"])
            self.assertIsNotNone(completed["end_time"])

    def test_startup_marks_unfinished_mission_as_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            mission_path = Path(tmp) / "mission_20260807_010203.json"
            mission_path.write_text(json.dumps({
                "mission_id": "20260807_010203",
                "name": "unfinished",
                "start_time": "2026-08-07T01:02:03",
                "end_time": None,
                "state": "running",
                "track_points": [],
                "route_waypoints": [],
                "sampling_events": [],
                "data_points": [],
            }), encoding="utf-8")

            manager = MissionDataManager(tmp)
            recovered = manager.get_mission("20260807_010203")

            self.assertEqual("interrupted", recovered["state"])
            self.assertEqual("web_server_restarted", recovered["interruption_reason"])
            self.assertIsNotNone(recovered["end_time"])
            self.assertIsNotNone(recovered["recovered_at"])

    def test_failed_atomic_save_keeps_previous_mission_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = MissionDataManager(tmp)
            manager.start_mission("atomic")
            mission_path = Path(manager.current_mission_file)
            previous = json.loads(mission_path.read_text(encoding="utf-8"))
            manager.current_mission_data["name"] = "changed"

            with mock.patch("scripts.web_config_server.json.dump", side_effect=RuntimeError("serialize failed")):
                with self.assertRaises(RuntimeError):
                    manager.save_current()

            self.assertEqual(previous, json.loads(mission_path.read_text(encoding="utf-8")))
            self.assertEqual([], list(Path(tmp).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
