import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "mission_plan_service_test_module",
        REPO_ROOT / "scripts" / "mission_plan_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MissionPlanServiceTests(unittest.TestCase):
    def test_builds_waypoints_and_sampling_script_items(self):
        module = _load_module()

        result = module.build_mission_plan({
            "waypoints": [
                {"lat": 30.0, "lng": 120.0, "sample": True},
                {"lat": 30.001, "lng": 120.002, "sample": False},
            ],
        })

        self.assertTrue(result["valid"])
        self.assertEqual([item["command"] for item in result["items"]], [16, 42702, 16])
        script = result["items"][1]
        self.assertEqual(script["param1"], 1.0)
        self.assertEqual(script["param2"], 255.0)
        self.assertFalse(result["start_auto"])

    def test_rejects_invalid_coordinates_before_mavros_upload(self):
        module = _load_module()

        result = module.build_mission_plan({
            "waypoints": [
                {"lat": 91.0, "lng": 120.0},
                {"lat": 30.001, "lng": 120.002},
            ],
        })

        self.assertFalse(result["valid"])
        self.assertIn("waypoints[0].lat", result["errors"][0])

    def test_upload_uses_clear_push_pull_and_never_changes_mode(self):
        module = _load_module()
        state = types.SimpleNamespace(cleared=0, pushed=[], pulled=0, service_names=[])

        class Waypoint:
            pass

        class WaypointList:
            def __init__(self, waypoints=None):
                self.waypoints = list(waypoints or [])

        def ardupilot_readback_waypoints():
            home = Waypoint()
            home.frame = module.MAV_FRAME_GLOBAL_RELATIVE_ALT
            home.command = module.MAV_CMD_NAV_WAYPOINT
            home.is_current = False
            home.autocontinue = True
            home.param1 = 0.0
            home.param2 = 0.0
            home.param3 = 0.0
            home.param4 = 0.0
            home.x_lat = 25.0
            home.y_long = 110.0
            home.z_alt = 0.0
            if not state.pushed:
                return []
            return [home] + list(state.pushed[-1][1:])

        class FakeRospy:
            @staticmethod
            def wait_for_service(name, timeout=None):
                state.service_names.append(("wait", name))

            @staticmethod
            def ServiceProxy(name, service_class):
                state.service_names.append(("proxy", name))

                def call(**kwargs):
                    if name == "/mavros/mission/clear":
                        state.cleared += 1
                        return types.SimpleNamespace(success=True)
                    if name == "/mavros/mission/push":
                        state.pushed.append(list(kwargs["waypoints"]))
                        return types.SimpleNamespace(success=True, wp_transfered=len(kwargs["waypoints"]))
                    if name == "/mavros/mission/pull":
                        state.pulled += 1
                        return types.SimpleNamespace(success=True, wp_received=len(state.pushed[-1]))
                    raise AssertionError("unexpected service: %s" % name)

                return call

            @staticmethod
            def wait_for_message(name, message_class, timeout=None):
                return WaypointList(ardupilot_readback_waypoints())

        result = module.upload_mission_plan(
            {
                "waypoints": [
                    {"lat": 30.0, "lng": 120.0, "sample": True},
                    {"lat": 30.001, "lng": 120.002, "sample": False},
                ],
                "replace": True,
            },
            rospy_module=FakeRospy,
            waypoint_class=Waypoint,
            waypoint_list_class=WaypointList,
            service_classes={
                "push": object,
                "pull": object,
                "clear": object,
            },
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["verified"])
        self.assertEqual(state.cleared, 1)
        self.assertEqual(state.pulled, 1)
        self.assertEqual(len(state.pushed[0]), 4)
        self.assertEqual([wp.command for wp in state.pushed[0]], [16, 16, 42702, 16])
        service_names = [name for _, name in state.service_names]
        self.assertNotIn("/mavros/set_mode", service_names)
        self.assertNotIn("/mavros/cmd/arming", service_names)

    def test_upload_returns_json_error_when_mavros_service_is_unavailable(self):
        module = _load_module()

        class Waypoint:
            pass

        class WaypointList:
            pass

        class FakeRospy:
            @staticmethod
            def wait_for_service(name, timeout=None):
                raise RuntimeError("service unavailable")

        result = module.upload_mission_plan(
            {"waypoints": [{"lat": 30.0, "lng": 120.0, "sample": True}]},
            rospy_module=FakeRospy,
            waypoint_class=Waypoint,
            waypoint_list_class=WaypointList,
            service_classes={
                "push": object,
                "pull": object,
                "clear": object,
            },
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["data"]["valid"])
        self.assertIn("mavros mission upload error", result["message"])
        self.assertIn("service unavailable", result["message"])


if __name__ == "__main__":
    unittest.main()
