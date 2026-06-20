import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class RecordingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


def _install_fake_ros_modules():
    class String:
        def __init__(self, data=""):
            self.data = data

    class Float32MultiArray:
        def __init__(self):
            self.data = []

    class TwistStamped:
        pass

    class Imu:
        pass

    class NavSatFix:
        def __init__(self, latitude=0.0, longitude=0.0, altitude=0.0):
            self.latitude = latitude
            self.longitude = longitude
            self.altitude = altitude

    class State:
        connected = False
        mode = ""
        armed = False

    class WaypointReached:
        wp_seq = 0

    class Mavlink:
        pass

    class SetModeRequest:
        def __init__(self):
            self.custom_mode = ""

    rospy = types.ModuleType("rospy")
    rospy.Publisher = lambda *args, **kwargs: RecordingPublisher()
    rospy.Subscriber = lambda *args, **kwargs: None
    rospy.ServiceProxy = lambda *args, **kwargs: None
    rospy.Service = lambda *args, **kwargs: None
    rospy.init_node = lambda *args, **kwargs: None
    rospy.get_param = lambda name, default=None: default
    rospy.wait_for_service = lambda *args, **kwargs: None
    rospy.loginfo = lambda *args, **kwargs: None
    rospy.logwarn = lambda *args, **kwargs: None
    rospy.logwarn_throttle = lambda *args, **kwargs: None
    rospy.logerr = lambda *args, **kwargs: None
    rospy.logdebug_throttle = lambda *args, **kwargs: None
    rospy.sleep = lambda *args, **kwargs: None
    rospy.is_shutdown = lambda: True
    rospy.Rate = lambda hz: types.SimpleNamespace(sleep=lambda: None)
    rospy.Time = types.SimpleNamespace(now=lambda: 0)
    rospy.ROSException = type("ROSException", (Exception,), {})
    rospy.ROSInterruptException = type("ROSInterruptException", (Exception,), {})
    rospy.ServiceException = type("ServiceException", (Exception,), {})

    std_msgs = types.ModuleType("std_msgs")
    std_msgs_msg = types.ModuleType("std_msgs.msg")
    std_msgs_msg.String = String
    std_msgs_msg.Float32MultiArray = Float32MultiArray
    std_msgs.msg = std_msgs_msg

    geometry_msgs = types.ModuleType("geometry_msgs")
    geometry_msgs_msg = types.ModuleType("geometry_msgs.msg")
    geometry_msgs_msg.TwistStamped = TwistStamped
    geometry_msgs.msg = geometry_msgs_msg

    sensor_msgs = types.ModuleType("sensor_msgs")
    sensor_msgs_msg = types.ModuleType("sensor_msgs.msg")
    sensor_msgs_msg.Imu = Imu
    sensor_msgs_msg.NavSatFix = NavSatFix
    sensor_msgs.msg = sensor_msgs_msg

    std_srvs = types.ModuleType("std_srvs")
    std_srvs_srv = types.ModuleType("std_srvs.srv")
    std_srvs_srv.Trigger = object
    std_srvs_srv.TriggerResponse = object
    std_srvs.srv = std_srvs_srv

    mavros_msgs = types.ModuleType("mavros_msgs")
    mavros_msgs_msg = types.ModuleType("mavros_msgs.msg")
    mavros_msgs_msg.State = State
    mavros_msgs_msg.WaypointReached = WaypointReached
    mavros_msgs_msg.Mavlink = Mavlink
    mavros_msgs.msg = mavros_msgs_msg
    mavros_msgs_srv = types.ModuleType("mavros_msgs.srv")
    mavros_msgs_srv.SetMode = object
    mavros_msgs_srv.SetModeRequest = SetModeRequest
    mavros_msgs.srv = mavros_msgs_srv

    sys.modules["rospy"] = rospy
    sys.modules["std_msgs"] = std_msgs
    sys.modules["std_msgs.msg"] = std_msgs_msg
    sys.modules["geometry_msgs"] = geometry_msgs
    sys.modules["geometry_msgs.msg"] = geometry_msgs_msg
    sys.modules["sensor_msgs"] = sensor_msgs
    sys.modules["sensor_msgs.msg"] = sensor_msgs_msg
    sys.modules["std_srvs"] = std_srvs
    sys.modules["std_srvs.srv"] = std_srvs_srv
    sys.modules["mavros_msgs"] = mavros_msgs
    sys.modules["mavros_msgs.msg"] = mavros_msgs_msg
    sys.modules["mavros_msgs.srv"] = mavros_msgs_srv


