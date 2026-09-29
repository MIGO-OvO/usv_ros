"""Unified automation terminal reason contract.

Covers the field-visible stop paths: spectrometer startup vs runtime stale,
watchdog, owner heartbeat, PID DONE/TIMEOUT/FAIL semantics, operator stop and
normal completion. Every path must produce a structured, non-guessed
terminal_reason instead of a generic "Automation stopped".
"""
import json
import threading
import time
import unittest
from unittest.mock import Mock

from test_hardware_runtime_sync import _load_script


class TerminalReasonTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pubs, self.String = _load_script(
            'pump_terminal_reasons', 'scripts/pump_control_node.py')
        self.node = self.module.PumpControlNode()
        self.node._refresh_runtime_settings = Mock()
        self.module.rospy.is_shutdown = lambda: False
        self.terminal_logs = []
        self._orig_logwarn = self.module.rospy.logwarn
        self.module.rospy.logwarn = self._capture_logwarn
        self.addCleanup(self.node.disconnect)
        self.addCleanup(self.node.injection_pump_worker.stop)

    def tearDown(self):
        self.module.rospy.logwarn = self._orig_logwarn

    def _capture_logwarn(self, msg, *args):
        try:
            self.terminal_logs.append(msg % args if args else msg)
        except (TypeError, ValueError):
            self.terminal_logs.append(str(msg))

    def activate_automation(self):
        """Mark the engine as active the same way a live worker thread would."""
        stop_flag = threading.Event()
        worker = threading.Thread(target=stop_flag.wait, args=(30,), daemon=True)
        worker.start()
        self.addCleanup(stop_flag.set)
        self.addCleanup(lambda: worker.join(timeout=2))
        self.node.automation_engine._running.set()
        self.node.automation_engine._thread = worker
        self.node.sampling_context = {
            'source': 'web', 'attempt_id': 'attempt-1',
        }
        self.node._owner_last_seen = time.monotonic()
        self.node.stop_all_pumps = Mock(return_value=True)
        self.node._stop_injection_for_automation_policy = Mock()
        return worker

    def automation_status_payloads(self):
        publisher = self.pubs.get('/usv/automation_status')
        if publisher is None:
            return []
        return [json.loads(msg.data) for msg in publisher.messages]

    # ------------------------------------------------------------------
    # PID semantics
    # ------------------------------------------------------------------
    def test_pid_done_completes_pending_motor(self):
        self.node.automation_engine._pending_pid_motors = {'X'}
        self.node._on_text_received('PID_DONE:X,abs=360.0,err=0.05')
        self.assertEqual(self.node.automation_engine._pending_pid_motors, set())
        self.assertIsNone(self.node._terminal_reason)

    def test_pid_timeout_is_not_completion(self):
        self.activate_automation()
        engine = self.node.automation_engine
        engine._pending_pid_motors = {'X'}
        self.node._on_text_received('PID_TIMEOUT:X,abs=360.0,err=5.0')
        self.assertTrue(engine.get_status()['failed'])
        self.assertEqual(self.node._terminal_reason, 'pid_timeout')

    def test_pid_fail_is_not_completion(self):
        self.activate_automation()
        engine = self.node.automation_engine
        engine._pending_pid_motors = {'A'}
        self.node._on_text_received('PID_FAIL:A')
        self.assertTrue(engine.get_status()['failed'])
        self.assertEqual(self.node._terminal_reason, 'pid_fail')
        # PID_FAIL with payload details still resolves the motor.
        engine._pending_pid_motors = {'A'}
        engine._failed = False
        engine._running.set()
        self.node._terminal_reason = None
        self.node._on_text_received('PID_FAIL:A,abs=10.0,reason=stall')
        self.assertEqual(self.node._terminal_reason, 'pid_fail')

    def test_engine_pid_wait_timeout_maps_to_pid_timeout_reason(self):
        self.activate_automation()
        self.node._on_automation_error('PID 等待超时 (60s)，未完成电机: {\'X\'}')
        self.assertEqual(self.node._terminal_reason, 'pid_timeout')

    # ------------------------------------------------------------------
    # Spectrometer freshness
    # ------------------------------------------------------------------
    def test_runtime_stale_stops_automation_with_spectrometer_stale(self):
        self.activate_automation()
        self.node.spectro_config['enabled'] = True
        self.node.spectro_state = 'acquiring'
        self.node._last_valid_spectro_at = time.monotonic() - 10.0
        self.node._spectro_start_ack_at = None
        self.node._check_spectro_freshness()
        self.assertEqual(self.node.spectro_state, 'stale')
        self.assertEqual(self.node._terminal_reason, 'spectrometer_stale')

    def test_startup_without_frames_uses_frame_timeout_reason(self):
        self.activate_automation()
        self.node.spectro_config['enabled'] = True
        self.node.spectro_state = 'starting'
        self.node._spectro_start_ack_at = time.monotonic() - 10.0
        self.node._last_valid_spectro_at = None
        self.node._check_spectro_freshness()
        self.assertEqual(self.node._terminal_reason, 'spectrometer_frame_timeout')

    def test_start_ack_never_becomes_measurement_freshness(self):
        self.node._last_valid_spectro_at = None
        self.node._on_text_received('ADS_OK:START')
        self.assertEqual(self.node.spectro_state, 'starting')
        self.assertIsNone(self.node._last_valid_spectro_at)
        self.assertEqual(self.node._spectro_txn_phase, 'idle')

    # ------------------------------------------------------------------
    # Watchdog / serial / owner
    # ------------------------------------------------------------------
    def test_watchdog_trip_records_watchdog_tripped_reason(self):
        self.activate_automation()
        self.node._on_text_received('WATCHDOG_TRIPPED')
        self.assertEqual(self.node._controller_fault, 'watchdog_tripped')
        self.assertEqual(self.node._terminal_reason, 'watchdog_tripped')

    def test_owner_heartbeat_requires_matching_attempt(self):
        self.activate_automation()
        self.node._owner_last_seen = time.monotonic() - 10.0
        stale_seen = self.node._owner_last_seen
        # Old attempt heartbeat must not renew the lease.
        self.node._owner_heartbeat_cb(self.String(json.dumps({'attempt_id': 'old-attempt'})))
        self.assertEqual(self.node._owner_last_seen, stale_seen)
        self.node._owner_heartbeat_cb(self.String(json.dumps({'attempt_id': 'attempt-1'})))
        self.assertGreater(self.node._owner_last_seen, stale_seen)

    def test_owner_silence_stops_automation_with_owner_lost(self):
        self.activate_automation()
        self.node._owner_last_seen = time.monotonic() - 10.0
        self.node._check_sampling_owner()
        self.assertEqual(self.node._terminal_reason, 'owner_lost')
        self.assertEqual(self.node._controller_fault, 'owner_lost')

    # ------------------------------------------------------------------
    # Stop / completion lifecycle
    # ------------------------------------------------------------------
    def test_operator_stop_when_no_fault_pre_registered(self):
        self.activate_automation()
        self.node._auto_stop_callback(None)
        self.assertEqual(self.node._terminal_reason, 'operator_stop')
        terminal = [line for line in self.terminal_logs if '[AUTOMATION TERMINAL]' in line]
        self.assertTrue(terminal)
        self.assertIn('reason=operator_stop', terminal[-1])
        self.assertIn('attempt_id=attempt-1', terminal[-1])

    def test_fault_reason_survives_following_operator_stop(self):
        self.activate_automation()
        self.node._set_terminal_reason('watchdog_tripped')
        self.node._auto_stop_callback(None)
        # The root cause recorded by the watchdog handler must win.
        self.assertEqual(self.node._terminal_reason, 'watchdog_tripped')

    def test_finished_run_records_completed(self):
        self.activate_automation()
        self.node._on_automation_status('finished')
        self.assertEqual(self.node._terminal_reason, 'completed')

    def test_new_attempt_resets_terminal_reason(self):
        self.node._set_terminal_reason('owner_lost')
        self.node._steps_callback(self.String(json.dumps({
            'steps': [{'name': 's', 'X': {'enable': 'D'}, 'Y': {'enable': 'D'},
                       'Z': {'enable': 'D'}, 'A': {'enable': 'D'}, 'interval': 100}],
            'loop_count': 1, 'source': 'web', 'attempt_id': 'attempt-2',
        })))
        self.assertIsNone(self.node._terminal_reason)

    def test_automation_status_payload_exposes_terminal_diagnostics(self):
        self.node._set_terminal_reason('spectrometer_stale')
        self.node._last_valid_spectro_at = time.monotonic() - 3.0
        self.node._owner_last_seen = time.monotonic() - 1.0
        self.node._publish_automation_status('stopped')
        payload = self.automation_status_payloads()[-1]
        self.assertEqual(payload['terminal_reason'], 'spectrometer_stale')
        self.assertIsInstance(payload['spectrometer_age_s'], float)
        self.assertIsInstance(payload['owner_age_s'], float)
        self.assertIn('serial_connected', payload)


if __name__ == '__main__':
    unittest.main()
