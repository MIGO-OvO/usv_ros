"""PR #83 regressions through the real pump, trigger and Web call seams."""
import json
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_automation_preflight as preparation_tests
import test_preflight_web_contract as web_tests
from test_hardware_runtime_sync import _load_script
from test_mavlink_command_compat import _load_script as load_trigger
from gps_fixtures import gps_position
from test_fcu_sampling_result import ForwardPublisher


class FeedbackReviewTests(unittest.TestCase):
    setUp = preparation_tests.PreflightWorkerTests.setUp
    stop = preparation_tests.PreflightWorkerTests.stop
    send = preparation_tests.PreflightWorkerTests.send
    start = preparation_tests.PreflightWorkerTests.start
    join = preparation_tests.PreflightWorkerTests.join
    status = preparation_tests.PreflightWorkerTests.status
    wait_phase = preparation_tests.PreflightWorkerTests.wait_phase

    def test_repeated_last_valid_angle_cannot_keep_oil_motion_alive(self):
        self.block_phase = 'separating'
        reader = self.module.PumpSerialReader(self.node.serial_conn)
        reader.on_angle_received = self.node._on_angle_received
        packet = bytearray(b'\x55\xcc' + struct.pack('<4f', 150, 5, 345, 18))
        checksum = 0
        for value in packet[1:]:
            checksum ^= value
        packet.extend([checksum, 10])
        with patch.object(self.module.AutomationPreflight, 'PROGRESS_TIMEOUT', 0.06):
            self.assertTrue(self.start()[0])
            self.wait_phase('separating')
            deadline = time.monotonic() + 0.25
            # Real firmware repeats last_valid at 50Hz after sensor failure.
            while self.node._preflight_thread.is_alive() and time.monotonic() < deadline:
                reader._process_data(bytes(packet))
                time.sleep(0.01)
            self.assertFalse(self.node._preflight_thread.is_alive(), 'frozen feedback kept oil motion alive')
        self.assertIn('STOPALL', self.commands)
        self.assertIn('AEFV20J720.000', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_other_axis_frames_do_not_refresh_the_oil_axis(self):
        with patch.object(self.module.time, 'monotonic', return_value=time.monotonic() + 2):
            self.node._on_angle_received({'X': 12.0})
            with self.assertRaises(self.module.PreflightError):
                self.node._preflight_angles(['A'])

    def test_oil_axis_outside_steps_must_prove_sensor_validity_before_open_loop(self):
        self.failure_phase = 'separating'
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'pid_fail')
        self.assertIn('AEFR0.000P0.1', self.commands)
        self.assertNotIn('AEFV20J720.000', self.commands)
        self.assertIn('STOPALL', self.commands)

    def test_missing_health_prevents_open_loop_even_with_fresh_angles(self):
        self.node._angle_health_received_at = None
        self.assertTrue(self.start()[0])
        self.join()
        self.assertNotIn('AEFV20J720.000', self.commands)
        self.assertEqual(self.status()['terminal_reason'], 'controller_fault')

    def test_duplicate_health_timestamps_and_fresh_angle_receipts_cannot_renew_health(self):
        with patch.object(self.module.time, 'monotonic', return_value=time.monotonic() + 3):
            self.node._on_health_received({'timestamp_ms': 1000})
            self.node._on_angle_received({'A': 19.0})
            self.node._on_text_received('ANGLE_AGE_CH_MS:1,1,1,1')
            with self.assertRaises(self.module.PreflightError):
                self.node._preflight_angles(['A'], require_health=True)

    def test_health_source_wraparound_is_forward_progress(self):
        for timestamp in (0x7ffffff0, 0xffffffc0, 5):
            self.node._on_health_received({'timestamp_ms': timestamp})
        self.assertEqual(self.node._angle_health_source_ms, 5)
        self.node._preflight_angles(['A'], require_health=True)

    def test_stale_channel_metadata_and_invalid_angle_stop_live_oil_motion(self):
        for fault in ('age', 'nan'):
            with self.subTest(fault=fault):
                self.block_phase = 'separating'
                self.node._on_angle_received({'A': 18.0})
                self.node._on_text_received('ANGLE_AGE_CH_MS:1,1,1,1')
                self.assertTrue(self.start()[0])
                self.wait_phase('separating')
                deadline = time.monotonic() + 1
                while self.node._preflight.travel is None and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertIsNotNone(self.node._preflight.travel)
                if fault == 'age':
                    self.node._on_text_received('ANGLE_AGE_CH_MS:1,1,1,2000')
                else:
                    self.node._on_angle_received({'A': float('nan')})
                self.join()
                self.assertEqual(self.status()['terminal_reason'], 'controller_fault')
                self.node.automation_engine.start.assert_not_called()
                self.assertIn('STOPALL', self.commands)

    def test_small_separation_uses_bounded_encoder_tolerance_independent_of_pid(self):
        sender = self.node.send_command
        def send(command):
            if command.startswith('AEFV'):
                self.node._on_text_received('CMD_OK')
                with self.node._preflight.lock:
                    self.node._preflight.travel['at'] -= 0.03
                self.node._on_angle_received({'A': 21.45})  # 0.15° below a 3.6° target
                return True
            return sender(command)
        self.node.send_command = send
        self.assertTrue(self.start(pid_precision=0.001, preflight={
            'oil_axis': 'A', 'separation_turns': 0.01, 'separation_rpm': 20})[0])
        self.join()
        self.node.automation_engine.start.assert_called_once()
        self.assertIn('ADFV0J0', self.commands)

    def test_tolerance_cannot_release_a_materially_short_separation(self):
        sender = self.node.send_command
        def send(command):
            if command.startswith('AEFV'):
                self.node._on_text_received('CMD_OK')
                with self.node._preflight.lock:
                    self.node._preflight.travel['at'] -= 0.03
                self.node._on_angle_received({'A': 21.25})  # More than 5% short
                return True
            return sender(command)
        self.node.send_command = send
        with patch.object(self.module.AutomationPreflight, 'PROGRESS_TIMEOUT', 0.06):
            self.assertTrue(self.start(pid_precision=10, preflight={
                'oil_axis': 'A', 'separation_turns': 0.01, 'separation_rpm': 20})[0])
            self.join()
        self.assertIn('STOPALL', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_managed_injection_is_confirmed_off_before_homing(self):
        self.node.inject_pump_enabled = True
        self.assertTrue(self.start()[0])
        self.join()
        self.assertLess(self.commands.index('PUMP:OFF'), self.commands.index('XEBR27.000P0.1ZEBR135.000P0.1'))
        self.assertEqual(self.commands.count('PUMP:SET:60'), 1)

    def test_low_rpm_quantized_motion_can_progress_but_stationary_jitter_cannot(self):
        clock = [0.0]
        runner = self.module.AutomationPreflight(lambda command: True, lambda: None,
            lambda axes, require_health=False: {'A': 0}, lambda: None)
        runner.travel = {'axis': 'A', 'angle': 359.9, 'at': 0.0, 'degrees': 0.0,
                         'rpm': 0.1, 'progress_at': 0.0, 'progress_degrees': 0.0}
        with patch.object(self.module.time, 'monotonic', side_effect=lambda: clock[0]):
            for index in range(1, 61):
                clock[0] = index * 0.02
                # MT6701 is 14-bit; at minimum rpm many 50Hz frames repeat.
                angle = round(((359.9 + clock[0] * 0.6) % 360) / (360 / 16384)) * (360 / 16384)
                runner.notify_angles({'A': angle})
                runner._check()
            self.assertGreater(runner.travel['degrees'], 0.7)
            frozen = runner.travel['angle']
            rejected = False
            for index in range(61, 200):
                clock[0] = index * 0.02
                runner.notify_angles({'A': (frozen + (0.02 if index % 2 else -0.02)) % 360})
                try:
                    runner._check()
                except self.module.PreflightError as exc:
                    self.assertIn('forward progress', str(exc))
                    rejected = True
                    break
            self.assertTrue(rejected, 'jitter around a frozen angle renewed the progress clock')


class WebReviewTests(unittest.TestCase):
    setUp = web_tests.PreflightWebContractTests.setUp

    def test_partial_start_pump_settings_preserve_other_values(self):
        original = {'pid_mode': False, 'pid_precision': 0.4, 'default_speed': 38,
                    'preflight': {'oil_axis': 'A', 'separation_turns': 2, 'separation_rpm': 7},
                    'injection_pump_policy': {'mode': 'automation', 'speed': 38,
                                              'lead_time_s': 3, 'stop_on_finish': False}}
        self.assertTrue(self.server.config_manager.update({'pump_settings': original}))
        self.server._call_control_command = Mock(return_value=(True, 'accepted', {}))
        self.server._start_data_recording_if_needed = Mock()
        self.server._start_sample_window_if_needed = Mock()
        response = self.client.post('/api/mission/start', json={
            'require_gps': False, 'pump_settings': {'preflight': {'separation_rpm': 9}}})
        self.assertTrue(response.get_json()['success'])
        expected = dict(original, preflight=dict(original['preflight'], separation_rpm=9))
        self.assertEqual(self.server.config_manager.get()['pump_settings'], expected)

    def test_survey_status_observer_does_not_drive_injection(self):
        self.server.config_manager.update({'pump_settings': {
            'injection_pump_policy': {'mode': 'survey', 'speed': 38}}})
        self.server._call_control_command = Mock(return_value=(True, 'accepted', {}))
        self.server._trigger_status_cb(self.module.String('survey_started'))
        self.server._trigger_status_cb(self.module.String('survey_stopped'))
        self.server._call_control_command.assert_not_called()

    def test_legacy_defaults_need_explicit_calibration_evidence(self):
        self.assertEqual(self.module.calibrated_offsets({
            'offsets': {'X': 0, 'Y': 0, 'Z': 0, 'A': 0}}), {})
        self.assertEqual(self.module.calibrated_offsets({
            'offsets': {'X': 123, 'Y': 0, 'Z': 0, 'A': 0}}), {'X': 123})

    def test_partial_config_save_and_invalid_start_preserve_nested_policy(self):
        self.server.config_manager.update({'pump_settings': {
            'preflight': {'oil_axis': 'A', 'separation_turns': 2, 'separation_rpm': 7},
            'injection_pump_policy': {'mode': 'survey', 'speed': 38, 'lead_time_s': 3}}})
        self.assertTrue(self.client.post('/api/config', json={'pump_settings': {
            'injection_pump_policy': {'speed': 41}}}).get_json()['success'])
        before = self.server.config_manager.get()['pump_settings']
        self.assertEqual(before['injection_pump_policy']['lead_time_s'], 3)
        self.assertEqual(before['injection_pump_policy']['mode'], 'survey')
        self.server._call_control_command = Mock()
        for patch_data in ({'preflight': {'oil_axis': None}}, {'injection_pump_policy': None}, None):
            with self.subTest(patch_data=patch_data):
                response = self.client.post('/api/mission/start', json={
                    'require_gps': False, 'pump_settings': patch_data})
                self.assertFalse(response.get_json()['success'])
                self.assertEqual(self.server.config_manager.get()['pump_settings'], before)
        self.server._call_control_command.assert_not_called()

    def test_explicit_zero_remains_calibrated_after_save_reload(self):
        self.assertTrue(self.client.post('/api/calibration/offsets', json={'offsets': {'X': 0}}).get_json()['success'])
        restored = self.module.CalibrationManager(self.server.calibration_manager.file_path)
        self.assertEqual(restored.configured_axes, {'X'})
        self.assertEqual(restored.offsets['X'], 0)


class TriggerReviewTests(unittest.TestCase):
    def setUp(self):
        self.module = load_trigger('preflight_review_trigger', 'scripts/mavlink_trigger_node.py')
        self.node = self.module.MAVLinkTriggerNode()
        self.node._call_automation_service = Mock(return_value=False)
        self.node._send_command_ack = Mock()
        self.node.current_mission_state = self.module.MissionState.SAMPLING

    def test_rejected_qgc_pause_resume_preserve_mission_and_send_failed_ack(self):
        for command, action in ((31012, 'pause'), (31013, 'resume')):
            with self.subTest(command=command):
                self.node._dispatch_command_long(command, 0, 0, 255, 190, 'test')
                self.node._call_automation_service.assert_called_with(action)
                self.node._send_command_ack.assert_called_with(
                    command, self.module.MAV_RESULT_FAILED, 255, 190)
                self.assertEqual(self.node.current_mission_state, self.module.MissionState.SAMPLING)

    def test_survey_kickoff_cannot_enable_injection_before_sample_preparation(self):
        self.node._load_config = lambda: {}
        self.node._bind_sample_position = Mock(return_value=True)
        self.node._start_injection_session = Mock(return_value=True)
        self.node._survey_loop = Mock()
        with patch.object(self.module.threading.Thread, 'start'):
            self.assertTrue(self.node._start_survey())
        self.node._start_injection_session.assert_not_called()

    def test_successful_qgc_pause_resume_ack_and_mission_follow_pump_response(self):
        self.node._call_automation_service.return_value = True
        for command, state in ((31012, self.module.MissionState.PAUSED),
                               (31013, self.module.MissionState.SAMPLING)):
            self.node._dispatch_command_long(command, 0, 0, 255, 190, 'test')
            self.node._send_command_ack.assert_called_with(command, self.module.MAV_RESULT_ACCEPTED, 255, 190)
            self.assertEqual(self.node.current_mission_state, state)


class SurveyAndQGCChainTests(unittest.TestCase):
    stop = preparation_tests.PreflightWorkerTests.stop
    send = preparation_tests.PreflightWorkerTests.send
    join = preparation_tests.PreflightWorkerTests.join
    status = preparation_tests.PreflightWorkerTests.status
    wait_phase = preparation_tests.PreflightWorkerTests.wait_phase

    def setUp(self):
        preparation_tests.PreflightWorkerTests.setUp(self)
        self.trigger_module = load_trigger('preflight_chain_trigger', 'scripts/mavlink_trigger_node.py')
        self.trigger = self.trigger_module.MAVLinkTriggerNode()
        self.trigger._latest_global_position = gps_position()
        self.requests = []
        def control(**request):
            payload = json.loads(request['payload_json'])
            self.requests.append((request['action'], payload))
            ok, message, result = self.node._execute_control_action(request['action'], payload)
            return SimpleNamespace(success=ok, message=message, result_json=json.dumps(result))
        self.trigger_module.rospy.ServiceProxy = lambda name, *args: (
            control if name == '/usv/control_command' else {
                '/usv/automation_pause': lambda: self.node._auto_pause_callback(None),
                '/usv/automation_resume': lambda: self.node._auto_resume_callback(None),
            }[name])
        self.trigger._queue_automation_terminal = lambda context, terminal: self.trigger._handle_completion(
            expected_context=context, **terminal)
        self.config = {
            'sampling_sequence': {'steps': [{'X': {'enable': 'E', 'direction': 'F', 'angle': '10', 'speed': '5'},
                                             'interval': 0}], 'loop_count': 1},
            'pump_settings': {'preflight': {'oil_axis': 'A', 'separation_turns': 2, 'separation_rpm': 20},
                              'default_speed': 60, 'injection_pump_policy': {
                                  'mode': 'survey', 'speed': 38, 'lead_time_s': 0, 'stop_on_finish': False}},
        }
        self.trigger._load_config = lambda: self.config

    def test_two_real_survey_acquisitions_each_prepare_then_inject_once_and_stop_in_gap(self):
        self.node.automation_engine.start = self.real_engine_start
        with patch.object(self.trigger_module.threading.Thread, 'start'):
            self.assertTrue(self.trigger._start_survey())
        self.assertEqual(self.commands, [])
        for _ in range(2):
            self.trigger._latest_global_position = gps_position()
            offset = len(self.commands)
            self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'started')
            self.join()
            self.node.automation_engine._thread.join(2)
            self.assertFalse(self.node.automation_engine.is_running())
            terminal = self.status()
            self.assertEqual(terminal['status'], 'finished')
            self.trigger._automation_status_cb(self.String(json.dumps(terminal)))
            commands = self.commands[offset:]
            self.assertLess(commands.index('AEFV20J720.000'), commands.index('PUMP:SET:38'))
            self.assertEqual(commands.count('PUMP:SET:38'), 1)
            self.assertFalse(self.node.inject_pump_enabled)
            self.assertFalse(self.trigger.is_sampling)
            self.assertTrue(self.trigger._survey_active)
        self.assertEqual([action for action, _ in self.requests], ['automation_start', 'automation_start'])
        self.assertTrue(self.trigger._stop_survey())
        self.assertEqual(self.requests[-1][0], 'automation_cleanup')

    def test_survey_stop_during_preflight_scopes_cleanup_and_does_not_enter_engine(self):
        self.trigger._survey_active = True
        self.block_phase = 'compensating'
        self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'started')
        self.wait_phase('compensating')
        owner = self.node.sampling_context['attempt_id']
        self.assertTrue(self.trigger._stop_survey())
        self.join()
        self.assertEqual(self.requests[-1], ('automation_cleanup', {'attempt_id': owner, 'reason': 'survey_stop'}))
        self.assertFalse(self.trigger._survey_active)
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertNotIn('PUMP:SET:38', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_stale_survey_stop_cannot_stop_new_owner_injection(self):
        self.trigger._survey_active = True
        self.trigger.is_sampling = True
        self.trigger.current_sampling_context = {'source': 'survey', 'attempt_id': 'old'}
        self.trigger._emit_sample_record = Mock()
        self.trigger._set_mission_state = Mock()
        self.assertTrue(self.node._execute_control_action('injection_on', {
            'source': 'web', 'attempt_id': 'new', 'speed': 60})[0])
        before = list(self.commands)
        self.assertTrue(self.trigger._stop_survey())
        self.assertEqual(self.commands, before)
        self.assertTrue(self.node.inject_pump_enabled)
        self.assertEqual(self.node.sampling_context['attempt_id'], 'new')
        self.trigger._set_mission_state.assert_not_called()
        self.assertEqual(self.trigger._emit_sample_record.call_args.args[1:], ('cancelled', 'cleanup_superseded'))

    def test_survey_cleanup_failure_is_rejected_and_keeps_controller_fault_latched(self):
        self.trigger._survey_active = True
        self.block_phase = 'compensating'
        self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'started')
        self.wait_phase('compensating')
        owner = self.node.sampling_context['attempt_id']
        self.node.stop_all_pumps = Mock(return_value=False)
        self.trigger._emit_sample_record = Mock()
        self.assertFalse(self.trigger._stop_survey())
        self.join()
        self.assertEqual(self.requests[-1], ('automation_cleanup', {'attempt_id': owner, 'reason': 'survey_stop'}))
        self.assertFalse(self.trigger._survey_active)
        self.assertFalse(self.trigger.is_sampling)
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.FAILED)
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertTrue(self.node._cleanup_failed)
        self.assertEqual(self.trigger._emit_sample_record.call_args.args[1:], ('failed', 'control_stop_failed'))
        self.node.automation_engine.start.assert_not_called()

    def test_qgc_rejected_pause_resume_leave_bridge_preparing_not_paused(self):
        bridge_module = load_trigger('preflight_chain_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(bridge_module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = bridge_module.USVMavlinkRouterBridge()
        self.trigger.mission_status_pub = ForwardPublisher(bridge._mission_status_cb)
        self.node.automation_status_pub = ForwardPublisher(bridge._automation_status_cb)
        self.trigger._send_command_ack = Mock()
        self.block_phase = 'compensating'
        self.assertTrue(self.trigger._do_manual_sample())
        self.wait_phase('compensating')
        for command in (31012, 31013):
            self.trigger._dispatch_command_long(command, 0, 0, 255, 190, 'test')
            self.trigger._send_command_ack.assert_called_with(
                command, self.trigger_module.MAV_RESULT_FAILED, 255, 190)
            self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['SAMPLING'])
            self.assertFalse(bridge._automation_paused)
            self.assertEqual(bridge._automation_step, 0)


if __name__ == '__main__':
    unittest.main()
