import importlib.util
import json
import os
import sys
import tempfile
import time
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


class FakeControlCommandService:
    def __init__(self, calls):
        self.calls = calls

    def wait_for_service(self, timeout=None):
        return None

    def __call__(self, command_id="", source="", action="", payload_json=""):
        self.calls.append({
            "command_id": command_id,
            "source": source,
            "action": action,
            "payload_json": payload_json,
        })
        if action == "injection_status":
            return types.SimpleNamespace(
                success=True,
                message="status",
                result_json=json.dumps({
                    "enabled": False,
                    "speed": 0,
                    "last_response": "",
                    "last_error": "",
                }),
            )
        return types.SimpleNamespace(
            success=True,
            message="%s ok" % action,
            result_json=json.dumps({"action": action}),
        )


class RecordingSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


class RecordingDataManager:
    def __init__(self):
        self.current_mission_file = None
        self.started = []
        self.stopped = 0
        self.points = []

    def start_mission(self, mission_name=""):
        self.started.append(mission_name)
        self.current_mission_file = "mission-test.json"
        return self.current_mission_file

    def stop_mission(self):
        self.stopped += 1
        self.current_mission_file = None

    def add_data_point(self, voltage, absorbance=0.0):
        if self.current_mission_file:
            self.points.append((voltage, absorbance))


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

    usv_ros = types.ModuleType("usv_ros")
    usv_ros_srv = types.ModuleType("usv_ros.srv")
    usv_ros_srv.ControlCommand = object

    class ControlCommandResponse:
        def __init__(self, success=False, message="", result_json=""):
            self.success = success
            self.message = message
            self.result_json = result_json

    usv_ros_srv.ControlCommandResponse = ControlCommandResponse
    usv_ros.srv = usv_ros_srv

    sys.modules["rospy"] = rospy
    sys.modules["std_msgs"] = std_msgs
    sys.modules["std_msgs.msg"] = std_msgs_msg
    sys.modules["std_srvs"] = std_srvs
    sys.modules["std_srvs.srv"] = std_srvs_srv
    sys.modules["usv_ros"] = usv_ros
    sys.modules["usv_ros.srv"] = usv_ros_srv
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

    def test_web_spectrometer_start_reapplies_runtime_config_before_start(self):
        module, publishers, _ = _load_script(
            "web_config_server_spectrometer_routes_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
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

        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual([call["action"] for call in control_calls[-3:]], [
            "spectrometer_i2c_map",
            "spectrometer_configure",
            "spectrometer_start",
        ])
        first_payload = json.loads(control_calls[-3]["payload_json"])
        self.assertEqual(first_payload["mapping"]["spectro_channel"], 0)
        self.assertEqual(publishers["/usv/spectrometer_command"].messages, [])

    def test_web_spectrometer_stop_route_publishes_stop_command(self):
        module, publishers, _ = _load_script(
            "web_config_server_spectrometer_stop_route_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            stop_resp = client.post("/api/spectrometer/stop")

        self.assertEqual(stop_resp.status_code, 200)
        self.assertEqual(control_calls[-1]["action"], "spectrometer_stop")
        self.assertEqual(json.loads(control_calls[-1]["payload_json"]), {"cmd": "stop"})
        self.assertEqual(publishers["/usv/spectrometer_command"].messages, [])

    def test_web_trigger_status_starts_and_stops_data_recording_once(self):
        module, _, string_cls = _load_script(
            "web_config_server_trigger_status_recording_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.data_manager = RecordingDataManager()

            server._trigger_status_cb(string_cls("sampling_started"))
            server._trigger_status_cb(string_cls("sampling_started"))
            server._trigger_status_cb(string_cls("sampling_stopped"))

        self.assertEqual(len(server.data_manager.started), 1)
        self.assertEqual(server.data_manager.stopped, 1)

    def test_web_structured_automation_status_controls_voltage_recording(self):
        module, _, string_cls = _load_script(
            "web_config_server_automation_status_recording_test",
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
            server.data_manager = RecordingDataManager()
            server.data_manager.start_mission("")

            server._automation_status_cb(string_cls(json.dumps({"running": True})))
            server._voltage_cb(string_cls(json.dumps({"voltage": 1.2, "absorbance": 0.3})))

            server._automation_status_cb(string_cls(json.dumps({"running": False})))
            server._voltage_cb(string_cls(json.dumps({"voltage": 2.4, "absorbance": 0.6})))

        self.assertEqual(server.data_manager.points, [(1.2, 0.3)])

    def test_web_direct_automation_terminal_status_stops_only_web_owned_recording(self):
        module, _, string_cls = _load_script(
            "web_config_server_automation_terminal_recording_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)

            web_manager = RecordingDataManager()
            server.data_manager = web_manager
            server._start_data_recording_if_needed(source="web")
            server._automation_status_cb(string_cls(json.dumps({
                "status": "finished",
                "running": False,
                "paused": False,
            })))

            trigger_manager = RecordingDataManager()
            server.data_manager = trigger_manager
            server._start_data_recording_if_needed(source="trigger")
            server._automation_status_cb(string_cls(json.dumps({
                "status": "finished",
                "running": False,
                "paused": False,
            })))

        self.assertEqual(web_manager.stopped, 1)
        self.assertEqual(trigger_manager.stopped, 0)
        self.assertFalse(server.automation_running)

    def test_automation_engine_finished_callback_reports_not_running(self):
        module = _load_script(
            "automation_engine_finished_status_test",
            "scripts/lib/automation_engine.py",
        )[0]

        class DummyCommandGenerator:
            def reset_for_auto_mode(self):
                pass

            def generate_pid_stop_command(self):
                return "PID:STOP"

            def generate_stop_command(self):
                return "STOP"

            def generate_command(self, step, mode="auto"):
                return ""

        engine = module.AutomationEngine(DummyCommandGenerator(), lambda command: True)
        engine.set_steps([{"interval": 0}])
        engine.set_loop_count(1)
        finished_running = []

        def on_status(status):
            if status == "finished":
                finished_running.append(engine.is_running())

        engine.on_status_update = on_status
        self.assertTrue(engine.start())
        deadline = time.time() + 2.0
        while engine._thread and engine._thread.is_alive() and time.time() < deadline:
            time.sleep(0.01)

        self.assertFalse(engine._thread.is_alive())
        self.assertEqual(finished_running, [False])

    def test_web_map_config_reads_amap_environment_without_persisting_credentials(self):
        module, _, _ = _load_script(
            "web_config_server_map_config_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            with unittest.mock.patch.dict(os.environ, {
                "AMAP_WEB_KEY": "test-web-key",
                "AMAP_SECURITY_JS_CODE": "test-security-code",
            }, clear=False):
                server = module.WebConfigServer(standalone=True)
                client = server.app.test_client()
                response = client.get("/api/map/config")

            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["enabled"])
        self.assertEqual(payload["data"]["key"], "test-web-key")
        self.assertEqual(payload["data"]["securityJsCode"], "test-security-code")
        self.assertIn("AMap.HeatMap", payload["data"]["plugins"])
        self.assertFalse(Path(config_file).exists())

    def test_web_static_dist_serves_spa_map_route(self):
        module, _, _ = _load_script(
            "web_config_server_spa_map_route_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            client = server.app.test_client()

            response = client.get("/map")
            missing_api = client.get("/api/not-a-real-route")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<div id="root">', response.data)
        self.assertEqual(missing_api.status_code, 404)

    def test_web_records_geo_sample_with_concentration_when_work_curve_enabled(self):
        module, _, string_cls = _load_script(
            "web_config_server_geo_sample_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.data_manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            server.config_manager.update({
                "pollution_metric": {
                    "enabled": True,
                    "slope": 2.0,
                    "intercept": 0.1,
                    "unit": "mg/L",
                    "display_name": "COD",
                }
            })

            server._start_data_recording_if_needed()
            server._gps_cb(types.SimpleNamespace(latitude=30.0, longitude=120.0, altitude=4.5))
            server._mission_status_cb(string_cls("SAMPLING:4"))
            server._automation_status_cb(string_cls(json.dumps({
                "running": True,
                "step_index": 2,
                "loop_index": 1,
            })))
            server._voltage_cb(string_cls(json.dumps({
                "voltage": 1.2,
                "absorbance": 0.3,
                "valid": True,
                "raw_code": 1234,
            })))

            point = server.data_manager.current_mission_data["data_points"][-1]

        self.assertEqual(point["waypoint_seq"], 4)
        self.assertEqual(point["step_index"], 2)
        self.assertEqual(point["loop_index"], 1)
        self.assertEqual(point["metric_used"], "concentration")
        self.assertAlmostEqual(point["concentration"], 0.7)
        self.assertEqual(point["concentration_unit"], "mg/L")
        self.assertEqual(point["wgs84"]["lat"], 30.0)
        self.assertEqual(point["wgs84"]["lng"], 120.0)
        self.assertIn("gcj02", point)

    def test_web_geojson_excludes_samples_without_gps_and_surface_reports_empty(self):
        module, _, _ = _load_script(
            "web_config_server_geojson_empty_surface_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.data_manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            mission_id = server.data_manager.start_mission("geojson")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            server.data_manager.add_data_point(1.0, 0.2, position={
                "wgs84": {"lat": 30.0, "lng": 120.0, "alt": 1.0},
                "gcj02": {"lat": 29.9975, "lng": 120.0046, "alt": 1.0},
            })
            server.data_manager.add_data_point(2.0, 0.4)
            client = server.app.test_client()

            geojson_resp = client.get(f"/api/data/mission/{mission_id}/geojson?metric=absorbance")
            surface_resp = client.get(f"/api/data/mission/{mission_id}/surface?metric=absorbance")

        geojson = geojson_resp.get_json()
        surface = surface_resp.get_json()
        sample_features = [
            f for f in geojson["data"]["features"]
            if f["properties"].get("layer") == "sample"
        ]

        self.assertEqual(geojson_resp.status_code, 200)
        self.assertEqual(len(sample_features), 1)
        self.assertEqual(sample_features[0]["properties"]["value"], 0.2)
        self.assertEqual(surface_resp.status_code, 200)
        self.assertFalse(surface["data"]["valid"])
        self.assertIn("3", surface["data"]["reason"])

    def test_web_idw_surface_uses_valid_metric_points(self):
        module, _, _ = _load_script(
            "web_config_server_idw_surface_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.data_manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            server.data_manager.start_mission("surface")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.1),
                (30.001, 120.000, 0.3),
                (30.000, 120.001, 0.5),
            ]:
                server.data_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                })
            client = server.app.test_client()

            response = client.get(f"/api/data/mission/{mission_id}/surface?metric=absorbance&size=4")

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["valid"])
        self.assertEqual(len(payload["data"]["grid"]), 16)
        self.assertAlmostEqual(payload["data"]["min"], 0.1)
        self.assertAlmostEqual(payload["data"]["max"], 0.5)

    def test_web_spectrometer_baseline_route_uses_current_valid_voltage(self):
        module, publishers, _ = _load_script(
            "web_config_server_spectrometer_baseline_route_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.current_voltage = 1.234
            server.latest_spectrometer_payload = {
                "valid": True,
                "voltage": 1.234,
                "reference_voltage": 0.0,
                "baseline_set": False,
            }
            client = server.app.test_client()

            response = client.post("/api/spectrometer/baseline")

        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["success"])
        self.assertAlmostEqual(body["reference_voltage"], 1.234)
        self.assertEqual(control_calls[-1]["action"], "spectrometer_baseline")
        self.assertEqual(json.loads(control_calls[-1]["payload_json"]), {
            "cmd": "set_baseline",
            "reference_voltage": 1.234,
        })
        self.assertEqual(publishers["/usv/spectrometer_command"].messages, [])

    def test_web_injection_on_with_speed_sends_set_command(self):
        module, publishers, _ = _load_script(
            "web_config_server_injection_on_speed_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
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
        self.assertEqual(control_calls[-1]["action"], "injection_on")
        self.assertEqual(json.loads(control_calls[-1]["payload_json"])["speed"], 60)
        self.assertEqual(publishers["/usv/pump_command"].messages, [])

    def test_pump_node_control_command_requires_manual_mode_and_emits_events(self):
        module, publishers, _ = _load_script(
            "pump_control_node_control_command_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True

        def request(action, payload=None, command_id="cmd-1"):
            return types.SimpleNamespace(
                command_id=command_id,
                source="unit-test",
                action=action,
                payload_json=json.dumps(payload or {}),
            )

        rejected = node._control_command_callback(request("manual_step", {
            "axis": "X",
            "direction": "F",
            "speed_rpm": 5,
            "angle_deg": 10,
        }))
        self.assertFalse(rejected.success)
        self.assertEqual(sent, [])

        enabled = node._control_command_callback(request("manual_mode", {"enabled": True}, "cmd-2"))
        self.assertTrue(enabled.success)

        accepted = node._control_command_callback(request("manual_step", {
            "axis": "X",
            "direction": "F",
            "speed_rpm": 5,
            "angle_deg": 10,
            "continuous": False,
        }, "cmd-3"))
        self.assertTrue(accepted.success)
        self.assertIn("XEF", sent[-1])

        events = [json.loads(msg.data) for msg in publishers["/usv/control_events"].messages]
        self.assertIn("failed", [event["state"] for event in events])
        self.assertIn("succeeded", [event["state"] for event in events])

    def test_pump_node_manual_mode_blocks_automation_and_spectrometer_start(self):
        module, _, _ = _load_script(
            "pump_control_node_manual_interlock_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.manual_mode_enabled = True
        node.automation_engine.steps = [{"name": "step"}]

        auto_response = node._auto_start_callback(None)
        spectro_response = node._spectro_start_callback(None)

        self.assertFalse(auto_response.success)
        self.assertFalse(spectro_response.success)
        self.assertIn("manual", auto_response.message.lower())
        self.assertIn("manual", spectro_response.message.lower())

    def test_web_manual_routes_use_control_transaction_service(self):
        module, _, _ = _load_script(
            "web_config_server_manual_routes_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            mode_resp = client.post("/api/manual/mode", json={"enabled": True})
            step_resp = client.post("/api/manual/pump-step", json={
                "axis": "X",
                "direction": "F",
                "speed_rpm": 5,
                "angle_deg": 10,
                "continuous": False,
            })

        self.assertEqual(mode_resp.status_code, 200)
        self.assertEqual(step_resp.status_code, 200)
        self.assertEqual([call["action"] for call in control_calls], ["manual_mode", "manual_step"])

    def test_frontend_declares_manual_page_and_control_feedback_state(self):
        app_text = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        sidebar_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
        manual_page = REPO_ROOT / "frontend" / "src" / "pages" / "Manual.tsx"
        store_text = (REPO_ROOT / "frontend" / "src" / "store.ts").read_text(encoding="utf-8")

        self.assertIn('path="/manual"', app_text)
        self.assertIn('href: "/manual"', sidebar_text)
        self.assertTrue(manual_page.exists())
        self.assertIn("controlEvents", store_text)
        self.assertIn("setManualMode", store_text)
        self.assertIn("sendManualPumpStep", store_text)

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

    def test_ros_spectrometer_default_reference_is_unset(self):
        config_text = (REPO_ROOT / "config" / "usv_params.yaml").read_text(encoding="utf-8")
        self.assertIn("reference_voltage: 0.0", config_text)

    def test_config_manager_preserves_correct_zero_spectro_channel(self):
        module, _, _ = _load_script(
            "web_config_server_legacy_spectro_channel_migration_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "sampling_config.json"
            config_file.write_text(json.dumps({
                "hardware": {
                    "spectro_channel": 0,
                    "i2c_mapping": {"X": 2, "Y": 3, "Z": 6, "A": 7},
                }
            }), encoding="utf-8")

            manager = module.ConfigManager(str(config_file))
            manager.load()

        self.assertEqual(manager.get()["hardware"]["spectro_channel"], 0)

    def test_frontend_voltage_handler_ignores_unmarked_periodic_snapshots(self):
        store_text = (REPO_ROOT / "frontend" / "src" / "store.ts").read_text(encoding="utf-8")
        self.assertNotIn("data.status === undefined || data.status === 'acquiring'", store_text)
        self.assertIn("data.raw?.valid === true", store_text)
        self.assertIn("VOLTAGE_UI_INTERVAL_MS = 200", store_text)

    def test_frontend_injection_on_passes_current_speed_input(self):
        card_text = (REPO_ROOT / "frontend" / "src" / "components" / "injection-pump-card.tsx").read_text(encoding="utf-8")
        self.assertIn("turnInjectionPumpOn(speed)", card_text)

    def test_frontend_monitor_has_spectrometer_start_stop_controls(self):
        monitor_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Monitor.tsx").read_text(encoding="utf-8")
        self.assertIn("/api/spectrometer/start", monitor_text)
        self.assertIn("/api/spectrometer/stop", monitor_text)
        self.assertIn("/api/spectrometer/baseline", monitor_text)

    def test_frontend_declares_amap_page_and_loader_dependency(self):
        app_text = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        sidebar_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
        mobile_nav_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "MobileNav.tsx").read_text(encoding="utf-8")
        package_text = (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        map_page = REPO_ROOT / "frontend" / "src" / "pages" / "Map.tsx"

        self.assertIn('path="/map"', app_text)
        self.assertIn('href: "/map"', sidebar_text)
        self.assertIn('href: "/map"', mobile_nav_text)
        self.assertTrue(map_page.exists())
        self.assertIn("@amap/amap-jsapi-loader", package_text)
        self.assertIn("/api/map/config", map_page.read_text(encoding="utf-8"))
        self.assertIn("AMap.HeatMap", map_page.read_text(encoding="utf-8"))

    def test_web_apply_publishes_i2c_ads_and_start_commands(self):
        module, publishers, _ = _load_script(
            "web_config_server_hardware_sync_test",
            "scripts/web_config_server.py",
        )
        control_calls = []
        module.rospy.ServiceProxy = lambda name, *args, **kwargs: (
            FakeControlCommandService(control_calls)
            if name == "/usv/control_command"
            else FakeTriggerService()
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
                    "spectro_channel": 2,
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

        self.assertEqual([call["action"] for call in control_calls[:3]], [
            "spectrometer_i2c_map",
            "spectrometer_configure",
            "spectrometer_start",
        ])
        first_payload = json.loads(control_calls[0]["payload_json"])
        second_payload = json.loads(control_calls[1]["payload_json"])
        self.assertEqual(first_payload["cmd"], "set_i2c_map")
        self.assertEqual(first_payload["mapping"], {
            "angles": {"X": 2, "Y": 3, "Z": 6, "A": 7},
            "spectro_channel": 2,
        })
        self.assertEqual(second_payload["cmd"], "configure")
        self.assertEqual(second_payload["gain"], 4)
        self.assertEqual(second_payload["publish_rate"], 200)
        self.assertEqual(second_payload["vref_mode"], "INT")
        self.assertEqual(publishers["/usv/spectrometer_command"].messages, [])

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
                "spectro_channel": 2,
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

        self.assertEqual(sent[0], "I2CMAP:X=2,Y=3,Z=6,A=7,SPEC=2")
        self.assertIn("ADSCFG:CH=2,ADDR=0x40,AIN=AIN0,REF=INT,GAIN=4,DR=90,MODE=CONT,PR=200", sent[1])

    def test_pump_node_spectrometer_start_reapplies_i2c_and_ads_before_adsstart(self):
        module, _, _ = _load_script(
            "pump_control_node_spectro_start_reconfigure_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True
        node._wait_for_spectro_command_result = lambda timeout=2.0: (True, "ADS_OK:START")

        success, message = node._spectro_start()

        self.assertTrue(success)
        self.assertEqual(message, "ADS_OK:START")
        self.assertEqual(sent[0], "I2CMAP:X=2,Y=3,Z=6,A=7,SPEC=0")
        self.assertIn("ADSCFG:CH=0,ADDR=0x40,AIN=AIN0,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=20", sent[1])
        self.assertEqual(sent[2], "ADSSTART")
        self.assertEqual(node.spectro_reference_voltage, 0.0)

    def test_pump_node_spectrometer_baseline_command_sets_reference_voltage(self):
        module, _, string_cls = _load_script(
            "pump_control_node_spectro_baseline_reference_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()

        node._spectro_cmd_callback(string_cls(json.dumps({
            "cmd": "set_baseline",
            "reference_voltage": 1.234,
        })))

        self.assertAlmostEqual(node.spectro_reference_voltage, 1.234)
        self.assertAlmostEqual(node.spectro_config["reference_voltage"], 1.234)
        self.assertEqual(node._calculate_absorbance(1.234), 0.0)

    def test_pump_node_absorbance_is_unset_until_baseline_reference(self):
        module, _, _ = _load_script(
            "pump_control_node_absorbance_without_reference_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()

        self.assertIsNone(node._calculate_absorbance(1.234))

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
