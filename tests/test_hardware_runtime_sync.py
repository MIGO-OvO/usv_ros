import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class RecordingPublisher:
    def __init__(self, topic=""):
        self.topic = topic
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class FakeTriggerService:
    def wait_for_service(self, timeout=None):
        return None

    def __call__(self):
        return types.SimpleNamespace(success=True, message="reconnected")


class RecordingSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


def _install_fake_ros_modules():
    publishers = {}

    class String:
        def __init__(self, data=""):
            self.data = data

    class TriggerResponse:
        def __init__(self, success=False, message=""):
            self.success = success
            self.message = message

    rospy = types.ModuleType("rospy")
    rospy.Publisher = lambda topic, *args, **kwargs: publishers.setdefault(topic, RecordingPublisher(topic))
    rospy.Subscriber = lambda *args, **kwargs: None
    rospy.Service = lambda *args, **kwargs: None
    rospy.ServiceProxy = lambda *args, **kwargs: FakeTriggerService()
    rospy.init_node = lambda *args, **kwargs: None
    rospy.get_param = lambda name, default=None: default
    rospy.set_param = lambda *args, **kwargs: None
    rospy.wait_for_service = lambda *args, **kwargs: None
    rospy.loginfo = lambda *args, **kwargs: None
    rospy.logwarn = lambda *args, **kwargs: None
    rospy.logerr = lambda *args, **kwargs: None
    rospy.logdebug = lambda *args, **kwargs: None
    rospy.logdebug_throttle = lambda *args, **kwargs: None
    rospy.sleep = lambda *args, **kwargs: None
    rospy.is_shutdown = lambda: True
    rospy.Rate = lambda hz: types.SimpleNamespace(sleep=lambda: None)

    std_msgs = types.ModuleType("std_msgs")
    std_msgs_msg = types.ModuleType("std_msgs.msg")
    std_msgs_msg.String = String
    std_msgs.msg = std_msgs_msg

    std_srvs = types.ModuleType("std_srvs")
    std_srvs_srv = types.ModuleType("std_srvs.srv")
    std_srvs_srv.Trigger = object
    std_srvs_srv.TriggerResponse = TriggerResponse
    std_srvs.srv = std_srvs_srv

    sys.modules["rospy"] = rospy
    sys.modules["std_msgs"] = std_msgs
    sys.modules["std_msgs.msg"] = std_msgs_msg
    sys.modules["std_srvs"] = std_srvs
    sys.modules["std_srvs.srv"] = std_srvs_srv
    return publishers, String


def _load_script(module_name, relative_path):
    publishers, string_cls = _install_fake_ros_modules()
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, publishers, string_cls


