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

    rospy = types.ModuleType("rospy")
    rospy.Publisher = lambda *args, **kwargs: RecordingPublisher()
    rospy.Subscriber = lambda *args, **kwargs: None
    rospy.init_node = lambda *args, **kwargs: None
    rospy.get_param = lambda name, default=None: default
    rospy.logwarn = lambda *args, **kwargs: None
    rospy.loginfo = lambda *args, **kwargs: None
    rospy.is_shutdown = lambda: True
    rospy.Rate = lambda hz: types.SimpleNamespace(sleep=lambda: None)

    rosnode = types.ModuleType("rosnode")
    rosnode.get_node_names = lambda: ["/pump_control_node", "/web_config_server"]

    std_msgs = types.ModuleType("std_msgs")
    std_msgs_msg = types.ModuleType("std_msgs.msg")
    std_msgs_msg.String = String
    std_msgs.msg = std_msgs_msg

    sys.modules["rospy"] = rospy
    sys.modules["rosnode"] = rosnode
    sys.modules["std_msgs"] = std_msgs
    sys.modules["std_msgs.msg"] = std_msgs_msg
    return String


def _load_script(module_name, relative_path):
    string_cls = _install_fake_ros_modules()
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, string_cls


class SystemHealthNodeTests(unittest.TestCase):
    def test_collector_combines_jetson_detector_and_ros_node_health(self):
        module, string_cls = _load_script("system_health_node_test", "scripts/system_health_node.py")
        collector = module.SystemHealthCollector(
            expected_nodes=["/pump_control_node", "/web_config_server", "/mavlink_trigger_node"],
            stale_after_s=3.0,
        )
        collector.read_cpu_percent = lambda: 12.5
        collector.read_memory = lambda: {
            "memory_total_mb": 4096.0,
            "memory_available_mb": 2048.0,
            "memory_used_mb": 2048.0,
            "memory_percent": 50.0,
        }
        collector.read_temperature_c = lambda: 55.2
        collector.read_uptime_s = lambda: 1234.0
        collector._now = lambda: 100.0
        collector.detector_health_cb(string_cls(json.dumps({
            "temperature_c": 43.2,
            "heap_free": 120000,
            "heap_total": 320000,
            "task_stack_hwm": {"loop": 2048, "comms": 4096, "sensors": 1024},
        })))

        snapshot = collector.collect()

        self.assertEqual(snapshot["jetson"]["cpu_percent"], 12.5)
        self.assertEqual(snapshot["jetson"]["memory_percent"], 50.0)
        self.assertTrue(snapshot["detector"]["online"])
        self.assertEqual(snapshot["detector"]["temperature_c"], 43.2)
        self.assertEqual(snapshot["detector"]["heap_percent_free"], 37.5)
        self.assertEqual(snapshot["ros_nodes"][2]["name"], "/mavlink_trigger_node")
        self.assertFalse(snapshot["ros_nodes"][2]["alive"])
        self.assertEqual(snapshot["health"]["code"], 1)


if __name__ == "__main__":
    unittest.main()
