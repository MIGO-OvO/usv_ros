import csv
import io
import importlib.util
import json
import math
import os
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
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
    mavros_state = types.SimpleNamespace(cleared=0, pushed=[], pulled=0)

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
    def service_proxy(name, *args, **kwargs):
        if name == "/mavros/mission/clear":
            def clear_call():
                mavros_state.cleared += 1
                return types.SimpleNamespace(success=True)
            return clear_call
        if name == "/mavros/mission/push":
            def push_call(**call_kwargs):
                waypoints = list(call_kwargs.get("waypoints") or [])
                mavros_state.pushed.append(waypoints)
                return types.SimpleNamespace(success=True, wp_transfered=len(waypoints))
            return push_call
        if name == "/mavros/mission/pull":
            def pull_call():
                mavros_state.pulled += 1
                count = len(mavros_state.pushed[-1]) if mavros_state.pushed else 0
                return types.SimpleNamespace(success=True, wp_received=count)
            return pull_call
        return FakeTriggerService()

    rospy.ServiceProxy = service_proxy
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
    rospy._mavros_state = mavros_state

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

    mavros_msgs = types.ModuleType("mavros_msgs")
    mavros_msgs_msg = types.ModuleType("mavros_msgs.msg")
    mavros_msgs_srv = types.ModuleType("mavros_msgs.srv")

    class Waypoint:
        pass

    class WaypointList:
        def __init__(self, waypoints=None):
            self.waypoints = list(waypoints or [])

    def ardupilot_readback_waypoints():
        if not mavros_state.pushed:
            return []
        home = Waypoint()
        home.frame = 3
        home.command = 16
        home.is_current = False
        home.autocontinue = True
        home.param1 = 0.0
        home.param2 = 0.0
        home.param3 = 0.0
        home.param4 = 0.0
        home.x_lat = 25.0
        home.y_long = 110.0
        home.z_alt = 0.0
        return [home] + list(mavros_state.pushed[-1][1:])

    rospy.wait_for_message = (
        lambda *args, **kwargs: WaypointList(ardupilot_readback_waypoints())
    )
    mavros_msgs_msg.Waypoint = Waypoint
    mavros_msgs_msg.WaypointList = WaypointList
    mavros_msgs_srv.WaypointPush = object
    mavros_msgs_srv.WaypointPull = object
    mavros_msgs_srv.WaypointClear = object
    mavros_msgs.msg = mavros_msgs_msg
    mavros_msgs.srv = mavros_msgs_srv

    sys.modules["rospy"] = rospy
    sys.modules["std_msgs"] = std_msgs
    sys.modules["std_msgs.msg"] = std_msgs_msg
    sys.modules["std_srvs"] = std_srvs
    sys.modules["std_srvs.srv"] = std_srvs_srv
    sys.modules["usv_ros"] = usv_ros
    sys.modules["usv_ros.srv"] = usv_ros_srv
    sys.modules["mavros_msgs"] = mavros_msgs
    sys.modules["mavros_msgs.msg"] = mavros_msgs_msg
    sys.modules["mavros_msgs.srv"] = mavros_msgs_srv
    return publishers, String


def _load_script(module_name, relative_path):
    publishers, string_cls = _install_fake_ros_modules()
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, publishers, string_cls


def _coord_pair(lat, lng):
    return {
        "wgs84": {"lat": lat, "lng": lng},
        "gcj02": {"lat": lat, "lng": lng},
    }


def _sample_event_payload(event_id="sample-t15", droplet_count=12):
    droplets = []
    for index in range(droplet_count):
        droplets.append({
            "droplet_index": index,
            "offset_ms": index * 25,
            "voltage": 2.5 + index * 0.01,
            "absorbance": 0.12 + index * 0.001,
            "truth_concentration": 1.7,
            "estimated_concentration": 1.8,
            "valid": True,
            "saturated": False,
            "noise_flags": [],
        })
    return {
        "schema_version": 2,
        "event_id": event_id,
        "mode": "waypoint",
        "route_ref": {"route_id": "route-t15", "waypoint_index": 2},
        "position": {
            "wgs84": {"lat": 30.0, "lng": 120.0, "alt": None},
            "gcj02": {"lat": 30.0, "lng": 120.0, "alt": None},
        },
        "analyte_id": "nh3n",
        "droplets": droplets,
        "mean": 1.8,
        "median": 1.8,
        "standard_deviation": 0.03,
        "valid_count": droplet_count,
        "quality_flags": [],
        "config_snapshot": {"schema_version": 2, "droplet_count": droplet_count},
    }