def _load_script(module_name, relative_path):
    _install_fake_ros_modules()
    script_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LabSimSamplingStateTests(unittest.TestCase):
    def test_simulated_lab_sampling_dwell_publishes_started_before_stopped(self):
        module = _load_script("mavlink_trigger_node_lab_dwell_test", "scripts/mavlink_trigger_node.py")
        sleeps = []
        module.rospy.sleep = lambda seconds: sleeps.append(seconds)
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_sampling_context = None
        node.status_pub = RecordingPublisher()
        node.mission_status_pub = RecordingPublisher()
        node.spectrometer_voltage_pub = RecordingPublisher()
        node.sample_event_pub = RecordingPublisher()
        node.lab_command_pub = RecordingPublisher()

        node._run_lab_sampling(3, 25.0, 110.0, {"sim": {"sample_dwell_s": 1.25}})

        statuses = [msg.data for msg in node.status_pub.messages]
        self.assertEqual(sleeps, [1.25])
        self.assertEqual(
            [status for status in statuses if status in ("sampling_started", "sampling_stopped")],
            ["sampling_started", "sampling_stopped"],
        )

    def test_simulated_lab_sampling_value_uses_configured_pollution_range(self):
        module = _load_script("mavlink_trigger_node_lab_value_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_sampling_context = None
        node.status_pub = RecordingPublisher()
        node.mission_status_pub = RecordingPublisher()
        node.spectrometer_voltage_pub = RecordingPublisher()
        node.sample_event_pub = RecordingPublisher()
        node.lab_command_pub = RecordingPublisher()

        node._run_lab_sampling(4, 25.0, 110.0, {
            "sim": {"sample_dwell_s": 0.0},
            "mission": {"center": {"lat": 25.0, "lng": 110.0}},
            "pollution": {
                "mode": "center",
                "radius_m": 100.0,
                "strength": 0.5,
                "value_min": 10.0,
                "value_max": 20.0,
                "reference_voltage": 3.0,
            },
        })

        payload = json.loads(node.spectrometer_voltage_pub.messages[-1].data)
        self.assertGreaterEqual(payload["value"], 10.0)
        self.assertLessEqual(payload["value"], 20.0)
        self.assertAlmostEqual(payload["value"], 15.0, places=3)
        self.assertTrue(payload["valid"])

    def test_simulated_lab_sampling_publishes_bounded_event_and_one_aggregate_voltage(self):
        module = _load_script("mavlink_trigger_node_lab_event_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_sampling_context = None
        node.status_pub = RecordingPublisher()
        node.mission_status_pub = RecordingPublisher()
        node.spectrometer_voltage_pub = RecordingPublisher()
        node.sample_event_pub = RecordingPublisher()
        node.lab_command_pub = RecordingPublisher()

        node._run_lab_sampling(5, 25.314167, 110.412778, {
            "droplet_count": 12,
            "sim": {"sample_dwell_s": 0.0},
            "mission": {
                "waypoints": [
                    {"wgs84": {"lat": 25.314167, "lng": 110.412778}, "gcj02": {"lat": 25.315, "lng": 110.414}}
                ],
                "center": {"wgs84": {"lat": 25.314167, "lng": 110.412778}, "gcj02": {"lat": 25.315, "lng": 110.414}},
            },
            "pollution": {
                "mode": "center",
                "value_max": 20.0,
                "source": {"wgs84": {"lat": 25.314167, "lng": 110.412778}, "gcj02": {"lat": 25.315, "lng": 110.414}},
            },
        })

        event_payload = json.loads(node.sample_event_pub.messages[-1].data)
        aggregate_payload = json.loads(node.spectrometer_voltage_pub.messages[-1].data)
        self.assertEqual(len(node.sample_event_pub.messages), 1)
        self.assertEqual(len(node.spectrometer_voltage_pub.messages), 1)
        self.assertEqual(len(event_payload["droplets"]), 12)
        self.assertEqual(event_payload["config_snapshot"]["droplet_count"], 12)
        self.assertEqual(aggregate_payload["sample_event_id"], event_payload["event_id"])
        self.assertEqual(aggregate_payload["droplet_count"], 12)
        self.assertNotIn("droplets", aggregate_payload)

    def test_simulated_survey_window_publishes_bounded_event_without_automation(self):
        module = _load_script("mavlink_trigger_node_survey_event_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.current_waypoint = 0
        node.is_sampling = False
        node._survey_active = True
        node._survey_sample_active = False
        node._last_survey_sample_position = None
        node.latest_spectrometer_payload = {"valid": True}
        node.last_linear_speed = 1.0
        node._latest_global_position = {"lat": 25.314167, "lon": 110.412778, "received_at": 0.0}
        node.status_pub = RecordingPublisher()
        node.mission_status_pub = RecordingPublisher()
        node.spectrometer_voltage_pub = RecordingPublisher()
        node.sample_event_pub = RecordingPublisher()
        node.steps_pub = RecordingPublisher()
        node._load_config = lambda: {
            "lab_mode": {
                "enabled": True,
                "data_source": "simulated",
                "droplet_count": 12,
                "sim": {"sample_dwell_s": 0.0},
                "pollution": {"value_max": 2.0},
            },
            "survey_sampling": {"survey_require_gps": True, "survey_max_position_age_s": 0.0},
        }

        result = node._start_survey_sample_once(node._load_config())

        event_payload = json.loads(node.sample_event_pub.messages[-1].data)
        aggregate_payload = json.loads(node.spectrometer_voltage_pub.messages[-1].data)
        self.assertEqual(result, "started")
        self.assertEqual(len(node.steps_pub.messages), 0)
        self.assertEqual(len(event_payload["droplets"]), 12)
        self.assertEqual(event_payload["mode"], "survey")
        self.assertEqual(aggregate_payload["sample_event_mode"], "survey")
        self.assertEqual(node._last_survey_sample_position["lat"], 25.314167)

    def test_lab_sim_completion_status_is_latched_once_after_sampling_done(self):
        module = _load_script("lab_sim_node_completed_once_test", "scripts/lab_sim_node.py")
        sim = module.LabSimulator({"start_lat": 25.0, "start_lng": 110.0, "arrival_radius_m": 3.0})
        sim.start_mission([{"seq": 1, "lat": 25.0, "lng": 110.0}])
        sim.step(0.0)

        snapshot = sim.snapshot()
        self.assertFalse(snapshot["mission"]["active"])
        self.assertFalse(snapshot["mission"]["completed"])
        self.assertTrue(snapshot["mission"]["waiting_sampling_done"])
        sim.complete_mission()
        completed_snapshot = sim.snapshot()
        self.assertFalse(completed_snapshot["running"])
        self.assertTrue(completed_snapshot["mission"]["completed"])

        node = module.LabSimNode.__new__(module.LabSimNode)
        node.sim = sim
        node._status_pub = RecordingPublisher()
        node._completed_status_emitted = False
        node._publish_status()
        node._publish_status()

        completed_frames = [
            json.loads(msg.data) for msg in node._status_pub.messages
            if json.loads(msg.data)["mission"]["completed"]
        ]
        self.assertEqual(len(completed_frames), 1)
        self.assertEqual(completed_frames[0]["mission"]["reached_count"], 1)


if __name__ == "__main__":
    unittest.main()
