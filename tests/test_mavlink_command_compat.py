import importlib.util
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
        def __init__(self):
            self.data = ""

    class Float32MultiArray:
        def __init__(self):
            self.data = []

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


class MavlinkCommandCompatibilityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