class HardwareRuntimeSyncTests(unittest.TestCase):
    def test_detector_default_i2c_mapping_matches_validated_firmware_defaults(self):
        pump_module, _, _ = _load_script(
            "pump_control_node_default_i2c_mapping_test",
            "scripts/pump_control_node.py",
        )
        web_module, _, _ = _load_script(
            "web_config_server_default_i2c_mapping_test",
            "scripts/web_config_server.py",
        )

        expected_angles = {"X": 0, "Y": 3, "Z": 4, "A": 7}
        self.assertEqual(pump_module.DEFAULT_I2C_MAPPING, {
            "angles": expected_angles,
            "spectro_channel": 2,
        })
        self.assertEqual(web_module.DEFAULT_CONFIG["hardware"]["i2c_mapping"], expected_angles)
        self.assertEqual(web_module.DEFAULT_CONFIG["hardware"]["spectro_channel"], 2)

        config_text = (REPO_ROOT / "config" / "usv_params.yaml").read_text(encoding="utf-8")
        self.assertIn("X: 0", config_text)
        self.assertIn("Z: 4", config_text)
        self.assertIn("spectro_channel: 2", config_text)

        settings_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Settings.tsx").read_text(encoding="utf-8")
        self.assertIn("spectro_channel: 2", settings_text)
        self.assertIn("i2c_mapping: { X: 0, Y: 3, Z: 4, A: 7 }", settings_text)

    def test_pump_node_publishes_angle_telemetry_and_detector_angle_age(self):
        module, publishers, _ = _load_script(
            "pump_control_node_angle_telemetry_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()

        node._on_angle_received({"X": 1.234, "Y": 2.345, "Z": 3.456, "A": 4.567})

        legacy_msg = publishers["/usv/pump_angles"].messages[-1].data
        self.assertEqual(legacy_msg, "X:1.234,Y:2.345,Z:3.456,A:4.567")

        telemetry_msg = publishers["/usv/pump_angle_telemetry"].messages[-1].data
        telemetry = json.loads(telemetry_msg)
        self.assertEqual(telemetry["angles"], {"X": 1.234, "Y": 2.345, "Z": 3.456, "A": 4.567})
        self.assertEqual(telemetry["source"], "detector_angle_frame")
        self.assertFalse(telemetry["stale"])
        self.assertTrue(telemetry["valid"])
        self.assertGreaterEqual(telemetry["received_at"], 0.0)
        self.assertGreaterEqual(telemetry["age_ms"], 0)

        node._on_text_received("ANGLE_AGE_MS:2500")

        stale_telemetry = json.loads(publishers["/usv/pump_angle_telemetry"].messages[-1].data)
        self.assertEqual(stale_telemetry["detector_angle_age_ms"], 2500)
        self.assertTrue(stale_telemetry["stale"])
        self.assertEqual(node.latest_detector_health["angle_age_ms"], 2500)

    def test_web_angle_telemetry_emits_staleness_without_polluting_raw_angles(self):
        module, _, string_cls = _load_script(
            "web_config_server_angle_telemetry_test",
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

            server._angle_telemetry_cb(string_cls(json.dumps({
                "angles": {"X": 10.0, "Y": 20.0, "Z": 30.0, "A": 40.0},
                "raw_angles": {"X": 10.0, "Y": 20.0, "Z": 30.0, "A": 40.0},
                "source": "detector_angle_frame",
                "received_at": time.time() - 5.0,
                "detector_angle_age_ms": 2500,
                "stale": False,
                "valid": True,
            })))

        angle_events = [payload for event, payload in server.socketio.events if event == "angle_telemetry"]
        self.assertTrue(angle_events)
        latest = angle_events[-1]
        self.assertEqual(latest["angles"]["X"], 10.0)
        self.assertEqual(latest["raw_angles"]["Z"], 30.0)
        self.assertEqual(latest["detector_angle_age_ms"], 2500)
        self.assertTrue(latest["stale"])
        self.assertTrue(latest["valid"])
        self.assertEqual(server.raw_angles, {"X": 10.0, "Y": 20.0, "Z": 30.0, "A": 40.0})

    def test_pump_serial_reader_parses_detector_health_packet(self):
        module, _, _ = _load_script(
            "pump_control_node_health_packet_test",
            "scripts/pump_control_node.py",
        )
        reader = module.PumpSerialReader(None)
        received = []
        reader.on_health_received = received.append

        body = bytearray()
        body.extend([module.HEADER2_HEALTH, 1, 0x03])
        body.extend((123456).to_bytes(4, "little"))
        body.extend((3600).to_bytes(4, "little"))
        body.extend((432).to_bytes(2, "little", signed=True))
        body.extend((240).to_bytes(2, "little"))
        body.extend((120000).to_bytes(4, "little"))
        body.extend((80000).to_bytes(4, "little"))
        body.extend((320000).to_bytes(4, "little"))
        body.extend([12])
        body.extend((2048).to_bytes(2, "little"))
        body.extend((4096).to_bytes(2, "little"))
        body.extend((1024).to_bytes(2, "little"))
        checksum = 0
        for value in body:
            checksum ^= value
        packet = bytes([module.HEADER1]) + bytes(body) + bytes([checksum, module.TAIL])

        reader._process_data(packet)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["version"], 1)
        self.assertEqual(received[0]["uptime_s"], 3600)
        self.assertAlmostEqual(received[0]["temperature_c"], 43.2)
        self.assertEqual(received[0]["heap_free"], 120000)
        self.assertEqual(received[0]["task_stack_hwm"]["comms"], 4096)

    def test_web_system_health_callback_emits_and_serves_latest_snapshot(self):
        module, _, string_cls = _load_script(
            "web_config_server_system_health_test",
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
            client = server.app.test_client()

            payload = {
                "jetson": {"cpu_percent": 12.5, "memory_percent": 34.0, "temperature_c": 55.2},
                "detector": {"online": True, "temperature_c": 43.2, "heap_free": 120000},
                "health": {"code": 1, "level": "warn", "summary": "warm"},
            }
            server._system_health_cb(string_cls(json.dumps(payload)))
            response = client.get("/api/diagnostics/system")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["latest"]["health"]["summary"], "warm")
        self.assertEqual(body["data"]["history"][-1]["detector"]["heap_free"], 120000)
        self.assertIn(("system_health", payload), server.socketio.events)

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
        self.assertEqual(first_payload["mapping"]["spectro_channel"], 2)
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

    def test_survey_simulated_samples_stay_in_one_lab_surface_mission(self):
        module, _, string_cls = _load_script(
            "web_config_server_survey_lab_surface_test",
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

            server._trigger_status_cb(string_cls("survey_started"))
            for index, (lat, lng) in enumerate([(30.0, 120.0), (30.001, 120.0), (30.0, 120.001)]):
                payload = _sample_event_payload("survey-%d" % index, droplet_count=12)
                payload["mode"] = "survey"
                payload["route_ref"] = {"route_id": "survey-route", "segment_index": index}
                payload["position"]["wgs84"] = {"lat": lat, "lng": lng, "alt": None}
                payload["position"]["gcj02"] = {"lat": lat, "lng": lng, "alt": None}
                server._trigger_status_cb(string_cls("sampling_started"))
                server._lab_sample_event_cb(string_cls(json.dumps(payload)))
                server._trigger_status_cb(string_cls("sampling_stopped"))
            server._trigger_status_cb(string_cls("survey_stopped"))
            missions = server.data_manager.list_missions()

        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0]["point_count"], 3)
        self.assertEqual(missions[0]["valid_surface_point_count"], 3)
        self.assertTrue(missions[0]["surface_ready"])

    def test_lab_virtual_waypoint_samples_stay_in_one_mission_until_lab_complete(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_virtual_recording_test",
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
            server._lab_sim_status_cb(string_cls(json.dumps({
                "running": True,
                "lat": 30.0,
                "lng": 120.0,
                "mission": {
                    "active": False,
                    "total": 3,
                    "target_seq": 0,
                    "reached_count": 1,
                    "completed": False,
                    "waiting_sampling_done": True,
                },
            })))

            for index, (lat, lng) in enumerate([(30.0, 120.0), (30.001, 120.0), (30.0, 120.001)]):
                payload = _sample_event_payload("lab-wp-%d" % index, droplet_count=12)
                payload["route_ref"] = {"route_id": "lab-route", "waypoint_index": index}
                payload["position"]["wgs84"] = {"lat": lat, "lng": lng, "alt": None}
                payload["position"]["gcj02"] = {"lat": lat, "lng": lng, "alt": None}
                server._trigger_status_cb(string_cls("sampling_started"))
                server._lab_sample_event_cb(string_cls(json.dumps(payload)))
                server._trigger_status_cb(string_cls("sampling_stopped"))
                server.lab_status["mission"].update({
                    "active": index < 2,
                    "reached_count": index + 1,
                    "waiting_sampling_done": index < 2,
                })

            self.assertTrue(server._data_recording_active())
            server._lab_sim_status_cb(string_cls(json.dumps({
                "running": False,
                "lat": 30.0,
                "lng": 120.001,
                "mission": {
                    "active": False,
                    "total": 3,
                    "target_seq": None,
                    "reached_count": 3,
                    "completed": True,
                    "waiting_sampling_done": False,
                },
            })))
            missions = server.data_manager.list_missions()

        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0]["point_count"], 3)
        self.assertEqual(missions[0]["valid_surface_point_count"], 3)

    def test_web_live_map_exposes_survey_gate_status(self):
        module, _, string_cls = _load_script(
            "web_config_server_survey_gate_status_test",
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
            server.automation_running = True

            server._mission_status_cb(string_cls("SURVEYING:5.0"))
            server._trigger_status_cb(string_cls("survey_gate_skipped:gps_stale"))
            skipped_live = client.get("/api/map/live").get_json()["data"]

            server._trigger_status_cb(string_cls("survey_sample_done"))
            done_live = client.get("/api/map/live").get_json()["data"]

        survey = skipped_live["survey_status"]
        self.assertEqual(survey["mission_status"], "SURVEYING:5.0")
        self.assertEqual(survey["last_gate"]["status"], "survey_gate_skipped:gps_stale")
        self.assertEqual(survey["last_gate"]["reason"], "gps_stale")
        self.assertIn("GPS", survey["last_gate"]["reason_label"])
        self.assertTrue(skipped_live["automation_running"])
        self.assertEqual(done_live["survey_status"]["trigger_status"]["status"], "survey_sample_done")
        self.assertIsNotNone(done_live["survey_status"]["last_sample_done_at"])

    def test_web_config_round_trips_mapping_profile_thresholds(self):
        module, _, _ = _load_script(
            "web_config_server_mapping_profile_test",
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

            initial = client.get("/api/config").get_json()["mapping_profile"]
            save_resp = client.post("/api/config", json={
                "mapping_profile": {
                    "survey_min_distance_m": "7.5",
                    "survey_min_speed_mps": "0.25",
                    "survey_max_speed_mps": "1.8",
                    "survey_require_gps": True,
                    "survey_require_valid_spectrometer": True,
                    "survey_max_position_age_s": "4.0",
                }
            })
            saved = client.get("/api/config").get_json()["mapping_profile"]

        self.assertEqual(save_resp.status_code, 200)
        self.assertEqual(initial["survey_min_distance_m"], 5.0)
        self.assertTrue(initial["survey_require_gps"])
        self.assertEqual(saved["survey_min_distance_m"], 7.5)
        self.assertEqual(saved["survey_min_speed_mps"], 0.25)
        self.assertEqual(saved["survey_max_speed_mps"], 1.8)
        self.assertTrue(saved["survey_require_gps"])
        self.assertTrue(saved["survey_require_valid_spectrometer"])
        self.assertEqual(saved["survey_max_position_age_s"], 4.0)

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

    def test_lab_simulator_integrates_straight_turn_stop_and_reset(self):
        module = _load_script(
            "lab_sim_node_integrator_test",
            "scripts/lab_sim_node.py",
        )[0]

        sim = module.LabSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "heading_deg": 0.0,
            "max_speed_mps": 1.0,
            "wheel_base_m": 0.5,
        })

        sim.set_virtual_propulsion(1.0, 1.0)
        straight = sim.step(2.0)
        sim.set_virtual_propulsion(-0.5, 0.5)
        turning = sim.step(1.0)
        sim.set_virtual_propulsion(0.0, 0.0)
        stopped = sim.step(1.0)
        sim.reset()
        reset = sim.snapshot()

        self.assertGreater(straight["lat"], 30.0)
        self.assertAlmostEqual(straight["lng"], 120.0, places=5)
        self.assertGreater(turning["heading_deg"], straight["heading_deg"])
        self.assertEqual(stopped["speed_mps"], 0.0)
        self.assertEqual(reset["track_count"], 1)
        self.assertEqual(reset["virtual_propulsion"]["left"], 0.0)
        self.assertFalse(reset["virtual_propulsion"]["real_output_enabled"])

    def test_lab_simulator_loads_mission_without_starting_and_starts_from_origin(self):
        module = _load_script(
            "lab_sim_node_start_origin_test",
            "scripts/lab_sim_node.py",
        )[0]

        sim = module.LabSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "heading_deg": 0.0,
            "max_speed_mps": 1.0,
            "arrival_radius_m": 3.0,
        })
        loaded = sim.load_mission([
            {"lat": 30.0, "lng": 120.0, "seq": 0},
            {"lat": 30.0, "lng": 120.0002, "seq": 1},
        ])
        started = sim.start_mission()
        after_step = sim.step(0.2)
        arrivals = sim.drain_arrivals()

        self.assertFalse(loaded["running"])
        self.assertFalse(loaded["mission"]["active"])
        self.assertAlmostEqual(started["lat"], 30.0)
        self.assertAlmostEqual(started["lng"], 120.0)
        self.assertTrue(after_step["mission"]["active"])
        self.assertEqual(arrivals[0]["seq"], 0)
        self.assertEqual(after_step["mission"]["target_seq"], 1)

    def test_lab_simulator_guidance_heading_matches_east_and_north_motion(self):
        module = _load_script(
            "lab_sim_node_guidance_heading_test",
            "scripts/lab_sim_node.py",
        )[0]

        east = module.LabSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "heading_deg": 90.0,
            "max_speed_mps": 2.0,
            "arrival_radius_m": 0.5,
        })
        east.load_mission([{"lat": 30.0, "lng": 120.001, "seq": 0}])
        east.start_mission()
        east_snapshot = east.step(1.0)

        north = module.LabSimulator({
            "start_lat": 30.0,
            "start_lng": 120.0,
            "heading_deg": 0.0,
            "max_speed_mps": 2.0,
            "arrival_radius_m": 0.5,
        })
        north.load_mission([{"lat": 30.001, "lng": 120.0, "seq": 0}])
        north.start_mission()
        north_snapshot = north.step(1.0)

        self.assertGreater(east_snapshot["lng"], 120.0)
        self.assertAlmostEqual(east_snapshot["lat"], 30.0, places=5)
        self.assertAlmostEqual(east_snapshot["heading_deg"], 90.0, delta=1.0)
        self.assertGreater(north_snapshot["lat"], 30.0)
        self.assertAlmostEqual(north_snapshot["lng"], 120.0, places=5)
        self.assertAlmostEqual(north_snapshot["heading_deg"], 0.0, delta=1.0)

    def test_web_map_config_serves_offline_tile_proxy_without_amap_key(self):
        module, _, _ = _load_script(
            "web_config_server_map_config_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")
            cache_dir = str(Path(tmpdir) / "map_cache")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            module.mtc.CACHE_DIR = cache_dir
            server = module.WebConfigServer(standalone=True)
            server.tile_cache = module.mtc.MapTileCache(cache_dir=cache_dir)
            client = server.app.test_client()
            response = client.get("/api/map/config")
            placeholder = client.get("/api/map/tile/not-a-style/3/0/0.png")
            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["enabled"])
        self.assertEqual(payload["data"]["provider"], "leaflet-amap-raster")
        self.assertIn("{style}", payload["data"]["tile_url"])
        self.assertIn("v=2", payload["data"]["tile_url"])
        self.assertIn("satellite", payload["data"]["styles"])
        self.assertEqual(payload["data"]["default_center"], {"lng": 110.412778, "lat": 25.314167})
        self.assertNotIn("key", payload["data"])
        self.assertNotIn("securityJsCode", payload["data"])
        self.assertEqual(placeholder.headers.get("X-Tile-Source"), "placeholder")
        self.assertIn("no-store", placeholder.headers.get("Cache-Control", ""))

    def test_map_tile_cache_ignores_legacy_offline_state(self):
        module, _, _ = _load_script(
            "map_tile_cache_legacy_offline_state_test",
            "scripts/map_resources/map_tile_cache.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, ".offline_mode").write_text("1", encoding="utf-8")
            cache = module.MapTileCache(cache_dir=tmpdir)
            cache._fetch_remote = lambda style, z, x, y: b"tile"

            data, hit = cache.get_tile("satellite", 13, 6610, 3385)
            enabled = cache.set_offline_mode(True)

            self.assertEqual(data, b"tile")
            self.assertEqual(hit, "remote")
            self.assertFalse(enabled)
            self.assertFalse(cache.offline_mode)
            self.assertFalse(Path(tmpdir, ".offline_mode").exists())

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
            lab_response = client.get("/lab")
            logo_response = client.get("/usv-logo.svg")
            missing_asset = client.get("/not-a-real-file.js")
            missing_api = client.get("/api/not-a-real-route")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<div id="root">', response.data)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(lab_response.status_code, 200)
        self.assertIn(b'<div id="root">', lab_response.data)
        self.assertEqual(logo_response.status_code, 200)
        self.assertIn("image/svg+xml", logo_response.headers.get("Content-Type", ""))
        self.assertIn("no-store", logo_response.headers.get("Cache-Control", ""))
        self.assertNotIn(b'<div id="root">', logo_response.data)
        self.assertEqual(missing_asset.status_code, 404)
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
                    "pollutant_name": "COD",
                    "method_name": "UV254",
                    "wavelength_nm": 254.0,
                    "calibration_id": "cal-20260609",
                    "calibrated_at": "2026-06-09T09:00:00+08:00",
                    "min_valid": 0.0,
                    "max_valid": 5.0,
                    "lod": 0.05,
                    "loq": 0.15,
                    "clamp_negative": True,
                }
            })

            server._start_data_recording_if_needed()
            server._gps_cb(types.SimpleNamespace(
                latitude=30.0,
                longitude=120.0,
                altitude=4.5,
                fix_type=3,
                hdop=0.8,
                speed_mps=1.1,
            ))
            server.current_position["received_at"] = module.time.time() - 2.0
            server.route_snapshot_id = "route-test"
            server.route_source = "mavros"
            server._mission_status_cb(string_cls("SAMPLING:4"))
            server._automation_status_cb(string_cls(json.dumps({
                "running": True,
                "step_index": 2,
                "loop_index": 1,
            })))
            server._system_health_cb(string_cls(json.dumps({
                "health": {"summary": "nominal"},
            })))
            server._voltage_cb(string_cls(json.dumps({
                "voltage": 1.2,
                "absorbance": 0.3,
                "valid": True,
                "baseline_set": True,
                "baseline_voltage": 0.05,
                "reference_voltage": 1.23,
                "raw_code": 1234,
            })))

            point = server.data_manager.current_mission_data["data_points"][-1]

        self.assertEqual(point["waypoint_seq"], 4)
        self.assertEqual(point["step_index"], 2)
        self.assertEqual(point["loop_index"], 1)
        self.assertEqual(point["metric_used"], "concentration")
        self.assertAlmostEqual(point["concentration"], 0.7)
        self.assertEqual(point["concentration_unit"], "mg/L")
        self.assertEqual(point["concentration_display_name"], "COD")
        self.assertEqual(point["pollutant_name"], "COD")
        self.assertEqual(point["method_name"], "UV254")
        self.assertEqual(point["wavelength_nm"], 254.0)
        self.assertEqual(point["calibration_id"], "cal-20260609")
        self.assertEqual(point["calibrated_at"], "2026-06-09T09:00:00+08:00")
        self.assertEqual(point["min_valid"], 0.0)
        self.assertEqual(point["max_valid"], 5.0)
        self.assertEqual(point["lod"], 0.05)
        self.assertEqual(point["loq"], 0.15)
        self.assertEqual(point["clamp_negative"], True)
        self.assertEqual(point["wgs84"]["lat"], 30.0)
        self.assertEqual(point["wgs84"]["lng"], 120.0)
        self.assertIn("gcj02", point)
        self.assertGreaterEqual(point["position_age_s"], 2.0)
        self.assertLess(point["position_age_s"], 3.0)
        self.assertEqual(point["mission_status"], "SAMPLING:4")
        self.assertEqual(point["route_snapshot_id"], "route-test")
        self.assertEqual(point["route_source"], "mavros")
        quality = point["quality"]
        self.assertEqual(quality["spectrometer_valid"], True)
        self.assertEqual(quality["baseline_set"], True)
        self.assertAlmostEqual(quality["baseline_voltage"], 0.05)
        self.assertAlmostEqual(quality["reference_voltage"], 1.23)
        self.assertEqual(quality["gps_valid"], True)
        self.assertEqual(quality["position_source"], "real")
        self.assertEqual(quality["gps_fix_type"], 3)
        self.assertAlmostEqual(quality["hdop"], 0.8)
        self.assertAlmostEqual(quality["speed_mps"], 1.1)
        self.assertEqual(quality["detector_health_summary"], "nominal")
        metric_snapshot = quality["metric_config_snapshot"]
        self.assertEqual(metric_snapshot["pollutant_name"], "COD")
        self.assertEqual(metric_snapshot["calibration_id"], "cal-20260609")
        self.assertEqual(metric_snapshot["clamp_negative"], True)

    def test_web_records_stale_position_age_and_marks_gps_invalid(self):
        module, _, string_cls = _load_script(
            "web_config_server_stale_position_age_test",
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
            server.data_manager.start_mission("stale-position")
            server.config_manager.update({
                "pollution_metric": {
                    "enabled": True,
                    "slope": 1.0,
                    "intercept": 0.0,
                    "unit": "mg/L",
                    "display_name": "COD",
                    "pollutant_name": "COD",
                }
            })

            server._start_data_recording_if_needed()
            server._gps_cb(types.SimpleNamespace(latitude=30.0, longitude=120.0, altitude=4.5))
            server.current_position["received_at"] = module.time.time() - (module.POSITION_STALE_AFTER_S + 1.0)
            server._mission_status_cb(string_cls("SAMPLING:2"))
            server._automation_status_cb(string_cls(json.dumps({"running": True})))
            server._voltage_cb(string_cls(json.dumps({
                "voltage": 1.0,
                "absorbance": 0.2,
                "valid": True,
                "baseline_set": True,
            })))

            point = server.data_manager.current_mission_data["data_points"][-1]

        self.assertGreater(point["position_age_s"], module.POSITION_STALE_AFTER_S)
        self.assertEqual(point["mission_status"], "SAMPLING:2")
        self.assertEqual(point["quality"]["gps_valid"], False)
        self.assertEqual(point["quality"]["position_source"], "real")

    def test_web_route_snapshot_id_tracks_wgs84_waypoints(self):
        module, _, _ = _load_script(
            "web_config_server_route_snapshot_test",
            "scripts/web_config_server.py",
        )

        server = module.WebConfigServer(standalone=True)

        def waypoint(lat, lng):
            return types.SimpleNamespace(
                x_lat=lat,
                y_long=lng,
                z_alt=0.0,
                command=16,
                frame=3,
                is_current=False,
                autocontinue=True,
            )

        first_route = [
            waypoint(30.0000, 120.0000),
            waypoint(30.0010, 120.0020),
        ]
        server._waypoints_cb(types.SimpleNamespace(waypoints=first_route))
        first_id = server.route_snapshot_id

        self.assertIsNotNone(first_id)
        self.assertEqual(server.route_source, "mavros")
        self.assertEqual(server.route_waypoints[0]["wgs84"]["lat"], 30.0)
        self.assertEqual(server.route_waypoints[1]["wgs84"]["lng"], 120.002)
        self.assertEqual(server._route_snapshot_id(server.route_waypoints), first_id)

        server._waypoints_cb(types.SimpleNamespace(waypoints=first_route))
        self.assertEqual(server.route_snapshot_id, first_id)

        changed_route = [
            waypoint(30.0000, 120.0000),
            waypoint(30.0010, 120.0030),
        ]
        server._waypoints_cb(types.SimpleNamespace(waypoints=changed_route))
        self.assertNotEqual(server.route_snapshot_id, first_id)

        server._waypoints_cb(types.SimpleNamespace(waypoints=[]))
        self.assertIsNone(server.route_snapshot_id)
        self.assertEqual(server.route_source, "none")

    def test_web_route_waypoints_cb_ignores_home_and_script_items(self):
        module, _, _ = _load_script(
            "web_config_server_route_filter_test",
            "scripts/web_config_server.py",
        )

        server = module.WebConfigServer(standalone=True)

        def waypoint(command, lat, lng):
            return types.SimpleNamespace(
                x_lat=lat,
                y_long=lng,
                z_alt=0.0,
                command=command,
                frame=3,
                is_current=False,
                autocontinue=True,
            )

        server._waypoints_cb(types.SimpleNamespace(waypoints=[
            waypoint(16, 25.0, 110.0),
            waypoint(16, 30.0, 120.0),
            waypoint(42702, 0.0, 0.0),
            waypoint(16, 30.001, 120.002),
        ]))

        self.assertEqual(len(server.route_waypoints), 2)
        self.assertEqual([wp["seq"] for wp in server.route_waypoints], [0, 1])
        self.assertEqual(server.route_waypoints[0]["wgs84"]["lat"], 30.0)
        self.assertEqual(server.route_waypoints[1]["wgs84"]["lng"], 120.002)

    def test_web_mission_plan_upload_pushes_mavros_and_updates_live_route(self):
        module, _, _ = _load_script(
            "web_config_server_mission_plan_upload_test",
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
            client = server.app.test_client()

            response = client.post("/api/mission/plan/upload", json={
                "replace": True,
                "waypoints": [
                    {"lat": 30.0, "lng": 120.0, "sample": True},
                    {"lat": 30.001, "lng": 120.002, "sample": False},
                ],
            })

            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["verified"])
        self.assertFalse(payload["data"]["start_auto"])
        self.assertEqual(payload["data"]["commands"], [16, 42702, 16])
        self.assertEqual(module.rospy._mavros_state.cleared, 1)
        self.assertEqual(module.rospy._mavros_state.pulled, 1)
        self.assertEqual(len(module.rospy._mavros_state.pushed[0]), 4)
        self.assertEqual(server.route_source, "web_upload")
        self.assertEqual(len(server.route_waypoints), 2)
        self.assertEqual(server.route_waypoints[0]["wgs84"]["lat"], 30.0)
        self.assertIn(("map_route", server.route_waypoints), server.socketio.events)

    def test_web_mission_plan_upload_persists_sample_timeout(self):
        module, _, _ = _load_script(
            "web_config_server_mission_sample_timeout_test",
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

            response = client.post("/api/mission/plan/upload", json={
                "replace": True,
                "sample_timeout_s": "7200",
                "waypoints": [
                    {"lat": 30.0, "lng": 120.0, "sample": True},
                    {"lat": 30.001, "lng": 120.002, "sample": False},
                ],
            })
            saved_config = client.get("/api/config").get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(saved_config["sampling"]["sample_timeout_s"], 3600.0)

    def test_web_lab_config_round_trips_bounds_dwell_and_water_area(self):
        module, _, _ = _load_script(
            "web_config_server_lab_schema_test",
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

            default_lab = client.get("/api/lab/config").get_json()["data"]
            save_resp = client.post("/api/lab/config", json={
                "enabled": True,
                "sim": {"sample_dwell_s": "700"},
                "pollution": {"value_min": "-2", "value_max": "-1"},
                "water_area": {
                    "enabled": True,
                    "polygon": [
                        _coord_pair(30.0, 120.0),
                        _coord_pair(30.0, 120.1),
                        _coord_pair(30.1, 120.1),
                    ],
                },
            })
            water_resp = client.get("/api/lab/water-area")

        saved = save_resp.get_json()["data"]
        self.assertEqual(default_lab["pollution"]["value_min"], 0.0)
        self.assertEqual(default_lab["pollution"]["value_max"], 1.0)
        self.assertEqual(default_lab["sim"]["sample_dwell_s"], 3.0)
        self.assertEqual(default_lab["water_area"], {"enabled": False, "polygon": []})
        self.assertEqual(save_resp.status_code, 200)
        self.assertEqual(saved["pollution"]["value_min"], 0.0)
        self.assertGreater(saved["pollution"]["value_max"], saved["pollution"]["value_min"])
        self.assertEqual(saved["sim"]["sample_dwell_s"], 600.0)
        self.assertEqual(water_resp.get_json()["data"], saved["water_area"])

    def test_web_lab_simulated_voltage_records_lab_mode_and_saves_immediately(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_voltage_recording_test",
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
            server._start_data_recording_if_needed(source="trigger")
            mission_file = Path(server.data_manager.current_mission_file)

            server.current_position = module.make_lab_position_snapshot(30.0, 120.0)
            server.automation_running = True
            server._voltage_cb(string_cls(json.dumps({
                "voltage": 1.2,
                "absorbance": 0.4,
                "valid": True,
                "baseline_set": True,
            })))
            persisted = json.loads(mission_file.read_text(encoding="utf-8"))

        point = persisted["data_points"][-1]
        self.assertTrue(point["lab_mode"])
        self.assertEqual(point["position_source"], "lab_sim")
        self.assertEqual(point["quality"]["position_source"], "lab_sim")

    def test_web_mission_plan_validate_rejects_bad_coordinate(self):
        module, _, _ = _load_script(
            "web_config_server_mission_plan_validate_test",
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
            response = client.post("/api/mission/plan/validate", json={
                "waypoints": [
                    {"lat": 91.0, "lng": 120.0},
                    {"lat": 30.001, "lng": 120.002},
                ],
            })
            payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertFalse(payload["data"]["valid"])
        self.assertIn("waypoints[0].lat", payload["data"]["errors"][0])

    def test_web_pollution_metric_clamps_negative_only_when_enabled(self):
        module, _, _ = _load_script(
            "web_config_server_pollution_metric_clamp_test",
            "scripts/web_config_server.py",
        )

        unclamped = module.resolve_pollution_metric(
            1.0,
            0.2,
            {"enabled": True, "slope": -2.0, "intercept": 0.1},
        )
        clamped = module.resolve_pollution_metric(
            1.0,
            0.2,
            {"enabled": True, "slope": -2.0, "intercept": 0.1, "clamp_negative": True},
        )

        self.assertAlmostEqual(unclamped["concentration"], -0.3)
        self.assertEqual(unclamped["metric_used"], "concentration")
        self.assertEqual(unclamped["clamp_negative"], False)
        self.assertEqual(clamped["concentration"], 0.0)
        self.assertEqual(clamped["metric_used"], "concentration")
        self.assertEqual(clamped["clamp_negative"], True)

    def test_web_geojson_and_csv_expose_pollutant_contract_fields(self):
        module, _, _ = _load_script(
            "web_config_server_pollution_contract_test",
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
            server.data_manager.start_mission("contract")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            metric_config = {
                "enabled": True,
                "slope": 2.0,
                "intercept": 0.1,
                "unit": "mg/L",
                "display_name": "COD",
                "pollutant_name": "COD",
                "method_name": "UV254",
                "calibration_id": "cal-20260609",
            }
            server.data_manager.add_data_point(
                1.2,
                0.3,
                position={
                    "wgs84": {"lat": 30.0, "lng": 120.0, "alt": 1.0},
                    "gcj02": {"lat": 29.9975, "lng": 120.0046, "alt": 1.0},
                    "position_source": "real",
                },
                spectrometer_raw={
                    "valid": True,
                    "baseline_set": True,
                    "baseline_voltage": 0.05,
                    "reference_voltage": 1.23,
                },
                pollution_metric=metric_config,
                system_health={"health": {"summary": "nominal"}},
            )
            server.data_manager.add_data_point(
                1.4,
                0.5,
                position={
                    "wgs84": {"lat": 30.1, "lng": 120.1, "alt": 1.0},
                    "gcj02": {"lat": 30.1, "lng": 120.1, "alt": 1.0},
                    "position_source": "lab_sim",
                    "lab_mode": True,
                },
                spectrometer_raw={"valid": True, "baseline_set": True},
                pollution_metric=metric_config,
                lab_mode=True,
            )
            client = server.app.test_client()

            geojson_resp = client.get(f"/api/data/mission/{mission_id}/geojson?metric=concentration")
            csv_resp = client.get(f"/api/data/mission/{mission_id}/csv")

        geojson = geojson_resp.get_json()
        sample_feature = [
            f for f in geojson["data"]["features"]
            if f["properties"].get("layer") == "sample"
        ][0]
        rows = list(csv.DictReader(io.StringIO(csv_resp.data.decode("utf-8"))))
        expected_old_columns = [
            "timestamp",
            "voltage",
            "absorbance",
            "concentration",
            "concentration_unit",
            "metric_used",
        ]
        expected_new_columns = [
            "pollutant_name",
            "method_name",
            "calibration_id",
            "quality_flags",
            "valid_for_surface",
            "excluded_reason",
        ]

        self.assertEqual(geojson_resp.status_code, 200)
        self.assertEqual(sample_feature["properties"]["concentration_unit"], "mg/L")
        self.assertEqual(sample_feature["properties"]["metric"], "concentration")
        self.assertEqual(sample_feature["properties"]["pollutant_name"], "COD")
        self.assertEqual(sample_feature["properties"]["method_name"], "UV254")
        self.assertEqual(sample_feature["properties"]["calibration_id"], "cal-20260609")
        self.assertEqual(sample_feature["properties"]["quality_flags"], "")
        self.assertEqual(sample_feature["properties"]["valid_for_surface"], True)
        self.assertEqual(sample_feature["properties"]["excluded_reason"], "")

        self.assertEqual(csv_resp.status_code, 200)
        self.assertEqual(rows[0].keys() & set(expected_old_columns), set(expected_old_columns))
        self.assertEqual(list(rows[0].keys())[-6:], expected_new_columns)
        self.assertIn("concentration", rows[0])
        self.assertIn("concentration_unit", rows[0])
        self.assertIn("metric_used", rows[0])
        self.assertEqual(rows[0]["pollutant_name"], "COD")
        self.assertEqual(rows[0]["method_name"], "UV254")
        self.assertEqual(rows[0]["calibration_id"], "cal-20260609")
        self.assertEqual(rows[0]["quality_flags"], "")
        self.assertEqual(rows[0]["valid_for_surface"], "true")
        self.assertEqual(rows[0]["excluded_reason"], "")
        self.assertEqual(rows[1]["quality_flags"], "gps_invalid|lab_mode")
        self.assertEqual(rows[1]["valid_for_surface"], "false")
        self.assertEqual(rows[1]["excluded_reason"], "lab_excluded")

    def test_web_pollutant_map_exports_have_download_headers(self):
        module, _, _ = _load_script(
            "web_config_server_pollutant_map_export_test",
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
            server.data_manager.start_mission("export")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            metric_config = {
                "enabled": True,
                "slope": 1.0,
                "intercept": 0.0,
                "unit": "mg/L",
                "display_name": "COD",
                "pollutant_name": "COD",
                "calibration_id": "cal-export-20260609",
                "min_valid": 0.0,
                "max_valid": 1.0,
            }
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.2),
                (30.001, 120.000, 0.4),
                (30.000, 120.001, 0.6),
            ]:
                server.data_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            client = server.app.test_client()
            geojson_resp = client.get(f"/api/data/mission/{mission_id}/geojson?metric=concentration&download=true")
            surface_resp = client.get(f"/api/data/mission/{mission_id}/surface?metric=concentration&size=4&download=true")
            csv_resp = client.get(f"/api/data/mission/{mission_id}/csv")

        self.assertEqual(geojson_resp.status_code, 200)
        self.assertEqual(surface_resp.status_code, 200)
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("application/json", geojson_resp.content_type)
        self.assertIn("application/json", surface_resp.content_type)
        self.assertIn(f"mission_{mission_id}.geojson", geojson_resp.headers.get("Content-Disposition", ""))
        self.assertIn(f"mission_{mission_id}_surface.json", surface_resp.headers.get("Content-Disposition", ""))
        self.assertIn(f"mission_{mission_id}.csv", csv_resp.headers.get("Content-Disposition", ""))
        surface_payload = json.loads(surface_resp.data.decode("utf-8"))
        self.assertTrue(surface_payload["data"]["valid"])
        self.assertEqual(len(surface_payload["data"]["grid"]), 16)

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
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True})
            server.data_manager.add_data_point(2.0, 0.4)
            no_gps_point = server.data_manager.current_mission_data["data_points"][-1]
            server.data_manager.add_data_point(2.2, 0.6, position={
                "wgs84": {"lat": 30.001, "lng": 120.001, "alt": 1.0},
                "gcj02": {"lat": 30.001, "lng": 120.001, "alt": 1.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": False, "baseline_set": True})
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
        self.assertEqual(len(sample_features), 2)
        surface_ready_features = [
            f for f in sample_features
            if f["properties"]["valid_for_surface"]
        ]
        excluded_features = [
            f for f in sample_features
            if not f["properties"]["valid_for_surface"]
        ]
        self.assertEqual(len(surface_ready_features), 1)
        self.assertEqual(surface_ready_features[0]["properties"]["value"], 0.2)
        self.assertEqual(excluded_features[0]["properties"]["excluded_reason"], "spectrometer_invalid")
        self.assertEqual(no_gps_point["quality"]["gps_valid"], False)
        self.assertEqual(no_gps_point["quality"]["position_source"], "none")
        self.assertEqual(surface_resp.status_code, 200)
        self.assertFalse(surface["data"]["valid"])
        self.assertIn("3", surface["data"]["reason"])
        self.assertEqual(surface["data"]["point_count"], 1)
        self.assertEqual(surface["data"]["excluded_count"], 2)
        self.assertEqual(surface["data"]["excluded_reasons"]["missing_gps"], 1)
        self.assertEqual(surface["data"]["excluded_reasons"]["spectrometer_invalid"], 1)

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
            metric_config = {
                "enabled": True,
                "slope": 1.0,
                "intercept": 0.0,
                "unit": "mg/L",
                "display_name": "COD",
                "pollutant_name": "COD",
                "calibration_id": "cal-surface-20260609",
                "min_valid": 0.0,
                "max_valid": 1.0,
            }
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.1),
                (30.001, 120.000, 0.3),
                (30.000, 120.001, 0.5),
            ]:
                server.data_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            server.data_manager.add_data_point(
                1.0,
                0.7,
                spectrometer_raw={"valid": True, "baseline_set": True},
                pollution_metric=metric_config,
            )
            server.data_manager.add_data_point(1.0, 0.4, position={
                "wgs84": {"lat": 30.002, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.002, "lng": 120.000, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": False, "baseline_set": True}, pollution_metric=metric_config)
            server.data_manager.add_data_point(1.0, 2.0, position={
                "wgs84": {"lat": 30.003, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.003, "lng": 120.000, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            server.data_manager.add_data_point(1.0, -0.1, position={
                "wgs84": {"lat": 30.0035, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.0035, "lng": 120.000, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            server.data_manager.add_data_point(1.0, 0.6, position={
                "wgs84": {"lat": 30.004, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.004, "lng": 120.000, "alt": 0.0},
                "position_source": "lab_sim",
                "lab_mode": True,
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config, lab_mode=True)
            server.data_manager.current_mission_data["data_points"].append({
                "voltage": 1.0,
                "absorbance": float("nan"),
                "concentration": float("nan"),
                "concentration_unit": "mg/L",
                "concentration_display_name": "COD",
                "pollutant_name": "COD",
                "metric_used": "concentration",
                "gcj02": {"lat": 30.005, "lng": 120.000, "alt": 0.0},
                "quality": {
                    "spectrometer_valid": True,
                    "gps_valid": True,
                    "metric_config_snapshot": metric_config,
                },
            })
            client = server.app.test_client()

            geojson_response = client.get(f"/api/data/mission/{mission_id}/geojson?metric=concentration")
            response = client.get(f"/api/data/mission/{mission_id}/surface?metric=concentration&size=4")
            include_lab_response = client.get(
                f"/api/data/mission/{mission_id}/surface?metric=concentration&size=4&include_lab=true"
            )

        geojson_payload = geojson_response.get_json()
        payload = response.get_json()
        include_lab_payload = include_lab_response.get_json()

        self.assertEqual(geojson_response.status_code, 200)
        self.assertIn("features", geojson_payload["data"])
        self.assertEqual(geojson_payload["data"]["meta"]["metric_label"], "COD")
        self.assertEqual(geojson_payload["data"]["meta"]["unit"], "mg/L")
        self.assertEqual(geojson_payload["data"]["meta"]["pollutant_name"], "COD")
        self.assertEqual(geojson_payload["data"]["meta"]["calibration_id"], "cal-surface-20260609")
        self.assertEqual(geojson_payload["data"]["meta"]["valid_surface_point_count"], 3)
        self.assertEqual(geojson_payload["data"]["meta"]["excluded_reasons"]["lab_excluded"], 1)
        self.assertEqual(geojson_payload["data"]["meta"]["include_lab"], False)
        self.assertEqual(geojson_payload["data"]["meta"]["idw"]["size"], module.DEFAULT_SURFACE_SIZE)
        self.assertAlmostEqual(geojson_payload["data"]["meta"]["idw"]["power"], 2.0)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["valid"])
        self.assertIn("grid", payload["data"])
        self.assertEqual(payload["data"]["meta"]["metric_label"], "COD")
        self.assertEqual(payload["data"]["meta"]["unit"], "mg/L")
        self.assertEqual(payload["data"]["meta"]["pollutant_name"], "COD")
        self.assertEqual(payload["data"]["meta"]["calibration_id"], "cal-surface-20260609")
        self.assertEqual(payload["data"]["meta"]["valid_surface_point_count"], 3)
        self.assertEqual(payload["data"]["meta"]["excluded_reasons"]["above_max_valid"], 1)
        self.assertEqual(payload["data"]["meta"]["idw"]["size"], 4)
        self.assertAlmostEqual(payload["data"]["meta"]["idw"]["power"], 2.0)
        self.assertEqual(len(payload["data"]["grid"]), 16)
        self.assertAlmostEqual(payload["data"]["min"], 0.1)
        self.assertAlmostEqual(payload["data"]["max"], 0.5)
        self.assertEqual(payload["data"]["point_count"], 3)
        self.assertEqual(payload["data"]["excluded_count"], 6)
        self.assertEqual(payload["data"]["excluded_reasons"]["missing_gps"], 1)
        self.assertEqual(payload["data"]["excluded_reasons"]["spectrometer_invalid"], 1)
        self.assertEqual(payload["data"]["excluded_reasons"]["below_min_valid"], 1)
        self.assertEqual(payload["data"]["excluded_reasons"]["above_max_valid"], 1)
        self.assertEqual(payload["data"]["excluded_reasons"]["lab_excluded"], 1)
        self.assertEqual(payload["data"]["excluded_reasons"]["non_finite_metric"], 1)
        self.assertEqual(payload["data"]["metric_label"], "COD")
        self.assertEqual(payload["data"]["unit"], "mg/L")
        self.assertEqual(payload["data"]["size"], 4)
        self.assertAlmostEqual(payload["data"]["power"], 2.0)
        self.assertEqual(include_lab_response.status_code, 200)
        self.assertTrue(include_lab_payload["data"]["valid"])
        self.assertEqual(include_lab_payload["data"]["meta"]["include_lab"], True)
        self.assertEqual(include_lab_payload["data"]["meta"]["valid_surface_point_count"], 4)
        self.assertEqual(include_lab_payload["data"]["point_count"], 4)
        self.assertEqual(include_lab_payload["data"]["excluded_count"], 5)
        self.assertNotIn("lab_excluded", include_lab_payload["data"]["excluded_reasons"])
        self.assertEqual(include_lab_payload["data"]["excluded_reasons"]["below_min_valid"], 1)
        self.assertAlmostEqual(include_lab_payload["data"]["min"], 0.1)
        self.assertAlmostEqual(include_lab_payload["data"]["max"], 0.6)

    def test_web_mission_list_and_detail_include_pollutant_summary(self):
        module, _, _ = _load_script(
            "web_config_server_mission_summary_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            manager.start_mission("summary")
            mission_id = manager.current_mission_data["mission_id"]
            metric_config = {
                "enabled": True,
                "slope": 1.0,
                "intercept": 0.0,
                "unit": "mg/L",
                "display_name": "COD",
                "pollutant_name": "COD",
                "calibration_id": "cal-summary-20260609",
                "min_valid": 0.0,
                "max_valid": 1.0,
            }
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.2),
                (30.001, 120.000, 0.4),
                (30.000, 120.001, 0.6),
            ]:
                manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            manager.add_data_point(
                1.0,
                0.8,
                spectrometer_raw={"valid": True, "baseline_set": True},
                pollution_metric=metric_config,
            )
            manager.add_data_point(1.0, 0.5, position={
                "wgs84": {"lat": 30.002, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.002, "lng": 120.000, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": False, "baseline_set": True}, pollution_metric=metric_config)
            manager.add_data_point(1.0, 2.0, position={
                "wgs84": {"lat": 30.003, "lng": 120.000, "alt": 0.0},
                "gcj02": {"lat": 30.003, "lng": 120.000, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            manager.stop_mission()

            server = module.WebConfigServer(standalone=True)
            server.data_manager = manager
            client = server.app.test_client()

            missions_response = client.get("/api/data/missions")
            detail_response = client.get(f"/api/data/mission/{mission_id}")

        self.assertEqual(missions_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        listed = missions_response.get_json()["data"][0]
        detail = detail_response.get_json()["data"]

        self.assertEqual(listed["id"], mission_id)
        self.assertEqual(listed["point_count"], 6)
        self.assertEqual(listed["valid_surface_point_count"], 3)
        self.assertEqual(listed["pollutant_name"], "COD")
        self.assertEqual(listed["unit"], "mg/L")
        self.assertAlmostEqual(listed["concentration_min"], 0.2)
        self.assertAlmostEqual(listed["concentration_max"], 2.0)
        self.assertEqual(listed["surface_ready"], True)
        self.assertEqual(listed["quality_summary"]["excluded_reasons"]["missing_gps"], 1)
        self.assertEqual(listed["quality_summary"]["excluded_reasons"]["spectrometer_invalid"], 1)
        self.assertEqual(listed["quality_summary"]["excluded_reasons"]["above_max_valid"], 1)
        self.assertEqual(detail["summary"]["calibration_id"], "cal-summary-20260609")
        self.assertEqual(detail["summary"]["valid_surface_point_count"], 3)
        self.assertEqual(detail["summary"]["quality_summary"]["excluded_count"], 3)

    def test_web_live_surface_uses_current_mission_points(self):
        module, _, _ = _load_script(
            "web_config_server_live_surface_test",
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
            client = server.app.test_client()
            metric_config = {
                "enabled": True,
                "slope": 1.0,
                "intercept": 0.0,
                "unit": "mg/L",
                "display_name": "COD",
                "pollutant_name": "COD",
                "calibration_id": "cal-live-20260609",
                "min_valid": 0.0,
                "max_valid": 1.0,
            }

            server.data_manager.start_mission("live-surface")
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.2),
                (30.001, 120.000, 0.4),
                (30.000, 120.001, 0.6),
            ]:
                server.data_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            valid_surface_resp = client.get("/api/map/live/surface?metric=concentration&size=4&power=1.5")
            live_resp = client.get("/api/map/live?metric=concentration&size=4&power=1.5")

            server.data_manager.start_mission("live-too-few")
            for lat, lng, absorbance in [
                (30.000, 120.000, 0.2),
                (30.001, 120.000, 0.4),
            ]:
                server.data_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            invalid_surface_resp = client.get("/api/map/live/surface?metric=concentration&size=4&power=1.5")

        self.assertEqual(valid_surface_resp.status_code, 200)
        surface = valid_surface_resp.get_json()["data"]
        self.assertTrue(surface["valid"])
        self.assertEqual(surface["point_count"], 3)
        self.assertEqual(len(surface["grid"]), 16)
        self.assertEqual(surface["meta"]["metric_label"], "COD")
        self.assertEqual(surface["meta"]["unit"], "mg/L")
        self.assertEqual(surface["meta"]["calibration_id"], "cal-live-20260609")
        self.assertEqual(surface["meta"]["idw"]["size"], 4)
        self.assertAlmostEqual(surface["meta"]["idw"]["power"], 1.5)

        self.assertEqual(live_resp.status_code, 200)
        live = live_resp.get_json()["data"]
        self.assertEqual(len(live["data_points"]), 3)
        self.assertTrue(live["surface"]["valid"])
        self.assertEqual(live["surface"]["point_count"], 3)

        self.assertEqual(invalid_surface_resp.status_code, 200)
        invalid_surface = invalid_surface_resp.get_json()["data"]
        self.assertFalse(invalid_surface["valid"])
        self.assertIn("3", invalid_surface["reason"])
        self.assertEqual(invalid_surface["point_count"], 2)
        self.assertEqual(invalid_surface["grid"], [])

    def test_web_idw_surface_clamps_params_and_handles_exact_or_zero_span_points(self):
        module, _, _ = _load_script(
            "web_config_server_idw_boundary_params_test",
            "scripts/web_config_server.py",
        )

        metric_config = {
            "enabled": True,
            "slope": 1.0,
            "intercept": 0.0,
            "unit": "mg/L",
            "display_name": "COD",
            "pollutant_name": "COD",
            "min_valid": 0.0,
            "max_valid": 1.0,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            server = module.WebConfigServer(standalone=True)
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            manager.start_mission("idw-boundary")
            for lat, lng, absorbance in [
                (30.0000, 120.0000, 0.1),
                (30.0010, 120.0000, 0.2),
                (30.0000, 120.0010, 0.3),
            ]:
                manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
                    "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            clamped_low = server._build_idw_surface(
                manager.current_mission_data,
                metric="concentration",
                size=1,
                power=0.1,
            )
            clamped_high = server._build_idw_surface(
                manager.current_mission_data,
                metric="concentration",
                size=999,
                power=9.0,
            )

            zero_span_manager = module.MissionDataManager(str(Path(tmpdir) / "zero-span"))
            zero_span_manager.start_mission("zero-span")
            for absorbance in [0.1, 0.2, 0.3]:
                zero_span_manager.add_data_point(1.0, absorbance, position={
                    "wgs84": {"lat": 30.0, "lng": 120.0, "alt": 0.0},
                    "gcj02": {"lat": 30.0, "lng": 120.0, "alt": 0.0},
                    "position_source": "real",
                }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            zero_span = server._build_idw_surface(
                zero_span_manager.current_mission_data,
                metric="concentration",
                size=3,
                power=2.0,
            )

        self.assertTrue(clamped_low["valid"])
        self.assertEqual(clamped_low["size"], 3)
        self.assertAlmostEqual(clamped_low["power"], 0.5)
        self.assertEqual(len(clamped_low["grid"]), 9)
        self.assertAlmostEqual(clamped_low["grid"][0]["value"], 0.1)
        self.assertEqual(clamped_high["size"], module.MAX_SURFACE_SIZE)
        self.assertAlmostEqual(clamped_high["power"], 5.0)
        self.assertEqual(len(clamped_high["grid"]), module.MAX_SURFACE_SIZE * module.MAX_SURFACE_SIZE)
        self.assertTrue(zero_span["valid"])
        self.assertEqual(len(zero_span["grid"]), 9)
        self.assertAlmostEqual(zero_span["bounds"]["southwest"]["lat"], 30.0)
        self.assertAlmostEqual(zero_span["bounds"]["northeast"]["lng"], 120.0)
        self.assertTrue(all(math.isfinite(cell["value"]) for cell in zero_span["grid"]))

    def test_web_idw_surface_reports_all_invalid_metric_values(self):
        module, _, _ = _load_script(
            "web_config_server_idw_all_invalid_test",
            "scripts/web_config_server.py",
        )

        metric_config = {
            "enabled": True,
            "slope": 1.0,
            "intercept": 0.0,
            "unit": "mg/L",
            "display_name": "COD",
            "pollutant_name": "COD",
            "min_valid": 0.0,
            "max_valid": 1.0,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            server = module.WebConfigServer(standalone=True)
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            manager.start_mission("idw-all-invalid")
            manager.add_data_point(1.0, -0.1, position={
                "wgs84": {"lat": 30.0, "lng": 120.0, "alt": 0.0},
                "gcj02": {"lat": 30.0, "lng": 120.0, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            manager.add_data_point(1.0, 2.0, position={
                "wgs84": {"lat": 30.001, "lng": 120.0, "alt": 0.0},
                "gcj02": {"lat": 30.001, "lng": 120.0, "alt": 0.0},
                "position_source": "real",
            }, spectrometer_raw={"valid": True, "baseline_set": True}, pollution_metric=metric_config)
            manager.current_mission_data["data_points"].append({
                "voltage": 1.0,
                "absorbance": float("nan"),
                "concentration": float("nan"),
                "concentration_unit": "mg/L",
                "concentration_display_name": "COD",
                "pollutant_name": "COD",
                "metric_used": "concentration",
                "gcj02": {"lat": 30.002, "lng": 120.0, "alt": 0.0},
                "quality": {
                    "spectrometer_valid": True,
                    "gps_valid": True,
                    "metric_config_snapshot": metric_config,
                },
            })
            surface = server._build_idw_surface(
                manager.current_mission_data,
                metric="concentration",
                size=4,
                power=2.0,
            )

        self.assertFalse(surface["valid"])
        self.assertEqual(surface["grid"], [])
        self.assertIsNone(surface["min"])
        self.assertIsNone(surface["max"])
        self.assertEqual(surface["point_count"], 0)
        self.assertEqual(surface["excluded_count"], 3)
        self.assertEqual(surface["excluded_reasons"]["below_min_valid"], 1)
        self.assertEqual(surface["excluded_reasons"]["above_max_valid"], 1)
        self.assertEqual(surface["excluded_reasons"]["non_finite_metric"], 1)

    def test_web_lab_config_api_and_sim_status_feed_live_map(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_api_test",
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

            save_resp = client.post("/api/lab/config", json={
                "enabled": True,
                "bypass_pid_wait": True,
                "position_source": "lab_sim",
                "sim": {"start_lat": 30.0, "start_lng": 120.0, "max_speed_mps": 1.2},
            })
            get_resp = client.get("/api/lab/config")
            start_resp = client.post("/api/lab/start")
            server._lab_sim_status_cb(string_cls(json.dumps({
                "running": True,
                "lat": 30.0,
                "lng": 120.0,
                "heading_deg": 90.0,
                "speed_mps": 0.5,
                "virtual_propulsion": {"left": 0.5, "right": 0.5, "real_output_enabled": False},
            })))
            live_resp = client.get("/api/map/live")
            stop_resp = client.post("/api/lab/stop")

        self.assertEqual(save_resp.status_code, 200)
        self.assertTrue(get_resp.get_json()["data"]["enabled"])
        self.assertTrue(start_resp.get_json()["success"])
        live = live_resp.get_json()["data"]
        self.assertEqual(live["position"]["position_source"], "lab_sim")
        self.assertTrue(live["position"]["lab_mode"])
        self.assertEqual(live["lab_status"]["virtual_propulsion"]["left"], 0.5)
        self.assertTrue(stop_resp.get_json()["success"])

    def test_web_lab_sim_status_converts_wgs84_position_to_gcj02(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_gcj_status_test",
            "scripts/web_config_server.py",
        )
        wgs_lat = 25.314167
        wgs_lng = 110.412778
        expected_gcj = module.wgs84_to_gcj02(wgs_lat, wgs_lng)

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.data_manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            server.data_manager.start_mission("lab")
            client = server.app.test_client()

            server._lab_sim_status_cb(string_cls(json.dumps({
                "running": True,
                "lat": wgs_lat,
                "lng": wgs_lng,
                "heading_deg": 45.0,
                "speed_mps": 0.8,
                "mission": {"active": True, "total": 2, "target_seq": 1, "reached_count": 1},
            })))
            server.data_manager.add_data_point(
                1.2,
                0.3,
                position=server.current_position,
                lab_mode=True,
            )
            lab_resp = client.get("/api/lab/status")
            live_resp = client.get("/api/map/live")

        lab_position = lab_resp.get_json()["data"]["position"]
        live = live_resp.get_json()["data"]
        live_position = live["position"]
        track_position = live["track_points"][-1]
        sample_point = live["data_points"][-1]
        for position in (lab_position, live_position, track_position, sample_point):
            self.assertAlmostEqual(position["wgs84"]["lat"], wgs_lat)
            self.assertAlmostEqual(position["wgs84"]["lng"], wgs_lng)
            self.assertAlmostEqual(position["gcj02"]["lat"], expected_gcj["lat"])
            self.assertAlmostEqual(position["gcj02"]["lng"], expected_gcj["lng"])
            self.assertNotAlmostEqual(position["gcj02"]["lat"], wgs_lat, places=5)
            self.assertNotAlmostEqual(position["gcj02"]["lng"], wgs_lng, places=5)
        self.assertEqual(live["lab_status"]["mission"]["target_seq"], 1)

    def test_web_lab_status_exposes_completion_sampling_progress_and_signal(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_visibility_test",
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
            client.post("/api/lab/config", json={
                "enabled": True,
                "data_source": "simulated",
                "sim": {"sample_dwell_s": 4.5},
            })

            server._lab_sim_status_cb(string_cls(json.dumps({
                "running": False,
                "lat": 25.314167,
                "lng": 110.412778,
                "mission": {
                    "active": False,
                    "total": 2,
                    "target_seq": None,
                    "reached_count": 2,
                    "completed": True,
                    "waiting_sampling_done": False,
                },
            })))
            server._mission_status_cb(string_cls("SAMPLING_DONE:2"))
            server._trigger_status_cb(string_cls("sampling_started"))
            active_resp = client.get("/api/lab/status")
            server._voltage_cb(string_cls(json.dumps({
                "voltage": 1.2,
                "absorbance": 0.3,
                "value": 0.8,
                "waypoint_seq": 2,
                "lab_mode": True,
                "simulated": True,
                "valid": True,
            })))
            server._trigger_status_cb(string_cls("sampling_stopped"))
            done_resp = client.get("/api/lab/status")

        active_status = active_resp.get_json()["data"]["status"]
        done_status = done_resp.get_json()["data"]["status"]
        self.assertTrue(active_status["sampling"]["active"])
        self.assertEqual(active_status["sampling"]["duration_s"], 4.5)
        self.assertTrue(done_status["mission"]["completed"])
        self.assertFalse(done_status["sampling"]["active"])
        self.assertEqual(done_status["sampling"]["progress_percent"], 100.0)
        self.assertEqual(done_status["signal"]["value"], 1.2)
        self.assertEqual(done_status["signal"]["raw"]["waypoint_seq"], 2)

    def test_web_lab_sample_event_callback_exposes_bounded_status_and_one_map_point(self):
        module, _, string_cls = _load_script(
            "web_config_server_lab_sample_event_test",
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
            server.data_manager.start_mission("t15-sample-event")
            mission_id = server.data_manager.current_mission_data["mission_id"]
            client = server.app.test_client()

            event = _sample_event_payload()
            server._lab_sample_event_cb(string_cls(json.dumps(event)))
            server.automation_running = True
            server._voltage_cb(string_cls(json.dumps({
                "sample_event_id": event["event_id"],
                "sample_event_mode": event["mode"],
                "droplet_count": 12,
                "valid_count": 12,
                "voltage": 2.55,
                "absorbance": 0.125,
                "value": 1.8,
                "simulated": True,
                "valid": True,
            })))
            live_resp = client.get("/api/map/live")
            status_resp = client.get("/api/lab/status")
            mission_resp = client.get("/api/data/mission/%s" % mission_id)

        mission = mission_resp.get_json()["data"]
        live = live_resp.get_json()["data"]
        status = status_resp.get_json()["data"]["status"]
        latest_event = status["sampling"]["latest_event"]

        self.assertEqual(len(mission["sampling_events"]), 1)
        self.assertEqual(len(mission["sampling_events"][0]["droplets"]), 12)
        self.assertEqual(len(mission["data_points"]), 1)
        self.assertEqual(len(live["data_points"]), 1)
        self.assertEqual(live["data_points"][0]["sample_event_id"], "sample-t15")
        self.assertNotIn("droplets", live["data_points"][0])
        self.assertEqual(latest_event["event_id"], "sample-t15")
        self.assertEqual(latest_event["droplet_count"], 12)
        self.assertEqual(latest_event["valid_count"], 12)
        self.assertEqual(latest_event["mean"], 1.8)
        self.assertEqual(latest_event["progress_percent"], 100.0)
        self.assertNotIn("droplets", latest_event)
        self.assertEqual(status["signal"]["raw"]["sample_event_id"], "sample-t15")
        self.assertNotIn("droplets", status["signal"]["raw"])

    def test_web_voltage_history_is_bounded_and_omits_large_raw_payload(self):
        module, _, string_cls = _load_script(
            "web_config_server_voltage_history_bound_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.automation_running = True
            client = server.app.test_client()

            for index in range(305):
                server._voltage_cb(string_cls(json.dumps({
                    "sample_event_id": "sample-%03d" % index,
                    "droplets": [{"droplet_index": droplet} for droplet in range(64)],
                    "voltage": 2.0 + index,
                    "absorbance": 0.1,
                    "valid": True,
                })))
            response = client.get("/api/data/voltage")

        payload = response.get_json()["data"]
        self.assertEqual(len(payload), 300)
        self.assertEqual(payload[0]["raw"]["sample_event_id"], "sample-005")
        self.assertEqual(payload[-1]["raw"]["sample_event_id"], "sample-304")
        self.assertNotIn("droplets", payload[-1]["raw"])
        self.assertEqual(payload[-1]["raw"]["droplets_omitted"], 64)

    def test_web_live_map_samples_are_bounded_and_summarize_raw_payload(self):
        module, _, _ = _load_script(
            "web_config_server_live_samples_bound_test",
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
            server.data_manager.start_mission("bounded-live")
            server.data_manager.current_mission_data["data_points"] = [
                {
                    "timestamp": "t-%03d" % index,
                    "voltage": float(index),
                    "raw": {
                        "sample_event_id": "sample-%03d" % index,
                        "droplets": [{"droplet_index": droplet} for droplet in range(64)],
                    },
                }
                for index in range(505)
            ]
            client = server.app.test_client()
            response = client.get("/api/map/live")

        points = response.get_json()["data"]["data_points"]
        self.assertEqual(len(points), 500)
        self.assertEqual(points[0]["timestamp"], "t-005")
        self.assertEqual(points[-1]["timestamp"], "t-504")
        self.assertNotIn("droplets", points[-1]["raw"])
        self.assertEqual(points[-1]["raw"]["droplets_omitted"], 64)

    def test_web_lab_start_publishes_atomic_reset_and_mission_start_command(self):
        module, publishers, _ = _load_script(
            "web_config_server_lab_start_command_test",
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
            save_resp = client.post("/api/lab/config", json={
                "enabled": True,
                "position_source": "lab_sim",
                "sim": {"start": _coord_pair(30.1, 120.2), "heading_deg": 30.0},
                "mission": {"waypoints": [_coord_pair(30.1, 120.2)]},
            })
            mission_resp = client.post("/api/lab/mission", json={
                "waypoints": [_coord_pair(30.1, 120.2)],
            })
            start_resp = client.post("/api/lab/start")

        commands = [
            json.loads(msg.data)
            for msg in publishers["/usv/lab_sim/command"].messages
        ]
        self.assertTrue(save_resp.get_json()["success"])
        self.assertTrue(mission_resp.get_json()["success"])
        self.assertTrue(start_resp.get_json()["success"])
        self.assertEqual(commands[-2]["cmd"], "mission")
        self.assertFalse(commands[-2].get("start", True))
        self.assertEqual(commands[-1]["cmd"], "start")
        self.assertTrue(commands[-1]["reset_to_start"])
        self.assertEqual(commands[-1]["waypoints"][0]["lat"], 30.1)

    def test_web_lab_start_command_uses_wgs84_runtime_coordinates_for_gcj02_clicks(self):
        module, publishers, _ = _load_script(
            "web_config_server_lab_start_wgs84_command_test",
            "scripts/web_config_server.py",
        )
        start_click = {"lat": 25.314167, "lng": 110.412778}
        waypoint_click = {"lat": 25.315, "lng": 110.413}
        expected_start = module.CoordinatePair.from_gcj02(
            module.parse_coordinate(start_click["lat"], start_click["lng"])
        )
        expected_waypoint = module.CoordinatePair.from_gcj02(
            module.parse_coordinate(waypoint_click["lat"], waypoint_click["lng"])
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            client = server.app.test_client()

            save_resp = client.post("/api/lab/config", json={
                "enabled": True,
                "position_source": "lab_sim",
                "sim": {
                    "start": {"input_crs": "GCJ02", "gcj02": start_click},
                    "heading_deg": 30.0,
                },
                "mission": {
                    "waypoints": [
                        {"input_crs": "GCJ02", "gcj02": waypoint_click},
                    ]
                },
            })
            start_resp = client.post("/api/lab/start")

        saved = save_resp.get_json()["data"]
        commands = [
            json.loads(msg.data)
            for msg in publishers["/usv/lab_sim/command"].messages
        ]
        start_command = commands[-1]

        self.assertEqual(save_resp.status_code, 200)
        self.assertTrue(start_resp.get_json()["success"])
        self.assertAlmostEqual(saved["sim"]["start_lat"], start_click["lat"], places=9)
        self.assertAlmostEqual(saved["sim"]["start_lng"], start_click["lng"], places=9)
        self.assertAlmostEqual(start_command["config"]["sim"]["start_lat"], expected_start.wgs84.lat, places=8)
        self.assertAlmostEqual(start_command["config"]["sim"]["start_lng"], expected_start.wgs84.lng, places=8)
        self.assertAlmostEqual(start_command["waypoints"][0]["lat"], expected_waypoint.wgs84.lat, places=8)
        self.assertAlmostEqual(start_command["waypoints"][0]["lng"], expected_waypoint.wgs84.lng, places=8)
        self.assertNotAlmostEqual(start_command["config"]["sim"]["start_lat"], start_click["lat"], places=6)
        self.assertNotAlmostEqual(start_command["waypoints"][0]["lat"], waypoint_click["lat"], places=6)

    def test_web_lab_import_qgc_reads_nested_wgs84_route_waypoints(self):
        module, _, _ = _load_script(
            "web_config_server_lab_import_qgc_test",
            "scripts/web_config_server.py",
        )
        first_wgs = {"lat": 30.0, "lng": 120.0}
        second_wgs = {"lat": 30.001, "lng": 120.002}
        expected_first_gcj = module.wgs84_to_gcj02(first_wgs["lat"], first_wgs["lng"])
        stale_first_gcj = {"lat": expected_first_gcj["lat"] + 0.01, "lng": expected_first_gcj["lng"] + 0.01}
        second_gcj = module.wgs84_to_gcj02(second_wgs["lat"], second_wgs["lng"])

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.route_waypoints = [
                {"seq": 0, "wgs84": first_wgs, "gcj02": stale_first_gcj},
                {"seq": 1, "wgs84": second_wgs, "gcj02": second_gcj},
            ]
            client = server.app.test_client()

            response = client.post("/api/lab/mission/import-qgc")
            config_resp = client.get("/api/lab/config")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        imported = response.get_json()["data"]["waypoints"]
        saved = config_resp.get_json()["data"]["mission"]["waypoints"]
        self.assertEqual(len(imported), 2)
        self.assertEqual(len(saved), 2)
        self.assertAlmostEqual(imported[0]["wgs84"]["lat"], first_wgs["lat"])
        self.assertAlmostEqual(imported[0]["wgs84"]["lng"], first_wgs["lng"])
        self.assertAlmostEqual(imported[0]["gcj02"]["lat"], expected_first_gcj["lat"])
        self.assertAlmostEqual(imported[0]["gcj02"]["lng"], expected_first_gcj["lng"])
        self.assertNotAlmostEqual(imported[0]["gcj02"]["lat"], first_wgs["lat"], places=5)
        self.assertNotAlmostEqual(imported[0]["gcj02"]["lng"], first_wgs["lng"], places=5)
        self.assertEqual(saved, imported)

    def test_mission_data_marks_lab_samples_and_geojson_excludes_by_default(self):
        module, _, _ = _load_script(
            "web_config_server_lab_data_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.MissionDataManager(str(Path(tmpdir) / "missions"))
            manager.start_mission("lab")
            mission_id = manager.current_mission_data["mission_id"]
            lab_position = module.make_position_snapshot(
                30.0,
                120.0,
                source="lab_sim",
                lab_mode=True,
            )
            manager.add_data_point(1.0, 0.2, position=lab_position)
            manager.stop_mission()

            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=True)
            server.data_manager = manager
            client = server.app.test_client()

            raw_resp = client.get(f"/api/data/mission/{mission_id}")
            geojson_resp = client.get(f"/api/data/mission/{mission_id}/geojson?metric=absorbance")
            include_resp = client.get(f"/api/data/mission/{mission_id}/geojson?metric=absorbance&include_lab=true")

        point = raw_resp.get_json()["data"]["data_points"][0]
        self.assertTrue(point["lab_mode"])
        self.assertEqual(point["position_source"], "lab_sim")
        self.assertEqual(len(geojson_resp.get_json()["data"]["features"]), 0)
        self.assertEqual(len(include_resp.get_json()["data"]["features"]), 1)

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

    def test_web_config_migrates_legacy_step_pump_to_global_default_and_strips_steps(self):
        module, _, _ = _load_script(
            "web_config_server_legacy_injection_pump_migration_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.ConfigManager(str(Path(tmpdir) / "sampling_config.json"))
            manager.update({
                "pump_settings": {"pid_mode": True, "pid_precision": 0.1, "default_speed": 0},
                "sampling_sequence": {
                    "loop_count": 1,
                    "steps": [{
                        "name": "legacy",
                        "X": {"enable": "D"},
                        "pump": {"enable": True, "speed": 60, "duration_ms": 3000},
                        "interval": 1000,
                    }],
                },
            })
            config = manager.get()

        self.assertEqual(config["pump_settings"]["default_speed"], 60)
        self.assertNotIn("pump", config["sampling_sequence"]["steps"][0])

    def test_web_config_preserves_zero_loop_count_for_infinite_sampling(self):
        module, _, _ = _load_script(
            "web_config_server_zero_loop_count_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.ConfigManager(str(Path(tmpdir) / "sampling_config.json"))
            manager.update({
                "sampling_sequence": {
                    "loop_count": 0,
                    "steps": [{"name": "continuous", "X": {"enable": "D"}, "interval": 0}],
                },
            })
            config = manager.get()

        self.assertEqual(config["sampling_sequence"]["loop_count"], 0)

    def test_web_publish_steps_includes_injection_pump_policy_and_zero_loop_count(self):
        module, publishers, _ = _load_script(
            "web_config_server_injection_policy_publish_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = str(Path(tmpdir) / "sampling_config.json")

            class TempConfigManager(module.ConfigManager):
                def __init__(self, _config_file=config_file):
                    super().__init__(_config_file)

            module.ConfigManager = TempConfigManager
            server = module.WebConfigServer(standalone=False)
            server.config_manager.update({
                "pump_settings": {
                    "default_speed": 55,
                    "injection_pump_policy": {
                        "mode": "automation",
                        "lead_time_s": 2.5,
                        "stop_on_finish": True,
                    },
                },
                "sampling_sequence": {
                    "loop_count": 0,
                    "steps": [{"name": "flush", "X": {"enable": "E", "speed": "5", "angle": "90"}, "interval": 0}],
                },
            })

            server._publish_steps()

        payload = json.loads(publishers["/usv/automation_steps"].messages[-1].data)
        self.assertEqual(payload["loop_count"], 0)
        self.assertEqual(payload["injection_pump_policy"], {
            "mode": "automation",
            "speed": 55,
            "lead_time_s": 2.5,
            "stop_on_finish": True,
        })

    def test_web_survey_trigger_status_applies_injection_pump_policy(self):
        module, _, string_cls = _load_script(
            "web_config_server_survey_injection_policy_test",
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
            server.config_manager.update({
                "pump_settings": {
                    "default_speed": 55,
                    "injection_pump_policy": {
                        "mode": "survey",
                        "speed": 55,
                        "lead_time_s": 0.0,
                        "stop_on_finish": True,
                    },
                },
            })

            server._trigger_status_cb(string_cls("survey_started"))
            server._trigger_status_cb(string_cls("survey_stopped"))

        self.assertEqual([call["action"] for call in control_calls], ["injection_on", "injection_off"])
        self.assertEqual(json.loads(control_calls[0]["payload_json"])["speed"], 55)

    def test_web_config_uses_legacy_step_pump_when_global_default_missing(self):
        module, _, _ = _load_script(
            "web_config_server_legacy_missing_default_speed_test",
            "scripts/web_config_server.py",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = module.ConfigManager(str(Path(tmpdir) / "sampling_config.json"))
            manager.update({
                "sampling_sequence": {
                    "loop_count": 1,
                    "steps": [{
                        "name": "legacy",
                        "X": {"enable": "D"},
                        "pump": {"enable": True, "speed": 72, "duration_ms": 3000},
                        "interval": 1000,
                    }],
                },
            })
            config = manager.get()

        self.assertEqual(config["pump_settings"]["default_speed"], 72)
        self.assertNotIn("pump", config["sampling_sequence"]["steps"][0])

    def test_pump_node_control_command_requires_manual_mode_and_emits_events(self):
        module, publishers, _ = _load_script(
            "pump_control_node_control_command_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.inject_pump_speed = 0
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

    def test_pump_node_manual_mode_blocks_automation_but_allows_spectrometer_start(self):
        module, _, _ = _load_script(
            "pump_control_node_manual_interlock_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.manual_mode_enabled = True
        node.automation_engine.steps = [{"name": "step"}]
        node.send_command = lambda cmd: True
        node._wait_for_spectro_command_result = lambda timeout=2.0: (True, "ADS_OK:START")

        auto_response = node._auto_start_callback(None)
        spectro_response = node._spectro_start_callback(None)

        self.assertFalse(auto_response.success)
        self.assertTrue(spectro_response.success)
        self.assertIn("manual", auto_response.message.lower())

    def test_pump_node_injection_policy_starts_before_automation_step(self):
        module, _, string_cls = _load_script(
            "pump_control_node_injection_policy_step_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True
        node.pid_mode = False
        node._latest_spectro_received_at = 0.0
        node._steps_callback(string_cls(json.dumps({
            "steps": [{
                "name": "sample",
                "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
                "interval": 0,
            }],
            "loop_count": 0,
            "pid_mode": False,
            "injection_pump_policy": {
                "mode": "automation",
                "speed": 55,
                "lead_time_s": 0.0,
                "stop_on_finish": True,
            },
        })))

        accepted = node._send_automation_step(node.automation_engine.steps[0])

        self.assertTrue(accepted)
        self.assertEqual(node.automation_engine.loop_count, 0)
        self.assertEqual(sent[0], "PUMP:SET:55")
        self.assertIn("XEF", sent[1])

    def test_pump_node_lab_steps_disable_pid_wait_but_keep_real_serial_failures(self):
        module, _, string_cls = _load_script(
            "pump_control_node_lab_steps_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        payload = {
            "steps": [{
                "name": "lab",
                "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
                "interval": 0,
            }],
            "loop_count": 1,
            "pid_mode": True,
            "lab_mode": True,
            "lab_options": {"bypass_pid_wait": True},
        }

        node._steps_callback(string_cls(json.dumps(payload)))

        self.assertTrue(node.lab_mode_enabled)
        self.assertFalse(node.pid_mode)
        self.assertFalse(node.automation_engine._pid_mode_enabled)

    def test_pump_node_manual_step_ignores_legacy_injection_pump_config(self):
        module, _, string_cls = _load_script(
            "pump_control_node_manual_step_legacy_injection_pump_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.inject_pump_speed = 0
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True

        node._step_callback(string_cls(json.dumps({
            "name": "legacy",
            "X": {"enable": "D"},
            "Y": {"enable": "D"},
            "Z": {"enable": "D"},
            "A": {"enable": "D"},
            "pump": {"enable": True, "speed": 60, "duration_ms": 3000},
            "interval": 0,
        })))

        self.assertNotIn("PUMP:SET:60", sent)

    def test_pump_node_automation_step_ignores_legacy_injection_pump_config(self):
        module, _, _ = _load_script(
            "pump_control_node_auto_step_legacy_injection_pump_test",
            "scripts/pump_control_node.py",
        )
        node = module.PumpControlNode()
        node.inject_pump_speed = 0
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True
        node.pid_mode = False
        node._latest_spectro_received_at = 0.0

        accepted = node._send_automation_step({
            "name": "legacy",
            "X": {"enable": "D"},
            "Y": {"enable": "D"},
            "Z": {"enable": "D"},
            "A": {"enable": "D"},
            "pump": {"enable": True, "speed": 60, "duration_ms": 3000},
            "interval": 0,
        })

        self.assertTrue(accepted)
        self.assertNotIn("PUMP:SET:60", sent)

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
        node.inject_pump_speed = 0
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

    def test_frontend_removes_step_injection_pump_editor_and_hardcoded_manual_default(self):
        automation_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Automation.tsx").read_text(encoding="utf-8")
        manual_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Manual.tsx").read_text(encoding="utf-8")

        self.assertNotIn("DEFAULT_INJECTION_PUMP", automation_text)
        self.assertNotIn("duration_ms", automation_text)
        self.assertNotIn("useState('40')", manual_text)
        self.assertNotIn("|| 40", manual_text)

    def test_frontend_monitor_has_spectrometer_start_stop_controls(self):
        monitor_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Monitor.tsx").read_text(encoding="utf-8")
        self.assertIn("/api/spectrometer/start", monitor_text)
        self.assertIn("/api/spectrometer/stop", monitor_text)
        self.assertIn("/api/spectrometer/baseline", monitor_text)

    def test_frontend_spectrometer_charts_limit_axis_and_tooltip_precision(self):
        monitor_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Monitor.tsx").read_text(encoding="utf-8")
        data_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Data.tsx").read_text(encoding="utf-8")
        manual_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Manual.tsx").read_text(encoding="utf-8")

        self.assertIn("formatChartNumber", monitor_text)
        self.assertIn("tickFormatter={formatChartNumber}", monitor_text)
        self.assertIn("formatter={formatChartTooltip}", monitor_text)
        self.assertNotIn("toPrecision(6)", data_text)
        self.assertIn("toPrecision(4)", data_text)
        self.assertIn("const interlockActive = manualStatus.automation_active", manual_text)
        self.assertNotIn("manualStatus.automation_active || manualStatus.spectrometer_active", manual_text)

    def test_frontend_declares_angle_telemetry_state_and_stale_status(self):
        store_text = (REPO_ROOT / "frontend" / "src" / "store.ts").read_text(encoding="utf-8")
        monitor_text = (REPO_ROOT / "frontend" / "src" / "pages" / "Monitor.tsx").read_text(encoding="utf-8")

        self.assertIn("angleTelemetry", store_text)
        self.assertIn("socket.on('angle_telemetry'", store_text)
        self.assertIn("detector_angle_age_ms", store_text)
        self.assertIn("angleTelemetry", monitor_text)
        self.assertIn("angleStatusLabel", monitor_text)

    def test_frontend_declares_map_page_and_leaflet_dependency(self):
        app_text = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        sidebar_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
        mobile_nav_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "MobileNav.tsx").read_text(encoding="utf-8")
        package_text = (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        map_page = REPO_ROOT / "frontend" / "src" / "pages" / "Map.tsx"

        self.assertIn('path="/map"', app_text)
        self.assertIn('href: "/map"', sidebar_text)
        self.assertIn('href: "/map"', mobile_nav_text)
        self.assertTrue(map_page.exists())
        self.assertIn('"leaflet"', package_text)
        self.assertIn("leaflet.heat", package_text)
        self.assertNotIn("@amap/amap-jsapi-loader", package_text)
        map_text = map_page.read_text(encoding="utf-8")
        self.assertIn("/api/map/config", map_text)
        self.assertIn("/api/map/cache/prewarm", map_text)

    def test_frontend_declares_lab_page_and_navigation_entry(self):
        app_text = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        sidebar_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
        mobile_nav_text = (REPO_ROOT / "frontend" / "src" / "components" / "layout" / "MobileNav.tsx").read_text(encoding="utf-8")
        lab_page = REPO_ROOT / "frontend" / "src" / "pages" / "Lab.tsx"

        self.assertIn('path="/lab"', app_text)
        self.assertIn('href: "/lab"', sidebar_text)
        self.assertIn('href: "/lab"', mobile_nav_text)
        self.assertTrue(lab_page.exists())
        lab_text = lab_page.read_text(encoding="utf-8")
        hook_text = (REPO_ROOT / "frontend" / "src" / "hooks" / "use-lab-map.ts").read_text(encoding="utf-8")
        self.assertIn("/api/lab/config", lab_text)
        self.assertIn("/api/lab/start", lab_text)
        self.assertIn("/api/lab/mission", lab_text)
        self.assertIn("persistMission", lab_text)
        self.assertIn("canAcceptRemoteConfig", lab_text)
        self.assertIn("!drawModeRef.current && !pendingRef.current", hook_text)

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
        node.inject_pump_speed = 0
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
        node.inject_pump_speed = 0
        sent = []
        node.send_command = lambda cmd: sent.append(cmd) or True
        node._wait_for_spectro_command_result = lambda timeout=2.0: (True, "ADS_OK:START")

        success, message = node._spectro_start()

        self.assertTrue(success)
        self.assertEqual(message, "ADS_OK:START")
        self.assertEqual(sent[0], "I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2")
        self.assertIn("ADSCFG:CH=2,ADDR=0x40,AIN=AIN0,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=20", sent[1])
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
