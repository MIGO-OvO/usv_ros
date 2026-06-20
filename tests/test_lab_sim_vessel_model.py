import importlib.util
import math
import unittest
from pathlib import Path

from scripts.lib.lab_sim.vessel_model import SurveyWindowConfig, VesselSimulator
from scripts.lib.lab_sim.coordinates import CoordinatePair, parse_coordinate


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_lab_sim_node():
    spec = importlib.util.spec_from_file_location(
        "lab_sim_node_vessel_model_characterization",
        REPO_ROOT / "scripts" / "lab_sim_node.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LabSimVesselModelCharacterizationTests(unittest.TestCase):
    def test_given_boat_inside_arrival_radius_when_mission_steps_then_waits_for_sampling_done(self):
        module = _load_lab_sim_node()
        sim = module.LabSimulator({
            "start_lat": 25.0,
            "start_lng": 110.0,
            "arrival_radius_m": 3.0,
        })
        sim.start_mission([{"seq": 7, "lat": 25.0, "lng": 110.0}])

        snapshot = sim.step(0.0)
        arrivals = sim.drain_arrivals()

        self.assertFalse(snapshot["mission"]["active"])
        self.assertFalse(snapshot["mission"]["completed"])
        self.assertTrue(snapshot["mission"]["waiting_sampling_done"])
        self.assertEqual(arrivals[0]["seq"], 7)

    def test_given_waiting_waypoint_when_time_steps_then_position_pauses_until_completion(self):
        module = _load_lab_sim_node()
        sim = module.LabSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "max_speed_mps": 5.0,
            "arrival_radius_m": 3.0,
        })
        sim.start_mission([
            {"seq": 0, "lat": 30.0, "lng": 120.0},
            {"seq": 1, "lat": 30.0, "lng": 120.001},
        ])
        arrived = sim.step(0.0)

        paused = sim.step(10.0)
        sim.complete_mission()
        resumed = sim.step(1.0)

        self.assertTrue(arrived["mission"]["waiting_sampling_done"])
        self.assertAlmostEqual(paused["lat"], 30.0, places=7)
        self.assertAlmostEqual(paused["lng"], 120.0, places=7)
        self.assertGreater(resumed["lng"], paused["lng"])

    def test_given_schema_v2_coordinates_when_configuring_then_wgs84_values_drive_navigation(self):
        start_click = CoordinatePair.from_gcj02(parse_coordinate(25.314167, 110.412778))
        waypoint_click = CoordinatePair.from_gcj02(parse_coordinate(25.315, 110.413))
        start_payload = start_click.as_dict()
        start_payload["lat"] = start_click.gcj02.lat
        start_payload["lng"] = start_click.gcj02.lng
        waypoint_payload = waypoint_click.as_dict()
        waypoint_payload["lat"] = waypoint_click.gcj02.lat
        waypoint_payload["lng"] = waypoint_click.gcj02.lng
        waypoint_payload["seq"] = 4
        sim = VesselSimulator({
            "start": start_payload,
            "start_lat": start_click.gcj02.lat,
            "start_lng": start_click.gcj02.lng,
            "arrival_radius_m": 1.0,
        })

        snapshot = sim.start_mission([waypoint_payload])

        self.assertAlmostEqual(snapshot["lat"], start_click.wgs84.lat, places=8)
        self.assertAlmostEqual(snapshot["lng"], start_click.wgs84.lng, places=8)
        self.assertAlmostEqual(sim.waypoints[0].coordinate.lat, waypoint_click.wgs84.lat, places=8)
        self.assertAlmostEqual(sim.waypoints[0].coordinate.lng, waypoint_click.wgs84.lng, places=8)
        self.assertNotAlmostEqual(snapshot["lat"], start_click.gcj02.lat, places=6)
        self.assertNotAlmostEqual(sim.waypoints[0].coordinate.lat, waypoint_click.gcj02.lat, places=6)
        self.assertEqual(snapshot["mission"]["target_seq"], 4)

    def test_given_last_waypoint_sample_completed_when_complete_mission_then_latches_completed(self):
        module = _load_lab_sim_node()
        sim = module.LabSimulator({
            "start_lat": 25.0,
            "start_lng": 110.0,
            "arrival_radius_m": 3.0,
        })
        sim.start_mission([{"seq": 1, "lat": 25.0, "lng": 110.0}])
        sim.step(0.0)

        snapshot = sim.complete_mission()

        self.assertFalse(snapshot["running"])
        self.assertFalse(snapshot["mission"]["active"])
        self.assertTrue(snapshot["mission"]["completed"])
        self.assertEqual(snapshot["mission"]["reached_count"], 1)

    def test_given_high_latitude_east_motion_when_step_then_longitude_uses_cosine_scaling(self):
        module = _load_lab_sim_node()
        sim = module.LabSimulator({
            "start_lat": 60.0,
            "start_lng": 10.0,
            "heading_deg": 90.0,
            "max_speed_mps": 10.0,
            "wheel_base_m": 0.6,
        })
        sim.set_virtual_propulsion(1.0, 1.0)

        snapshot = sim.step(10.0)

        expected_delta_lng = 100.0 / (111320.0 * math.cos(math.radians(60.0)))
        self.assertAlmostEqual(snapshot["lat"], 60.0, places=5)
        self.assertAlmostEqual(snapshot["lng"] - 10.0, expected_delta_lng, delta=expected_delta_lng * 0.02)

    def test_given_survey_distance_window_when_distance_accumulates_then_trigger_resets_distance(self):
        sim = VesselSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "heading_deg": 0.0,
            "max_speed_mps": 2.0,
            "wheel_base_m": 0.6,
        })
        sim.configure_survey_window(SurveyWindowConfig(distance_m=5.0, time_s=0.0))
        sim.set_virtual_propulsion(1.0, 1.0)

        before = sim.step(2.0)
        triggered = sim.step(1.0)

        self.assertFalse(before["survey"]["window_triggered"])
        self.assertTrue(triggered["survey"]["window_triggered"])
        self.assertEqual(triggered["survey"]["window_index"], 1)
        self.assertLess(triggered["survey"]["distance_since_window_m"], 1.5)

    def test_given_survey_time_window_when_time_accumulates_then_trigger_resets_time(self):
        sim = VesselSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "max_speed_mps": 0.0,
        })
        sim.configure_survey_window(SurveyWindowConfig(distance_m=0.0, time_s=3.0))
        sim.start()

        before = sim.step(2.0)
        triggered = sim.step(1.5)

        self.assertFalse(before["survey"]["window_triggered"])
        self.assertTrue(triggered["survey"]["window_triggered"])
        self.assertEqual(triggered["survey"]["window_index"], 1)
        self.assertAlmostEqual(triggered["survey"]["time_since_window_s"], 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
