import importlib.util
import json
import os
import sys
import threading
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _workspace_root():
    candidates = []
    env_root = os.environ.get("USV_WORKSPACE_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.extend([
        REPO_ROOT.parents[1],
        REPO_ROOT.parent,
        Path("D:/usv_ws"),
    ])
    for candidate in candidates:
        if (candidate / "ardupilot-usv" / "Rover" / "mode_auto.cpp").exists():
            return candidate
    return REPO_ROOT.parents[1]


class RecordingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


def _install_fake_ros_modules():
    class String:
        def __init__(self):
            self.data = ""

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
        def __init__(self):
            self.header = types.SimpleNamespace(stamp=None)
            self.payload64 = []

    class TriggerResponse:
        def __init__(self, success=False, message=""):
            self.success = success
            self.message = message

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
    std_srvs_srv.TriggerResponse = TriggerResponse
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

    pymavlink = types.ModuleType("pymavlink")
    pymavlink.mavutil = types.SimpleNamespace(
        mavlink=types.SimpleNamespace(
            MAV_SEVERITY_NOTICE=5,
            MAV_TYPE_ONBOARD_CONTROLLER=18,
            MAV_AUTOPILOT_INVALID=8,
            MAV_STATE_ACTIVE=4,
        ),
        mavlink_connection=lambda *args, **kwargs: None,
    )

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
    sys.modules["pymavlink"] = pymavlink
    sys.modules["pymavlink.mavutil"] = pymavlink.mavutil


def _load_script(module_name, relative_path):
    _install_fake_ros_modules()
    script_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCommandLong:
    def __init__(self, command, param1, param2, target_system, target_component, sender_system, sender_component):
        self.command = command
        self.param1 = param1
        self.param2 = param2
        self.target_system = target_system
        self.target_component = target_component
        self._sender_system = sender_system
        self._sender_component = sender_component

    def get_type(self):
        return "COMMAND_LONG"

    def get_srcSystem(self):
        return self._sender_system

    def get_srcComponent(self):
        return self._sender_component


class FakeConnection:
    def __init__(self, messages):
        self._messages = list(messages)

    def recv_match(self, blocking=False):
        if self._messages:
            return self._messages.pop(0)
        return None


class RecordingMav:
    def __init__(self):
        self.named_values = []
        self.command_acks = []

    def named_value_float_send(self, timestamp, name, value):
        clean_name = name.decode("ascii").rstrip("\x00")
        self.named_values.append((clean_name, value))

    def command_ack_send(self, command, result, progress, result_param2, target_sys, target_comp):
        self.command_acks.append((command, result, progress, result_param2, target_sys, target_comp))


class FailingOnceNamedValueMav(RecordingMav):
    def __init__(self):
        super().__init__()
        self.fail_next_named_value = True

    def named_value_float_send(self, timestamp, name, value):
        if self.fail_next_named_value:
            self.fail_next_named_value = False
            raise OSError("router socket closed")
        super().named_value_float_send(timestamp, name, value)


