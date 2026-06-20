import json
import tempfile
import unittest
from pathlib import Path


class CountingMissionDataManager:
    def __init__(self, manager):
        self.manager = manager
        self.save_count = 0
        original_save = manager._save_current

        def counted_save():
            self.save_count += 1
            original_save()

        manager._save_current = counted_save


def _droplet(index, estimated=1.5):
    return {
        "droplet_index": index,
        "offset_ms": index * 25,
        "voltage": 2.5 + index * 0.001,
        "absorbance": 0.12 + index * 0.0001,
        "truth_concentration": 1.6,
        "estimated_concentration": estimated,
        "valid": True,
        "saturated": False,
        "noise_flags": [],
    }


def _sampling_event(event_id="sample-1", droplet_count=64):
    return {
        "schema_version": 2,
        "event_id": event_id,
        "mode": "waypoint",
        "route_ref": {"route_id": "route-1", "waypoint_index": 0},
        "position": {
            "wgs84": {"lat": 25.32259176452231, "lng": 110.39774012635971, "alt": None},
            "gcj02": {"lat": 25.314167, "lng": 110.412778, "alt": None},
        },
        "analyte_id": "nh3n",
        "droplets": [_droplet(index) for index in range(droplet_count)],
        "mean": 1.5,
        "median": 1.5,
        "standard_deviation": 0.02,
        "valid_count": droplet_count,
        "quality_flags": [],
        "config_snapshot": {"schema_version": 2, "droplet_count": droplet_count},
    }


class TestSamplingEventMissionStorage(unittest.TestCase):
    def test_sampling_event_persists_one_event_one_map_point_and_one_save(self):
        from scripts import web_config_server as module

        # Given an active mission and one bounded 64-droplet lab event
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            counter = CountingMissionDataManager(manager)
            manager.start_mission("event-storage")
            counter.save_count = 0

            # When the event is appended
            manager.add_sampling_event(_sampling_event())
            persisted = json.loads(Path(manager.current_mission_file).read_text(encoding="utf-8"))

        # Then one event and one aggregate map point are persisted with one save
        self.assertEqual(counter.save_count, 1)
        self.assertEqual(len(persisted["sampling_events"]), 1)
        self.assertEqual(len(persisted["sampling_events"][0]["droplets"]), 64)
        self.assertEqual(len(persisted["data_points"]), 1)
        self.assertEqual(persisted["data_points"][0]["sample_event_id"], "sample-1")
        self.assertEqual(persisted["data_points"][0]["droplet_count"], 64)

    def test_one_hundred_events_produce_one_hundred_points_and_event_level_saves(self):
        from scripts import web_config_server as module

        # Given an active mission
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            counter = CountingMissionDataManager(manager)
            manager.start_mission("bulk-event-storage")
            counter.save_count = 0

            # When 100 bounded events are appended
            for index in range(100):
                manager.add_sampling_event(_sampling_event("sample-%03d" % index, droplet_count=12))
            persisted = json.loads(Path(manager.current_mission_file).read_text(encoding="utf-8"))

        # Then storage scales by event count, not droplet count
        self.assertEqual(counter.save_count, 100)
        self.assertEqual(len(persisted["sampling_events"]), 100)
        self.assertEqual(len(persisted["data_points"]), 100)
        self.assertEqual(sum(len(event["droplets"]) for event in persisted["sampling_events"]), 1200)

    def test_legacy_mission_without_sampling_events_stays_readable(self):
        from scripts import web_config_server as module

        # Given an older mission JSON with only data_points
        with tempfile.TemporaryDirectory() as tmpdir:
            missions = Path(tmpdir) / "missions"
            missions.mkdir()
            mission_file = missions / "mission_legacy.json"
            mission_file.write_text(json.dumps({
                "mission_id": "legacy",
                "name": "legacy",
                "start_time": "2026-06-18T00:00:00",
                "end_time": None,
                "track_points": [],
                "route_waypoints": [],
                "data_points": [{"voltage": 1.0, "absorbance": 0.2, "concentration": 0.3}],
            }), encoding="utf-8")
            manager = module.MissionDataManager(str(missions))

            # When it is read through the existing history APIs
            mission = manager.get_mission("legacy")
            listed = manager.list_missions()

        # Then it remains readable and summary still comes from data_points
        self.assertEqual(mission["mission_id"], "legacy")
        self.assertEqual(mission["summary"]["point_count"], 1)
        self.assertEqual(listed[0]["point_count"], 1)

    def test_malformed_sampling_event_is_rejected_without_partial_persistence(self):
        from scripts import web_config_server as module
        from scripts.lib.lab_sim.models import ModelParseError

        # Given an active mission and an event with an invalid droplet_count
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            counter = CountingMissionDataManager(manager)
            manager.start_mission("bad-event")
            counter.save_count = 0
            bad_event = _sampling_event("bad-event", droplet_count=2)

            # When the malformed event is appended
            with self.assertRaises(ModelParseError):
                manager.add_sampling_event(bad_event)
            persisted = json.loads(Path(manager.current_mission_file).read_text(encoding="utf-8"))

        # Then no sampling event, map point, or event-level save is written
        self.assertEqual(counter.save_count, 0)
        self.assertEqual(persisted["sampling_events"], [])
        self.assertEqual(persisted["data_points"], [])

    def test_sampling_event_rejects_droplet_count_mismatch_without_partial_persistence(self):
        from scripts import web_config_server as module
        from scripts.lib.lab_sim.models import ModelParseError

        # Given an active mission and 65 droplets hidden behind an in-range snapshot count
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            counter = CountingMissionDataManager(manager)
            manager.start_mission("mismatch-event")
            counter.save_count = 0
            bad_event = _sampling_event("mismatch-event", droplet_count=64)
            bad_event["droplets"].append(_droplet(64))

            # When the mismatched event is appended
            with self.assertRaises(ModelParseError):
                manager.add_sampling_event(bad_event)
            persisted = json.loads(Path(manager.current_mission_file).read_text(encoding="utf-8"))

        # Then no partial event or aggregate map point is written
        self.assertEqual(counter.save_count, 0)
        self.assertEqual(persisted["sampling_events"], [])
        self.assertEqual(persisted["data_points"], [])

    def test_legacy_real_device_data_point_api_still_records_single_points(self):
        from scripts import web_config_server as module

        # Given an active mission
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            manager.start_mission("real-device")

            # When the legacy real-device point API is used
            manager.add_data_point(1.2, 0.4)
            mission_file = Path(manager.current_mission_file)
            manager.stop_mission()
            persisted = json.loads(mission_file.read_text(encoding="utf-8"))

        # Then the existing single-point path remains available
        self.assertEqual(len(persisted["data_points"]), 1)
        self.assertEqual(persisted["data_points"][0]["voltage"], 1.2)
        self.assertEqual(persisted["sampling_events"], [])


if __name__ == "__main__":
    unittest.main()