class HardwareRuntimeSyncTests(unittest.TestCase):
    def test_web_periodic_snapshot_does_not_emit_synthetic_voltage_sample(self):
        module, _, _ = _load_script(
            "web_config_server_no_synthetic_voltage_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.socketio = RecordingSocket()

            shutdown_checks = iter([False, True])
            module.rospy.is_shutdown = lambda: next(shutdown_checks, True)
            server._data_push_loop()

        event_names = [event for event, _ in server.socketio.events]
        self.assertIn("status", event_names)
        self.assertIn("angles", event_names)
        self.assertNotIn("voltage", event_names)

    def test_web_spectrometer_start_and_stop_routes_publish_runtime_commands(self):
        module, publishers, _ = _load_script(
            "web_config_server_spectrometer_routes_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            start_resp = client.post("/api/spectrometer/start")
            stop_resp = client.post("/api/spectrometer/stop")

        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual(stop_resp.status_code, 200)
        messages = [
            json.loads(msg.data)
            for msg in publishers["/usv/spectrometer_command"].messages
        ]
        self.assertEqual(messages[-2:], [{"cmd": "start"}, {"cmd": "stop"}])

    def test_web_injection_on_with_speed_sends_set_command(self):
        module, publishers, _ = _load_script(
            "web_config_server_injection_on_speed_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            response = client.post("/api/injection-pump/on", json={"speed": 60})

        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["enabled"], True)
        self.assertEqual(body["data"]["speed"], 60)
        self.assertEqual(publishers["/usv/pump_command"].messages[-1].data, "PUMP:SET:60")

    def test_pump_node_injection_on_service_uses_cached_nonzero_speed(self):
        module, _, _ = _load_script(
            "pump_control_node_injection_on_speed_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.inject_pump_speed = 60
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True

        response = node._injection_on_callback(None)

        self.assertTrue(response.success)
        self.assertEqual(sent, ["PUMP:SET:60"])

    def test_pump_node_injection_on_rejects_zero_speed(self):
        module, _, _ = _load_script(
            "pump_control_node_injection_on_zero_speed_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True

        response = node._injection_on_callback(None)

        self.assertFalse(response.success)
        self.assertEqual(sent, [])
        self.assertIn("speed", response.message.lower())

    def test_ros_spectrometer_default_baseline_is_zero(self):
        config_text = (REPO_ROOT / "config" / "usv_params.yaml").read_text(encoding="utf-8")
        self.assertIn("baseline_voltage: 0.0", config_text)

    def test_frontend_voltage_handler_ignores_unmarked_periodic_snapshots(self):
        store_text = (REPO_ROOT / "frontend" / "src" / "store.ts").read_text(encoding="utf-8")
        self.assertNotIn("data.status === undefined || data.status === 'acquiring'", store_text)
        self.assertIn("data.raw?.valid === true", store_text)

    def test_frontend_injection_on_passes_current_speed_input(self):
        card_text = (REPO_ROOT / "frontend" / "src" / "components" / "injection-pump-card.tsx").read_text(encoding="utf-8")
        self.assertIn("turnInjectionPumpOn(speed)", card_text)

    def test_frontend_monitor_has_spectrometer_start_stop_controls(self):
        monitor_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Monitor.tsx").read_text(encoding="utf-8")
        self.assertIn("/api/spectrometer/start", monitor_text)
        self.assertIn("/api/spectrometer/stop", monitor_text)

    def test_web_apply_publishes_i2c_ads_and_start_commands(self):
        module, publishers, _ = _load_script(
            "web_config_server_hardware_sync_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            response = client.post(
                "/api/hardware/apply",
                json={
                    "pump_serial_port": "/dev/ttyUSB0",
                    "pump_baudrate": 115200,
                    "pump_timeout": 1.0,
                    "ads_address": "0x40",
                    "spectro_channel": 0,
                    "mux": "AIN0_AVSS",
                    "gain": 128,
                    "vref_mode": "INTERNAL",
                    "adc_rate": 90,
                    "publish_rate": 250,
                    "continuous_mode": True,
                    "auto_start": True,
                    "reference_voltage": 2.5,
                    "baseline_voltage": 0.0,
                    "i2c_mapping": {"X": 2, "Y": 3, "Z": 6, "A": 7},
                },
            )

        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["results"]["i2c_mapping"]["success"], True)
        self.assertEqual(body["results"]["spectrometer"]["success"], True)

        messages = [
            json.loads(msg.data)
            for msg in publishers["/usv/spectrometer_command"].messages
        ]
        self.assertEqual(messages[0]["cmd"], "set_i2c_map")
        self.assertEqual(messages[0]["mapping"], {
            "angles": {"X": 2, "Y": 3, "Z": 6, "A": 7},
            "spectro_channel": 0,
        })
        self.assertEqual(messages[1]["cmd"], "configure")
        self.assertEqual(messages[1]["gain"], 4)
        self.assertEqual(messages[1]["publish_rate"], 200)
        self.assertEqual(messages[1]["vref_mode"], "INT")
        self.assertEqual(messages[2]["cmd"], "start")

    def test_pump_node_runtime_commands_generate_expected_serial_commands(self):
        module, _, string_cls = _load_script(
            "pump_control_node_hardware_sync_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True

        node._spectro_cmd_callback(string_cls(json.dumps({
            "cmd": "set_i2c_map",
            "mapping": {
                "angles": {"X": 2, "Y": 3, "Z": 6, "A": 7},
                "spectro_channel": 0,
            },
        })))
        node._spectro_cmd_callback(string_cls(json.dumps({
            "cmd": "configure",
            "ads_address": "0x40",
            "mux": "AIN0_AVSS",
            "gain": 4,
            "vref_mode": "INTERNAL",
            "adc_rate": 90,
            "publish_rate": 200,
            "continuous_mode": True,
            "reference_voltage": 2.5,
            "baseline_voltage": 0.0,
        })))

        self.assertEqual(sent[0], "I2CMAP:X=2,Y=3,Z=6,A=7,SPEC=0")
        self.assertIn("ADSCFG:CH=0,ADDR=0x40,AIN=AIN0,REF=INT,GAIN=4,DR=90,MODE=CONT,PR=200", sent[1])

    def test_web_status_callback_emits_spectrometer_status_without_sample(self):
        module, _, string_cls = _load_script(
            "web_config_server_spectro_status_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.socketio = RecordingSocket()

            server._spectro_status_cb(string_cls("configured"))

        self.assertEqual(server.spectrometer_status, "configured")
        self.assertIn(("spectrometer_status", "configured"), server.socketio.events)


if __name__ == "__main__":
    unittest.main()