class MavlinkCommandCompatibilityTests(unittest.TestCase):
    def _install_injection_session_recorder(self, node, accepted=True):
        calls = []

        def start(source, config=None):
            calls.append(("on", source))
            return accepted

        def stop(source, reason=""):
            calls.append(("off", source, reason))
            return True

        node._start_injection_session = start
        node._stop_injection_session = stop
        return calls

    def test_trigger_node_constructs_without_auto_trigger_attr_crash(self):
        module = _load_script("mavlink_trigger_node_construct_test", "scripts/mavlink_trigger_node.py")

        node = module.MAVLinkTriggerNode()

        self.assertFalse(node.auto_trigger_on_waypoint)
        self.assertEqual(node._source_system_id, 1)
        self.assertEqual(node._source_component_id, 191)

    def test_bringup_launch_defaults_mavlink_source_system_to_fcu_system(self):
        launch_text = (REPO_ROOT / "launch" / "usv_bringup.launch").read_text(encoding="utf-8")

        self.assertIn('<arg name="mavlink_source_system" default="1" />', launch_text)

    def test_router_bridge_forwards_usv_command_long_to_internal_bus(self):
        module = _load_script("usv_mavlink_router_bridge_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._conn = FakeConnection([
            FakeCommandLong(31010, 1.5, 2.5, 1, 191, 255, 190),
        ])
        bridge._cmd_rx_pub = RecordingPublisher()

        bridge._receive_mavlink_messages()

        self.assertEqual(len(bridge._cmd_rx_pub.messages), 1)
        self.assertEqual(
            bridge._cmd_rx_pub.messages[0].data,
            [31010.0, 1.5, 2.5, 1.0, 191.0, 255.0, 190.0],
        )

    def test_router_bridge_sends_command_ack_immediately_on_ack_request(self):
        module = _load_script("usv_mavlink_router_bridge_ack_fast_path_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._diag_tx_total = 0
        bridge._diag_pub_errors = 0
        bridge._pending_acks = []
        mav = RecordingMav()
        bridge._conn = types.SimpleNamespace(mav=mav)

        msg = sys.modules["std_msgs.msg"].Float32MultiArray()
        msg.data = [31018.0, 0.0, 255.0, 190.0]

        bridge._cmd_ack_cb(msg)

        self.assertEqual(mav.command_acks, [(31018, 0, 0xFF, 0, 255, 190)])
        self.assertEqual(bridge._pending_acks, [])

    def test_trigger_node_accepts_forwarded_internal_command_bus(self):
        module = _load_script("mavlink_trigger_node_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node._source_system_id = 1
        node._source_component_id = 191
        node.is_sampling = False

        handled = []
        acknowledgements = []

        def fake_handle(command, param1, param2):
            handled.append((command, param1, param2))
            return True

        def fake_ack(command, result, target_system, target_component):
            acknowledgements.append((command, result, target_system, target_component))

        node.handle_mavlink_command = fake_handle
        node._send_command_ack = fake_ack

        msg = sys.modules["std_msgs.msg"].Float32MultiArray()
        msg.data = [31010.0, 0.0, 0.0, 1.0, 191.0, 255.0, 190.0]

        node._mavlink_cmd_rx_cb(msg)

        self.assertEqual(handled, [(31010, 0.0, 0.0)])
        self.assertEqual(
            acknowledgements,
            [(31010, module.MAV_RESULT_ACCEPTED, 255, 190)],
        )

    def test_trigger_node_dispatches_spectrometer_commands(self):
        module = _load_script("mavlink_trigger_node_spectro_dispatch_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False

        handled = []
        acknowledgements = []

        def fake_handle(command, param1, param2):
            handled.append((command, param1, param2))
            return True

        node.handle_mavlink_command = fake_handle
        node._send_command_ack = lambda command, result, target_system, target_component: acknowledgements.append(
            (command, result, target_system, target_component)
        )

        node._dispatch_command_long(31018, 0.0, 0.0, 255, 190, "test")
        node._dispatch_command_long(31019, 0.0, 0.0, 255, 190, "test")

        self.assertEqual(handled, [(31018, 0.0, 0.0), (31019, 0.0, 0.0)])
        self.assertEqual(
            acknowledgements,
            [
                (31018, module.MAV_RESULT_ACCEPTED, 255, 190),
                (31019, module.MAV_RESULT_ACCEPTED, 255, 190),
            ],
        )

    def test_trigger_node_spectrometer_start_and_stop_call_pump_services(self):
        module = _load_script("mavlink_trigger_node_spectro_services_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        calls = []
        node.spectrometer_command_pub = RecordingPublisher()
        node._call_spectrometer_service = lambda action: calls.append(action) or True

        self.assertTrue(node.handle_mavlink_command(31018, 0.0, 0.0))
        self.assertTrue(node.handle_mavlink_command(31019, 0.0, 0.0))

        self.assertEqual(calls, ["start", "stop"])
        self.assertEqual(node.spectrometer_command_pub.messages, [])

    def test_trigger_node_spectrometer_service_failure_rejects_command(self):
        module = _load_script("mavlink_trigger_node_spectro_service_failure_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node._call_spectrometer_service = lambda action: False

        self.assertFalse(node.handle_mavlink_command(31018, 0.0, 0.0))

    def test_manual_sample_start_rejection_does_not_publish_failed_mission_state(self):
        module = _load_script("mavlink_trigger_node_manual_reject_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 0
        node.steps_pub = RecordingPublisher()
        states = []

        node._load_config = lambda: {"steps": []}
        node._get_default_config = lambda: {"steps": []}
        node._build_steps_payload = lambda config, waypoint: {"steps": []}
        node._call_automation_service = lambda name: False
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        self._install_injection_session_recorder(node)

        accepted = node._do_manual_sample()

        self.assertFalse(accepted)
        self.assertFalse(node.is_sampling)
        self.assertNotIn((module.MissionState.FAILED, "manual_start_failed"), states)
        self.assertEqual(states[-1], (module.MissionState.IDLE, "manual_start_rejected"))

    def test_manual_sample_publishes_sampling_started_after_automation_start_accepts(self):
        module = _load_script("mavlink_trigger_node_manual_started_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 0
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node._load_config = lambda: {"steps": []}
        node._get_default_config = lambda: {"steps": []}
        node._build_steps_payload = lambda config, waypoint: {"steps": []}
        node._set_mission_state = lambda state, context=None: None
        calls = self._install_injection_session_recorder(node)
        node._call_automation_service = lambda name: calls.append(("automation", name)) or True

        accepted = node._do_manual_sample()

        self.assertTrue(accepted)
        self.assertEqual(calls[:2], [("on", "manual"), ("automation", "start")])
        self.assertEqual([msg.data for msg in node.status_pub.messages], ["sampling_started"])

    def test_manual_sample_rejects_when_injection_start_fails_before_steps_or_automation(self):
        module = _load_script("mavlink_trigger_node_manual_injection_reject_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 0
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        automation_calls = []
        node._load_config = lambda: {"sampling_sequence": {"steps": [{"name": "sample"}]}}
        node._get_default_config = lambda: {"sampling_sequence": {"steps": []}}
        node._build_manual_steps_payload = lambda config: {"steps": [{"name": "sample"}]}
        node._set_mission_state = lambda state, context=None: None
        node._call_automation_service = lambda name: automation_calls.append(name) or True
        self._install_injection_session_recorder(node, accepted=False)

        accepted = node._do_manual_sample()

        self.assertFalse(accepted)
        self.assertFalse(node.is_sampling)
        self.assertEqual(node.steps_pub.messages, [])
        self.assertEqual(automation_calls, [])

    def test_manual_sample_uses_global_loop_count_not_waypoint_override(self):
        module = _load_script("mavlink_trigger_node_manual_loop_count_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 3
        node.default_retry_count = 0
        node.default_on_fail = "HOLD"
        node.hold_settle_time = 3.0
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node._load_config = lambda: {
            "sampling_sequence": {"steps": [{"name": "sample"}], "loop_count": 5},
            "pump_settings": {"pid_mode": True, "pid_precision": 0.1},
            "waypoint_sampling": {"3": {"enabled": True, "loop_count": 1}},
        }
        node._get_default_config = lambda: {"sampling_sequence": {"steps": [], "loop_count": 1}}
        node._set_mission_state = lambda state, context=None: None
        node._call_automation_service = lambda name: True
        self._install_injection_session_recorder(node)

        accepted = node._do_manual_sample()

        self.assertTrue(accepted)
        payload = json.loads(node.steps_pub.messages[-1].data)
        self.assertEqual(payload["loop_count"], 5)

    def test_manual_sample_lab_mode_disables_pid_wait_and_marks_position_source(self):
        module = _load_script("mavlink_trigger_node_manual_lab_mode_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 0
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node._load_config = lambda: {
            "sampling_sequence": {"steps": [{"name": "lab sample"}], "loop_count": 1},
            "pump_settings": {"pid_mode": True, "pid_precision": 0.1},
            "lab_mode": {
                "enabled": True,
                "bypass_pid_wait": True,
                "position_source": "lab_sim",
            },
        }
        node._get_default_config = lambda: {"sampling_sequence": {"steps": [], "loop_count": 1}}
        node._set_mission_state = lambda state, context=None: None
        node._call_automation_service = lambda name: True
        self._install_injection_session_recorder(node)

        accepted = node._do_manual_sample()

        self.assertTrue(accepted)
        payload = json.loads(node.steps_pub.messages[-1].data)
        self.assertFalse(payload["pid_mode"])
        self.assertTrue(payload["lab_mode"])
        self.assertEqual(payload["position_source"], "lab_sim")
        self.assertTrue(payload["lab_options"]["bypass_pid_wait"])

    def test_manual_sample_completion_returns_idle_without_auto_resume(self):
        module = _load_script("mavlink_trigger_node_manual_completion_idle_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node.is_sampling = False
        node.current_waypoint = 0
        node.default_retry_count = 0
        node.default_on_fail = "HOLD"
        node.hold_settle_time = 3.0
        node.steps_pub = RecordingPublisher()
        states = []
        statuses = []
        resumed = []
        node._load_config = lambda: {
            "sampling_sequence": {"steps": [{"name": "sample"}], "loop_count": 2},
            "pump_settings": {"pid_mode": True, "pid_precision": 0.1},
            "waypoint_sampling": {},
        }
        node._get_default_config = lambda: {"sampling_sequence": {"steps": [], "loop_count": 1}}
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._set_waypoint_state = lambda *args: None
        node._publish_status = lambda status: statuses.append(status)
        node._call_automation_service = lambda name: True
        node._resume_auto_if_mission_exists = lambda: resumed.append(True)
        injection_calls = self._install_injection_session_recorder(node)

        accepted = node._do_manual_sample()
        node._handle_completion(success=True, reason="finished")

        self.assertTrue(accepted)
        self.assertFalse(node.is_sampling)
        self.assertEqual(states[-1], (module.MissionState.IDLE, "manual_finished"))
        self.assertIn("sampling_stopped", statuses)
        self.assertIn(("off", "manual", "finished"), injection_calls)
        self.assertEqual(resumed, [])

    def test_manual_stop_returns_idle_without_auto_resume(self):
        module = _load_script("mavlink_trigger_node_manual_stop_idle_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = True
        node.current_waypoint = 0
        node.current_sampling_context = {"source": "manual"}
        states = []
        statuses = []
        resumed = []
        node._call_automation_service = lambda name: True
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._publish_status = lambda status: statuses.append(status)
        node._resume_auto_if_mission_exists = lambda: resumed.append(True)
        injection_calls = self._install_injection_session_recorder(node)

        node._stop_sampling_sequence()

        self.assertFalse(node.is_sampling)
        self.assertEqual(states[-1], (module.MissionState.IDLE, "manual_stopped"))
        self.assertEqual(statuses, ["sampling_stopped"])
        self.assertIn(("off", "manual", "manual_stop"), injection_calls)
        self.assertEqual(resumed, [])

    def test_fcu_sample_publishes_sampling_started_after_automation_start_accepts(self):
        module = _load_script("mavlink_trigger_node_fcu_started_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 4
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node._load_config = lambda: {"steps": []}
        node._get_default_config = lambda: {"steps": []}
        node._build_steps_payload = lambda config, waypoint: {"steps": []}
        node._set_mission_state = lambda state, context=None: None
        calls = self._install_injection_session_recorder(node)
        node._call_automation_service = lambda name: calls.append(("automation", name)) or True

        accepted = node._do_fcu_sample(42)

        self.assertTrue(accepted)
        self.assertEqual(calls[:2], [("on", "fcu"), ("automation", "start")])
        self.assertEqual([msg.data for msg in node.status_pub.messages], ["sampling_started"])

    def test_nav_sampling_sequence_publishes_sampling_started_after_automation_start_accepts(self):
        module = _load_script("mavlink_trigger_node_nav_started_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.is_sampling = False
        node.current_waypoint = 7
        node.default_on_fail = "HOLD"
        node.current_sampling_context = None
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node._load_config = lambda: {"steps": []}
        node._get_default_config = lambda: {"steps": []}
        node._get_waypoint_sampling_config = lambda config, waypoint: {
            "enabled": True,
            "retry_count": 0,
            "on_fail": "HOLD",
            "hold_before_sampling_s": 0,
        }
        node._build_steps_payload = lambda config, waypoint: {"steps": []}
        node._set_waypoint_state = lambda state, context=None: None
        node._set_mission_state = lambda state, context=None: None
        node.set_mode = lambda mode: True
        node._wait_until_stable = lambda timeout: (True, "stable")
        calls = self._install_injection_session_recorder(node)
        node._call_automation_service = lambda name: calls.append(("automation", name)) or True
        node._handle_failure_action = lambda reason: None

        accepted = node._start_sampling_sequence(7)

        self.assertTrue(accepted)
        self.assertEqual(calls[:2], [("on", "waypoint"), ("automation", "start")])
        self.assertEqual([msg.data for msg in node.status_pub.messages], ["sampling_started"])

    def test_router_bridge_updates_automation_named_values_from_structured_status(self):
        module = _load_script("usv_mavlink_router_bridge_automation_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._automation_step = 0.0
        bridge._automation_total = 0.0
        bridge._pid_mode = 0.0
        bridge._status_code = 0

        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({
            "status": "running",
            "running": True,
            "paused": False,
            "automation_step": 2,
            "automation_total": 5,
            "current_loop": 1,
            "total_loops": 3,
            "pid_mode": "running",
        })

        bridge._automation_status_cb(msg)

        self.assertEqual(bridge._automation_step, 2.0)
        self.assertEqual(bridge._automation_total, 5.0)
        self.assertEqual(bridge._pid_mode, 1.0)
        self.assertEqual(bridge._status_code, module.USVMavlinkRouterBridge._MISSION_STATE_CODES["SAMPLING"])

    def test_router_bridge_clears_sampling_status_when_web_started_automation_finishes(self):
        module = _load_script("usv_mavlink_router_bridge_web_finish_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._automation_step = 1.0
        bridge._automation_total = 1.0
        bridge._pid_mode = 1.0
        bridge._automation_running = True
        bridge._last_mission_state = "SAMPLING"
        bridge._status_code = module.USVMavlinkRouterBridge._MISSION_STATE_CODES["SAMPLING"]
        bridge._fcu_sample_id = 0

        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({
            "status": "finished",
            "running": False,
            "paused": False,
            "automation_step": 1,
            "automation_total": 1,
            "pid_mode": "done",
        })

        bridge._automation_status_cb(msg)

        self.assertEqual(bridge._status_code, module.USVMavlinkRouterBridge._MISSION_STATE_CODES["IDLE"])

    def test_router_bridge_treats_finished_status_as_terminal_even_if_running_flag_is_stale(self):
        module = _load_script("usv_mavlink_router_bridge_stale_running_finish_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._automation_step = 1.0
        bridge._automation_total = 1.0
        bridge._pid_mode = 1.0
        bridge._automation_running = True
        bridge._last_mission_state = "SAMPLING"
        bridge._status_code = module.USVMavlinkRouterBridge._MISSION_STATE_CODES["SAMPLING"]
        bridge._fcu_sample_id = 0

        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({
            "status": "finished",
            "running": True,
            "paused": False,
            "automation_step": 1,
            "automation_total": 1,
            "pid_mode": "done",
        })

        bridge._automation_status_cb(msg)

        self.assertFalse(bridge._automation_running)
        self.assertEqual(bridge._status_code, module.USVMavlinkRouterBridge._MISSION_STATE_CODES["IDLE"])

    def test_router_bridge_keeps_sampling_status_for_pending_fcu_done(self):
        module = _load_script("usv_mavlink_router_bridge_fcu_done_guard_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._automation_step = 1.0
        bridge._automation_total = 1.0
        bridge._pid_mode = 1.0
        bridge._automation_running = True
        bridge._last_mission_state = "SAMPLING"
        bridge._status_code = module.USVMavlinkRouterBridge._MISSION_STATE_CODES["SAMPLING"]
        bridge._fcu_sample_id = 42

        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({
            "status": "finished",
            "running": False,
            "paused": False,
            "automation_step": 1,
            "automation_total": 1,
            "pid_mode": "done",
        })

        bridge._automation_status_cb(msg)

        self.assertEqual(bridge._status_code, module.USVMavlinkRouterBridge._MISSION_STATE_CODES["SAMPLING"])

    def test_router_bridge_sends_baseline_named_values_with_payload(self):
        module = _load_script("usv_mavlink_router_bridge_baseline_payload_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._boot_time = 0
        bridge._pkt_count = 10
        bridge._diag_tx_total = 0
        bridge._diag_tx_named = 0
        bridge._diag_pub_errors = 0
        bridge._lock = threading.Lock()
        mav = RecordingMav()
        bridge._conn = types.SimpleNamespace(mav=mav)

        bridge._send_payload(
            voltage=1.23,
            absorbance=0.12,
            angles={"X": 1.0, "Y": 2.0, "Z": 3.0, "A": 4.0},
            status=14,
            automation_step=2,
            automation_total=5,
            sample_count=7,
            pid_error=0.01,
            pid_mode=1,
            baseline_set=1.0,
            reference_voltage=1.23,
            baseline_voltage=0.0,
            spectrometer_valid=1.0,
        )

        names = [name for name, _ in mav.named_values]
        self.assertIn("USV_BSET", names)
        self.assertIn("USV_REF", names)
        self.assertIn("USV_BASE", names)
        self.assertIn("USV_VLD", names)
        self.assertIn("USV_JTMP", names)
        self.assertIn("USV_ETMP", names)
        self.assertIn("USV_JCPU", names)
        self.assertIn("USV_JMEM", names)
        self.assertIn("USV_EHEAP", names)
        self.assertEqual(len(mav.named_values), 22)
        self.assertEqual(bridge._diag_tx_named, 22)

    def test_rover_nav_script_time_is_the_only_mission_sampling_trigger(self):
        workspace_root = _workspace_root()
        mode_auto = (workspace_root / "ardupilot-usv" / "Rover" / "mode_auto.cpp").read_text(encoding="utf-8")

        self.assertNotIn("case 31010:", mode_auto)
        self.assertIn("nav_scripting.command == 1", mode_auto)
        self.assertIn('gcs().send_named_float("USV_SMPL"', mode_auto)
    def test_router_bridge_reconnects_and_keeps_payload_alive_after_socket_write_failure(self):
        module = _load_script("usv_mavlink_router_bridge_reconnect_payload_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._boot_time = 0
        bridge._pkt_count = 10
        bridge._diag_tx_total = 0
        bridge._diag_tx_named = 0
        bridge._diag_pub_errors = 0
        bridge._lock = threading.Lock()
        first_mav = FailingOnceNamedValueMav()
        recovered_mav = RecordingMav()
        bridge._conn = types.SimpleNamespace(mav=first_mav)
        reconnects = []

        def reconnect_router():
            reconnects.append(True)
            bridge._conn = types.SimpleNamespace(mav=recovered_mav)
            return True

        bridge._reconnect_router = reconnect_router

        bridge._send_payload(
            voltage=1.23,
            absorbance=0.12,
            angles={"X": 1.0, "Y": 2.0, "Z": 3.0, "A": 4.0},
            status=14,
            automation_step=2,
            automation_total=5,
            sample_count=7,
            pid_error=0.01,
            pid_mode=1,
            baseline_set=1.0,
            reference_voltage=1.23,
            baseline_voltage=0.0,
            spectrometer_valid=1.0,
        )

        self.assertEqual(reconnects, [True])
        self.assertEqual(len(recovered_mav.named_values), 22)
        self.assertEqual(recovered_mav.named_values[0][0], "USV_VOLT")
        self.assertEqual(bridge._diag_tx_named, 22)

    def test_router_bridge_caches_spectrometer_valid_from_voltage_payload(self):
        module = _load_script("usv_mavlink_router_bridge_valid_payload_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._voltage = 0.0
        bridge._absorbance = 0.0
        bridge._baseline_set = 0.0
        bridge._reference_voltage = 0.0
        bridge._baseline_voltage = 0.0
        bridge._spectrometer_valid = 0.0

        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({"voltage": 1.23, "valid": True})

        bridge._voltage_cb(msg)

        self.assertEqual(bridge._spectrometer_valid, 1.0)

    def test_router_bridge_adds_system_health_named_value_fields(self):
        module = _load_script("usv_mavlink_router_bridge_health_payload_test", "scripts/usv_mavlink_router_bridge.py")
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        bridge._lock = threading.Lock()
        bridge._boot_time = 0.0
        bridge._pkt_count = 7
        bridge._diag_tx_total = 0
        bridge._diag_tx_named = 0
        bridge._diag_pub_errors = 0
        bridge._health_fields = module.DEFAULT_HEALTH_FIELDS.copy()
        mav = RecordingMav()
        bridge._conn = types.SimpleNamespace(mav=mav)
        msg = sys.modules["std_msgs.msg"].String()
        msg.data = json.dumps({
            "jetson": {"temperature_c": 55.2, "cpu_percent": 12.5, "memory_percent": 34.0},
            "detector": {"temperature_c": 43.2, "heap_percent_free": 37.5},
        })

        bridge._system_health_cb(msg)
        bridge._send_payload(
            voltage=1.2,
            absorbance=0.3,
            angles={"X": 1.0, "Y": 2.0, "Z": 3.0, "A": 4.0},
            status=1,
            automation_step=2.0,
            automation_total=3.0,
            sample_count=4.0,
            pid_error=0.5,
            pid_mode=1.0,
            baseline_set=1.0,
            reference_voltage=1.1,
            baseline_voltage=1.0,
            spectrometer_valid=1.0,
        )

        sent = dict(mav.named_values)
        self.assertEqual(len(mav.named_values), 22)
        self.assertAlmostEqual(sent["USV_JTMP"], 55.2)
        self.assertAlmostEqual(sent["USV_ETMP"], 43.2)
        self.assertAlmostEqual(sent["USV_JCPU"], 12.5)
        self.assertAlmostEqual(sent["USV_JMEM"], 34.0)
        self.assertAlmostEqual(sent["USV_EHEAP"], 37.5)
        self.assertEqual(bridge._diag_tx_named, 22)

    def test_trigger_node_set_baseline_uses_latest_valid_voltage(self):
        module = _load_script("mavlink_trigger_node_baseline_valid_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.spectrometer_command_pub = RecordingPublisher()
        node.latest_spectrometer_payload = {"valid": True, "voltage": 1.234}

        accepted = node.handle_mavlink_command(31017, 0.0, 0.0)

        self.assertTrue(accepted)
        payload = json.loads(node.spectrometer_command_pub.messages[-1].data)
        self.assertEqual(payload, {"cmd": "set_baseline", "reference_voltage": 1.234})

    def test_trigger_node_set_baseline_rejects_missing_valid_voltage(self):
        module = _load_script("mavlink_trigger_node_baseline_missing_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.spectrometer_command_pub = RecordingPublisher()
        node.latest_spectrometer_payload = {"valid": False, "voltage": 0.0}

        accepted = node.handle_mavlink_command(31017, 0.0, 0.0)

        self.assertFalse(accepted)
        self.assertEqual(node.spectrometer_command_pub.messages, [])

    def test_survey_sample_completion_stays_in_surveying_without_auto_resume(self):
        module = _load_script("mavlink_trigger_node_survey_completion_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node.is_sampling = True
        node._survey_active = True
        node._survey_sample_active = True
        node._survey_interval = 5.0
        node.current_waypoint = 4
        states = []
        statuses = []
        resumed = []
        node._set_waypoint_state = lambda *args: None
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._publish_status = lambda status: statuses.append(status)
        node._resume_auto_if_mission_exists = lambda: resumed.append(True)

        node._handle_completion(success=True, reason="finished")

        self.assertFalse(node.is_sampling)
        self.assertFalse(node._survey_sample_active)
        self.assertIn((module.MissionState.SURVEYING, "5.0"), states)
        self.assertIn("survey_sample_done", statuses)
        self.assertEqual(resumed, [])

    def test_start_survey_keeps_param1_interval_and_default_gates_compatible(self):
        module = _load_script("mavlink_trigger_node_survey_gate_compat_test", "scripts/mavlink_trigger_node.py")
        started_threads = []
        control_calls = []

        class ControlService:
            def __call__(self, command_id="", source="", action="", payload_json=""):
                control_calls.append({
                    "command_id": command_id,
                    "source": source,
                    "action": action,
                    "payload_json": payload_json,
                })
                return types.SimpleNamespace(success=True, message="ok", result_json="{}")

        class RecordingThread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        original_thread = module.threading.Thread
        original_service_proxy = module.rospy.ServiceProxy
        module.threading.Thread = RecordingThread
        module.rospy.ServiceProxy = lambda *args, **kwargs: ControlService()
        try:
            node = module.MAVLinkTriggerNode()
            accepted = node.handle_mavlink_command(31015, 5.0, 0.0)
        finally:
            module.threading.Thread = original_thread
            module.rospy.ServiceProxy = original_service_proxy

        self.assertTrue(accepted)
        self.assertTrue(node._survey_active)
        self.assertEqual(node._survey_interval, 5.0)
        self.assertEqual(len(started_threads), 1)
        self.assertEqual([call["action"] for call in control_calls], ["injection_on"])
        allowed, reason = node._survey_gate_status({})
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_survey_stop_turns_injection_pump_off_once(self):
        module = _load_script("mavlink_trigger_node_survey_stop_injection_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node._survey_active = True
        states = []
        statuses = []
        injection_calls = self._install_injection_session_recorder(node)
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._publish_status = lambda status: statuses.append(status)

        accepted = node._stop_survey()

        self.assertTrue(accepted)
        self.assertFalse(node._survey_active)
        self.assertIn(("off", "survey", "survey_stop"), injection_calls)
        self.assertEqual(statuses, ["survey_stopped"])

    def test_survey_sample_failure_stops_survey_and_injection_pump(self):
        module = _load_script("mavlink_trigger_node_survey_failure_injection_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node.is_sampling = True
        node._survey_active = True
        node._survey_sample_active = True
        node.current_sampling_context = {"source": "survey", "waypoint_seq": 3}
        node.current_waypoint = 3
        states = []
        statuses = []
        injection_calls = self._install_injection_session_recorder(node)
        node._set_waypoint_state = lambda waypoint, state: None
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._publish_status = lambda status: statuses.append(status)
        node._handle_failure_action = lambda reason: statuses.append("failure_action_called")

        node._handle_completion(success=False, reason="automation:error")

        self.assertFalse(node.is_sampling)
        self.assertFalse(node._survey_sample_active)
        self.assertFalse(node._survey_active)
        self.assertIn(("off", "survey", "automation:error"), injection_calls)
        self.assertEqual(statuses, ["survey_stopped"])

    def _make_survey_gate_node(self, module, config):
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node.is_sampling = False
        node._survey_active = True
        node._survey_sample_active = False
        node._survey_interval = 5.0
        node.current_waypoint = 3
        node.last_linear_speed = 1.0
        node.latest_spectrometer_payload = {"valid": True}
        node._latest_global_position = None
        node._last_survey_sample_position = None
        node.steps_pub = RecordingPublisher()
        node.status_pub = RecordingPublisher()
        node.mission_status_pub = RecordingPublisher()
        node._load_config = lambda: config
        node._get_default_config = lambda: {"sampling_sequence": {"steps": [], "loop_count": 1}}
        node._build_steps_payload = lambda cfg, wp: {"steps": [], "loop_count": 1, "waypoint_seq": int(wp)}
        node._set_mission_state = lambda *args, **kwargs: None
        node._call_automation_service = lambda action: True
        return node

    def test_survey_gate_skips_without_gps_when_required_and_keeps_survey_active(self):
        module = _load_script("mavlink_trigger_node_survey_gate_no_gps_test", "scripts/mavlink_trigger_node.py")
        node = self._make_survey_gate_node(module, {
            "survey_sampling": {
                "survey_require_gps": True,
                "survey_min_distance_m": 5.0,
            }
        })

        result = node._start_survey_sample_once(node._load_config())

        self.assertEqual(result, "skipped")
        self.assertTrue(node._survey_active)
        self.assertFalse(node.is_sampling)
        self.assertEqual(node.steps_pub.messages, [])
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:no_gps")

    def test_survey_gate_caches_wgs84_global_position(self):
        module = _load_script("mavlink_trigger_node_survey_gate_gps_cache_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node._latest_global_position = None

        node._global_position_cb(module.NavSatFix(latitude=30.1, longitude=120.2, altitude=4.5))

        self.assertAlmostEqual(node._latest_global_position["lat"], 30.1)
        self.assertAlmostEqual(node._latest_global_position["lon"], 120.2)
        self.assertAlmostEqual(node._latest_global_position["alt"], 4.5)
        self.assertIn("received_at", node._latest_global_position)

    def test_survey_gate_skips_stale_gps_when_required(self):
        module = _load_script("mavlink_trigger_node_survey_gate_stale_gps_test", "scripts/mavlink_trigger_node.py")
        node = self._make_survey_gate_node(module, {
            "survey_sampling": {
                "survey_require_gps": True,
                "survey_max_position_age_s": 5.0,
            }
        })
        node._latest_global_position = {
            "lat": 30.0,
            "lon": 120.0,
            "received_at": module.time.time() - 10.0,
        }

        result = node._start_survey_sample_once(node._load_config())

        self.assertEqual(result, "skipped")
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:gps_stale")

    def test_survey_gate_skips_speed_out_of_range(self):
        module = _load_script("mavlink_trigger_node_survey_gate_speed_test", "scripts/mavlink_trigger_node.py")
        node = self._make_survey_gate_node(module, {
            "survey_sampling": {
                "survey_min_speed_mps": 0.5,
                "survey_max_speed_mps": 2.0,
            }
        })
        node.last_linear_speed = 0.1

        low_result = node._start_survey_sample_once(node._load_config())

        self.assertEqual(low_result, "skipped")
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:speed_too_low")

        node.last_linear_speed = 2.5
        high_result = node._start_survey_sample_once(node._load_config())

        self.assertEqual(high_result, "skipped")
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:speed_too_high")

    def test_survey_gate_uses_wgs84_distance_from_last_successful_start(self):
        module = _load_script("mavlink_trigger_node_survey_gate_distance_test", "scripts/mavlink_trigger_node.py")
        node = self._make_survey_gate_node(module, {
            "survey_sampling": {
                "survey_require_gps": True,
                "survey_min_distance_m": 5.0,
                "survey_max_position_age_s": 30.0,
            }
        })
        now = module.time.time()
        node._last_survey_sample_position = {"lat": 30.0, "lon": 120.0, "received_at": now - 1.0}
        node._latest_global_position = {"lat": 30.0, "lon": 120.0, "received_at": now}

        skipped = node._start_survey_sample_once(node._load_config())

        self.assertEqual(skipped, "skipped")
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:distance_too_short")

        node._latest_global_position = {"lat": 30.0001, "lon": 120.0, "received_at": now}
        started = node._start_survey_sample_once(node._load_config())

        self.assertEqual(started, "started")
        self.assertTrue(node.is_sampling)
        self.assertTrue(node._survey_sample_active)
        self.assertEqual(len(node.steps_pub.messages), 1)
        payload = json.loads(node.steps_pub.messages[-1].data)
        self.assertEqual(payload["loop_count"], 1)
        self.assertEqual(payload["waypoint_seq"], 3)
        self.assertAlmostEqual(node._last_survey_sample_position["lat"], 30.0001)

    def test_survey_gate_skips_invalid_spectrometer_when_required(self):
        module = _load_script("mavlink_trigger_node_survey_gate_spectrometer_test", "scripts/mavlink_trigger_node.py")
        node = self._make_survey_gate_node(module, {
            "survey_sampling": {
                "survey_require_valid_spectrometer": True,
            }
        })
        node.latest_spectrometer_payload = {"valid": False}

        result = node._start_survey_sample_once(node._load_config())

        self.assertEqual(result, "skipped")
        self.assertFalse(node.is_sampling)
        self.assertEqual(node.steps_pub.messages, [])
        self.assertEqual(node.status_pub.messages[-1].data, "survey_gate_skipped:spectrometer_invalid")

    def test_bringup_launch_exposes_survey_gate_defaults(self):
        launch_text = (REPO_ROOT / "launch" / "usv_bringup.launch").read_text(encoding="utf-8")

        self.assertIn('<arg name="survey_interval_s" default="5.0" />', launch_text)
        self.assertIn('<arg name="survey_min_distance_m" default="0.0" />', launch_text)
        self.assertIn('<arg name="survey_require_gps" default="false" />', launch_text)
        self.assertIn('<arg name="survey_require_valid_spectrometer" default="false" />', launch_text)

    def test_sampling_failure_publishes_sampling_stopped_for_bridge_completion(self):
        module = _load_script("mavlink_trigger_node_failure_stopped_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.state_lock = threading.Lock()
        node.is_sampling = True
        node._survey_sample_active = False
        node.current_waypoint = 9
        states = []
        statuses = []
        failures = []
        node._set_waypoint_state = lambda *args: None
        node._set_mission_state = lambda state, context=None: states.append((state, context))
        node._publish_status = lambda status: statuses.append(status)
        node._handle_failure_action = lambda reason: failures.append(reason)

        node._handle_completion(success=False, reason="automation_timeout")

        self.assertFalse(node.is_sampling)
        self.assertIn("sampling_stopped", statuses)
        self.assertIn((module.MissionState.FAILED, "9:automation_timeout"), states)
        self.assertEqual(failures, ["automation_timeout"])

    def test_trigger_retry_passes_decremented_retry_count_to_next_attempt(self):
        module = _load_script("mavlink_trigger_node_retry_test", "scripts/mavlink_trigger_node.py")
        node = module.MAVLinkTriggerNode.__new__(module.MAVLinkTriggerNode)
        node.current_waypoint = 7
        node.default_on_fail = "HOLD"
        node.current_sampling_context = {
            "waypoint_seq": 7,
            "retry_count": 1,
            "on_fail": "HOLD",
        }
        calls = []

        def fake_start_sampling_sequence(waypoint_seq=None, retry_count_override=None, on_fail_override=None):
            calls.append((waypoint_seq, retry_count_override, on_fail_override))

        class ImmediateThread:
            def __init__(self, target, args=(), kwargs=None, daemon=None):
                self.target = target
                self.args = args
                self.kwargs = kwargs or {}

            def start(self):
                self.target(*self.args, **self.kwargs)

        node._start_sampling_sequence = fake_start_sampling_sequence
        original_thread = module.threading.Thread
        module.threading.Thread = ImmediateThread
        try:
            node._handle_failure_action("automation_start_failed")
        finally:
            module.threading.Thread = original_thread

        self.assertEqual(node.current_sampling_context["retry_count"], 0)
        self.assertEqual(calls, [(7, 0, "HOLD")])


if __name__ == "__main__":
    unittest.main()
