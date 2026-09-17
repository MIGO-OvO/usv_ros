"""Cross-entry regressions. No real ROS, serial port or vehicle is used."""
import json
import struct
import types
import unittest
from unittest.mock import patch

from test_mavlink_command_compat import _load_script as load_mav, FakeConnection, RecordingMav
from test_hardware_runtime_sync import _load_script as load_runtime


class BridgeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_mav('bridge_system_safety', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(self.module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = self.module.USVMavlinkRouterBridge()

    def test_sensor_silence_expires_valid_even_when_bridge_keeps_running(self):
        with patch.object(self.module.time, 'monotonic', return_value=10.0):
            self.bridge._voltage_cb(types.SimpleNamespace(data=json.dumps({'voltage': 1.2, 'valid': True})))
        with patch.object(self.module.time, 'monotonic', return_value=20.0), \
                patch.object(self.module.rospy, 'is_shutdown', side_effect=[False, True]), \
                patch.object(self.bridge, '_receive_mavlink_messages'), \
                patch.object(self.bridge, '_send_heartbeat'), \
                patch.object(self.bridge, '_send_payload') as send:
            self.bridge.run()
        self.assertEqual(send.call_args.args[12], 0.0)

    def test_ack_callback_never_writes_shared_socket(self):
        with patch.object(self.bridge, '_send_command_ack') as send:
            self.bridge._cmd_ack_cb(types.SimpleNamespace(data=[31010, 0, 255, 190]))
        send.assert_not_called()
        self.assertEqual(list(self.bridge._pending_acks), [(31010, 0, 255, 190)])

    def test_nonfinite_voltage_cannot_be_valid(self):
        self.bridge._voltage_cb(types.SimpleNamespace(data=json.dumps({'voltage': float('nan'), 'valid': True})))
        self.assertEqual(self.bridge._spectrometer_valid, 0.0)

    def test_cancellation_emits_failure_not_completion_on_router(self):
        self.bridge._fcu_sample_id = 42
        self.bridge._sampling_result_cb(types.SimpleNamespace(data=json.dumps(
            {'source': 'fcu', 'sample_id': 42, 'outcome': 'cancelled'})))
        mav = RecordingMav()
        self.bridge._conn = types.SimpleNamespace(mav=mav)
        with patch.object(self.module.rospy, 'is_shutdown', side_effect=[False, True]), \
                patch.object(self.bridge, '_receive_mavlink_messages'), \
                patch.object(self.bridge, '_send_heartbeat'), patch.object(self.bridge, '_send_payload'), \
                patch.object(self.bridge, '_send_statustext'):
            self.bridge.run()
        self.assertEqual(mav.named_values, [('USV_FAIL', 42.0)])

    def test_foreign_vehicle_cannot_trigger_sampling(self):
        frame = types.SimpleNamespace(name='USV_SMPL', value=42,
                                      get_type=lambda: 'NAMED_VALUE_FLOAT',
                                      get_srcSystem=lambda: 2, get_srcComponent=lambda: 1)
        self.bridge._conn = FakeConnection([frame])
        self.bridge._receive_mavlink_messages()
        self.assertEqual(self.bridge._cmd_rx_pub.messages, [])


class PumpSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module, self.publishers, _ = load_runtime('pump_system_safety', 'scripts/pump_control_node.py')
        self.node = self.module.PumpControlNode()
        self.sent = []
        self.node.send_command = lambda cmd: self.sent.append(cmd) or True
        self.node.automation_engine.send_command = self.node.send_command
        self.node._send_injection_pump_command = lambda **kwargs: True
        self.node.automation_engine._thread = types.SimpleNamespace(
            is_alive=lambda: self.node.automation_engine._running.is_set(), join=lambda **kwargs: None)

    def test_all_stop_entries_cancel_automation_not_only_motor_output(self):
        for source in ('control', 'topic'):
            with self.subTest(source=source):
                self.node.automation_engine._running.set()
                if source == 'control':
                    self.node._execute_control_action('manual_stop_all', {})
                else:
                    self.node._cmd_callback(types.SimpleNamespace(data='STOP'))
                self.assertFalse(self.node.automation_engine.is_running())

    def test_raw_calibration_cannot_override_active_automation(self):
        self.node.automation_engine._running.set()
        self.node._cmd_callback(types.SimpleNamespace(data='CALXYZA'))
        self.assertEqual(self.sent, [])

    def test_synthetic_frame_cannot_claim_real_measurement_validity(self):
        received = []
        reader = self.module.PumpSerialReader(None)
        reader.on_spectro_received = received.append
        frame = struct.pack('<BBIBBifBB', 0x55, 0xDD, 10, 2, 0x11, 123, 1.2, 0, 10)
        reader._parse_spectro_packet(frame)
        self.assertFalse(received[0]['valid'])
        self.assertTrue(received[0]['simulated'])

    def test_invalid_frame_invalidates_previously_published_average(self):
        self.node._emit_spectro_average([{'voltage': 1.2, 'valid': True, 'timestamp_ms': 1}])
        self.node._on_spectro_received({'voltage': 0.0, 'valid': False, 'i2c_error': True})
        latest = json.loads(self.publishers['/usv/spectrometer_voltage'].messages[-1].data)
        self.assertFalse(latest['valid'])
        self.assertFalse(self.node.latest_spectro['valid'])

    def test_start_transaction_loads_exact_steps_and_context_before_start(self):
        payload = {'steps': [{'name': 'new'}], 'sample_id': 42, 'source': 'fcu', 'waypoint_seq': 3, 'attempt_id': 'attempt-42'}
        observations = []
        def start():
            observations.append((self.node.automation_engine.steps, dict(self.node.sampling_context)))
            return True
        with patch.object(self.node.automation_engine, 'start', side_effect=start):
            ok, _, _ = self.node._execute_control_action('automation_start', payload)
        self.assertTrue(ok)
        self.assertEqual(observations[0][0], payload['steps'])
        self.assertEqual(observations[0][1]['sample_id'], 42)

    def test_invalid_start_payload_never_starts_old_steps(self):
        self.node.automation_engine.steps = [{'name': 'old'}]
        with patch.object(self.node.automation_engine, 'start') as start:
            ok, _, _ = self.node._execute_control_action('automation_start', {'steps': 'bad'})
        self.assertFalse(ok)
        start.assert_not_called()

    def test_watchdog_trip_cancels_and_latches_until_reconnect(self):
        self.node.automation_engine._running.set()
        self.node._on_text_received('WATCHDOG_TRIPPED')
        self.assertFalse(self.node.automation_engine.is_running())
        ok, _, _ = self.node._execute_control_action('injection_on', {'speed': 30})
        self.assertFalse(ok)

    def test_step_topic_cannot_mutate_running_configuration(self):
        self.node.automation_engine.steps = [{'name': 'running'}]
        self.node.automation_engine._running.set()
        self.assertFalse(self.node._steps_callback(types.SimpleNamespace(data=json.dumps({'steps': [{'name': 'replacement'}]}))))
        self.assertEqual(self.node.automation_engine.steps, [{'name': 'running'}])

    def test_stale_measurement_cancels_active_sampling(self):
        self.node.automation_engine._running.set()
        self.node._last_valid_spectro_at = 10.0
        with patch.object(self.module.time, 'monotonic', return_value=20.0):
            self.node._check_spectro_freshness()
        self.assertFalse(self.node.automation_engine.is_running())

    def test_reconnect_cancels_motion_before_arming_new_device_session(self):
        self.node.automation_engine._running.set()
        observed = []
        self.node.disconnect = lambda: None
        self.node.connect = lambda: observed.append(self.node.automation_engine.is_running()) or True
        result = self.node._reconnect_callback(None)
        self.assertTrue(result.success)
        self.assertEqual(observed, [False])

    def test_raw_commands_cannot_rearm_latched_session(self):
        self.node._controller_fault = 'watchdog_tripped'
        for command in ('WATCHDOG:ARM', 'WATCHDOG:KEEPALIVE', 'HELLO?\nWATCHDOG:ARM', 'CALXYZA'):
            self.node._cmd_callback(types.SimpleNamespace(data=command))
        self.assertEqual(self.sent, [])

    def test_old_owner_heartbeat_cannot_keep_new_sampling_alive(self):
        self.node.sampling_context = {'source': 'fcu', 'sample_id': 42, 'attempt_id': 'new'}
        self.node._owner_last_seen = 10.0
        self.node.automation_engine._running.set()
        with patch.object(self.module.time, 'monotonic', return_value=20.0):
            self.node._owner_heartbeat_cb(types.SimpleNamespace(data='{"attempt_id":"old"}'))
            self.node._check_sampling_owner()
        self.assertFalse(self.node.automation_engine.is_running())
        self.assertEqual(self.node._controller_fault, 'owner_lost')

    def test_legacy_motion_entries_respect_latched_fault(self):
        self.node._controller_fault = 'owner_lost'
        step = {'X': {'enable': 'E', 'direction': 'F', 'speed': 1, 'angle': 10}}
        self.node._step_callback(types.SimpleNamespace(data=json.dumps(step)))
        self.assertEqual(self.sent, [])
        self.assertFalse(self.node._injection_on_callback(None).success)

    def test_legacy_start_cannot_replay_unowned_old_configuration(self):
        self.node.automation_engine.steps = [{'name': 'old'}]
        with patch.object(self.node.automation_engine, 'start') as start:
            self.assertFalse(self.node._auto_start_callback(None).success)
        start.assert_not_called()


class ModeConfirmationTests(unittest.TestCase):
    def test_mode_sent_without_observed_hold_is_not_success(self):
        module = load_mav('trigger_mode_safety', 'scripts/mavlink_trigger_node.py')
        node = module.MAVLinkTriggerNode()
        node.set_mode_client = lambda req: types.SimpleNamespace(mode_sent=True)
        node.mavros_state = types.SimpleNamespace(connected=True, mode='AUTO')
        self.assertFalse(node.set_mode('HOLD'))


if __name__ == '__main__':
    unittest.main()
