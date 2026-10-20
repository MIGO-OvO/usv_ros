"""Operator-visible stop result and recovery contract through production paths.

Covers the two P0 defects that made a failed run unrecoverable:

* readiness read a clock before the frame it compared against, so a frame
  accepted during the check rejected the check;
* a latched cleanup/fault from the previous run vetoed every later stop, and
  the success branch never released the latch.
"""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from measurement_fixtures import prepare_measurement, fresh_measurement
import test_field_transactions as field
import test_automation_preflight as preflight_fixture


class MeasurementSnapshotTests(unittest.TestCase):
    setUp = field.FieldTransactionTests.setUp

    def ready_node(self):
        self.node.serial_conn = SimpleNamespace(is_open=True)
        prepare_measurement(self.node)
        self.assertTrue(self.node._real_measurement_ready())

    def test_frame_newer_than_an_early_clock_sample_still_counts(self):
        """The reader may stamp a frame after the check sampled its clock.

        The old check read ``now`` first and then required
        ``last_valid <= now``, so a frame accepted in between made the check
        report "not ready" and preflight fail with spectrometer_not_ready.
        """
        self.ready_node()
        sampled_at = self.node._last_valid_spectro_at
        # Reader stamped the frame immediately after the check read its clock.
        self.node._last_valid_spectro_at = sampled_at + 0.001
        with patch.object(self.module.time, 'monotonic', return_value=sampled_at):
            self.assertTrue(self.node._real_measurement_ready())

    def test_genuinely_stale_or_simulated_frame_is_still_rejected(self):
        self.ready_node()
        stale_at = self.node._last_valid_spectro_at + self.node.measurement_timeout + 0.5
        with patch.object(self.module.time, 'monotonic', return_value=stale_at):
            self.assertFalse(self.node._real_measurement_ready())
            self.assertTrue(self.node._real_measurement_ready(allow_recovery=True))
        fresh_measurement(self.node)
        self.node.latest_spectro['simulated'] = True
        self.assertFalse(self.node._real_measurement_ready(allow_recovery=True))

    def test_old_session_frame_cannot_back_a_new_start_ack(self):
        self.ready_node()
        frame_at = self.node._last_valid_spectro_at
        self.node._spectro_start_ack_at = frame_at + 0.5
        self.assertFalse(self.node._real_measurement_ready())

    def test_mechanical_preflight_check_does_not_require_measurement(self):
        """Homing/360/separation must survive a stale detector reading."""
        self.node.sampling_context = {'attempt_id': 'current'}
        self.node._preflight_attempt_id = 'current'
        self.node._owner_last_seen = time.monotonic()
        self.node._preflight = self.module.AutomationPreflight(
            self.node._send_preflight_command, self.node._check_preflight_active,
            self.node._preflight_angles, self.node._publish_preflight_status)
        self.node.serial_conn = SimpleNamespace(is_open=True)
        self.node._preflight_connection = self.node.serial_conn
        self.node.spectro_config['enabled'] = True
        self.node._spectro_config_state = 'ready'
        self.assertFalse(self.node._real_measurement_ready())
        self.node._check_preflight_active(False)
        with self.assertRaises(self.module.PreflightError) as raised:
            self.node._check_preflight_active(True)
        self.assertEqual(raised.exception.reason, 'spectrometer_not_ready')


class ConfirmedStopTests(unittest.TestCase):
    setUp = field.FieldTransactionTests.setUp

    def connected(self):
        serial = field.FakeSerial()
        serial.open()
        self.node.serial_conn = serial
        self.node.spectro_config['enabled'] = True
        self.node._spectro_config_state = 'ready'
        self.node.stop_all_pumps = Mock(return_value=True)
        self.node._stop_injection_for_automation_policy = Mock(return_value=True)

    def failed_run_state(self):
        self.node._cleanup_failed = True
        self.node._prior_cleanup_failed = True
        self.node._controller_fault = 'spectrometer_stale'
        self.node.spectro_state = 'stale'
        self.node._set_terminal_reason('spectrometer_stale')
        self.node.automation_engine._cleanup_failed = True

    def test_confirmed_stop_wins_over_the_previous_run_latches(self):
        self.connected()
        self.failed_run_state()
        self.node._confirm_all_outputs_stopped = Mock(return_value=True)
        response = self.node._auto_stop_callback(None, confirm_stop=True)
        self.assertTrue(response.success, response.message)
        # Current device state: recoverable and ready for a new attempt.
        self.assertFalse(self.node._cleanup_failed)
        self.assertIsNone(self.node._controller_fault)
        self.assertFalse(self.node.automation_engine._cleanup_failed)
        # History is retained, not rewritten.
        self.assertTrue(self.node._prior_cleanup_failed)
        self.assertEqual(self.node._terminal_reason, 'spectrometer_stale')

    def test_unconfirmed_stop_still_fails_and_keeps_the_restriction(self):
        self.connected()
        self.failed_run_state()
        self.node._confirm_all_outputs_stopped = Mock(return_value=False)
        response = self.node._auto_stop_callback(None, confirm_stop=True)
        self.assertFalse(response.success)
        self.assertTrue(self.node._cleanup_failed)
        self.assertEqual(self.node._controller_fault, 'spectrometer_stale'
                         if self.node._controller_fault else 'cleanup_failed')

    def test_stop_write_failure_cannot_be_reported_as_a_stop(self):
        self.connected()
        self.node.stop_all_pumps = Mock(return_value=False)
        self.node._confirm_all_outputs_stopped = Mock(return_value=True)
        response = self.node._auto_stop_callback(None, confirm_stop=True)
        self.assertFalse(response.success)

    def test_non_recoverable_control_fault_survives_a_confirmed_stop(self):
        self.connected()
        self.node._controller_fault = 'watchdog_tripped'
        self.node._set_terminal_reason('watchdog_tripped')
        self.node._confirm_all_outputs_stopped = Mock(return_value=True)
        self.assertTrue(self.node._auto_stop_callback(None, confirm_stop=True).success)
        self.assertEqual(self.node._controller_fault, 'watchdog_tripped')

    def test_spectrometer_stop_reports_ads_and_output_scopes_separately(self):
        self.connected()
        self.node._automation_is_active = Mock(return_value=True)
        self.node._auto_stop_locked = Mock(
            return_value=self.module.TriggerResponse(success=False, message='outputs unconfirmed'))
        self.node._spectro_stop = Mock(return_value=(True, 'ADS_OK:STOP'))
        self.node._ensure_spectro_reply_sync = Mock(return_value=(True, ''))
        success, message, extra = self.node._execute_control_action('spectrometer_stop', {})
        self.assertFalse(success, 'unconfirmed outputs must not be reported as a full stop')
        self.assertTrue(extra['ads_stop'])
        self.assertFalse(extra['outputs_stop'])
        self.assertEqual(message, 'ADS_OK:STOP')

    def test_spectrometer_stop_does_not_claim_a_manual_injection_pump_stopped(self):
        self.connected()
        self.node.inject_pump_enabled = True
        self.node._automation_is_active = Mock(return_value=False)
        self.node._spectro_stop = Mock(return_value=(True, 'ADS_OK:STOP'))
        self.node._ensure_spectro_reply_sync = Mock(return_value=(True, ''))
        success, message, extra = self.node._execute_control_action('spectrometer_stop', {})
        # Stopping the detector on its own is a success for its own scope.
        self.assertTrue(success)
        self.assertTrue(extra['ads_stop'])
        # No automation output was stopped here, so no such claim is made.
        self.assertFalse(extra['outputs_stop'])
        self.assertIn('not stopped by this command', message)

    def test_recovered_stop_allows_a_new_measurement_baseline(self):
        """After a confirmed stop the detector recovery path must be open."""
        self.connected()
        self.failed_run_state()
        self.node._confirm_all_outputs_stopped = Mock(return_value=True)
        self.assertTrue(self.node._auto_stop_callback(None, confirm_stop=True).success)
        prepare_measurement(self.node)
        self.assertTrue(self.node._real_measurement_ready())


class PreflightStaleMeasurementTests(unittest.TestCase):
    """Mechanical preparation runs to the hand-off without a fresh sample."""

    def setUp(self):
        self.fixture = preflight_fixture.PreflightWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.node = self.fixture.node
        self.module = self.fixture.module

    def test_homing_and_compensation_run_while_the_detector_is_silent(self):
        # Every command stops refreshing the detector; the hand-off still fails
        # closed because formal acquisition needs real data.
        fixture = self.fixture
        self.node.sampling_context = {'attempt_id': 'preflight-current'}
        self.node._preflight_attempt_id = 'preflight-current'
        self.node._owner_last_seen = time.monotonic()
        self.node._preflight = self.module.AutomationPreflight(
            self.node._send_preflight_command, self.node._check_preflight_active,
            self.node._preflight_angles, self.node._publish_preflight_status)
        def silent_send(command):
            fixture.commands.append(command.strip())
            if command.strip() == 'PIDQUERY':
                self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
            elif command.strip() == 'PUMP:OFF':
                self.node._on_text_received('PUMP_OK:OFF')
            elif command.strip().startswith('PUMP:SET:'):
                self.node._on_text_received('PUMP_OK:SET=60,ON')
            elif 'R' in command and command.strip()[0] in 'XYZA':
                for axis, direction, delta, precision in __import__('re').findall(
                        r'([XYZA])E([FB])R([\d.]+)P([\d.]+)', command):
                    self.node._on_text_received(
                        'PID_START:%s,delta=%.1f,dir=%s,prec=%s' % (axis, float(delta), direction, precision))
                    self.node._on_text_received('PID_DONE:%s,abs=360.0,err=0.01' % axis)
                self.node._on_text_received('CMD_OK')
            return True
        self.node.send_command = silent_send
        started = time.monotonic()
        with patch.object(self.node, '_real_measurement_ready', return_value=False):
            with self.assertRaises(self.module.PreflightError) as raised:
                self.node._preflight.run(
                    ['X'], {'X': 123.0},
                    {'oil_axis': None, 'separation_turns': 0, 'separation_rpm': 5}, 0.1, (60, 0))
        self.assertEqual(raised.exception.reason, 'spectrometer_not_ready')
        self.assertLess(time.monotonic() - started, 5.0)
        sent = ''.join(fixture.commands)
        self.assertIn('XEBR27.000P0.1', sent)
        self.assertIn('XEFR360.000P0.1', sent)
        self.assertIn('PUMP:SET:60', fixture.commands)

    def test_background_freshness_cannot_cancel_a_slow_mechanical_preparation(self):
        """The real background check runs while the mechanical PRE blocks."""
        fixture = self.fixture
        node = self.node
        node.sampling_context = {'attempt_id': 'preflight-slow'}
        node._preflight_attempt_id = 'preflight-slow'
        node._owner_last_seen = time.monotonic()
        node._preflight = self.module.AutomationPreflight(
            node._send_preflight_command, node._check_preflight_active,
            node._preflight_angles, node._publish_preflight_status)
        # A send that acknowledges commands but never refreshes the detector.
        def silent_send(command):
            fixture.commands.append(command.strip())
            if command.strip() == 'PIDQUERY':
                node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
            elif command.strip() == 'PUMP:OFF':
                node._on_text_received('PUMP_OK:OFF')
            elif command.strip() == 'STOPALL':
                node._on_text_received('STOPALL_OK')
            return True
        node.send_command = silent_send
        # Runtime silence, not a cold start: the stale branch is the one that
        # would stop a running acquisition.
        node._spectro_start_ack_at = None
        node._spectro_first_frame_deadline = None
        node._last_valid_spectro_at = time.monotonic() - node.spectro_stale_stop_timeout - 1.0
        node._preflight.stage('homing')
        self.assertFalse(node._measurement_required_now(), 'homing must not read the detector')
        with patch.object(node, '_stop_stale_acquisition', wraps=node._stop_stale_acquisition) as stop:
            for _ in range(3):
                node._check_spectro_freshness()
        self.assertFalse(stop.called, 'mechanical preparation was cancelled by a detector time-out')
        self.assertTrue(node._preflight.snapshot()['active'])
        self.assertIsNone(node._terminal_reason)
        self.assertEqual(node.spectro_state, 'stale')
        # The same gate flips at the hand-off to formal acquisition.
        node._preflight.stage('verifying')
        self.assertTrue(node._measurement_required_now())
        self.assertIsNotNone(node._measurement_owner())

    def test_late_stale_check_cannot_cancel_the_task_that_replaced_it(self):
        """Ownership is verified under the same lock the stop path uses."""
        node = self.node
        node.sampling_context = {'attempt_id': 'attempt-A'}
        node.automation_engine._running.set()
        node.send_command = Mock(return_value=True)
        node._auto_stop_locked = Mock(
            return_value=self.module.TriggerResponse(success=True, message='stopped'))
        owner = node._measurement_owner()
        self.assertIsNotNone(owner)
        # Attempt A is stopped and attempt B legitimately starts.
        node._request_output_stop()
        node._finish_output_stop()
        node.sampling_context = {'attempt_id': 'attempt-B'}
        node.automation_engine._running.set()
        self.assertFalse(node._stop_stale_acquisition(owner, 'spectrometer_stale'))
        node._auto_stop_locked.assert_not_called()
        self.assertEqual(node.sampling_context['attempt_id'], 'attempt-B')
        self.assertTrue(node.automation_engine._running.is_set())


class PayloadStatusAfterStopTests(unittest.TestCase):
    """USV_STAT must describe the current device state, not the last failure."""

    def setUp(self):
        from test_mavlink_command_compat import _load_script as load_trigger
        self.module = load_trigger('stop_recovery_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(self.module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = self.module.USVMavlinkRouterBridge()

    def feed(self, payload):
        self.bridge._automation_status_cb(SimpleNamespace(data=json.dumps(payload)))
        return self.bridge._status_code

    def test_failure_keeps_failed_until_a_stop_is_confirmed(self):
        codes = self.module.USVMavlinkRouterBridge._MISSION_STATE_CODES
        self.assertEqual(self.feed({
            'status': 'preflight', 'running': True,
            'preflight': {'active': True, 'phase': 'homing'}}), codes['WAITING_STABLE'])
        self.assertEqual(self.feed({
            'status': 'failed', 'running': False, 'preflight': {'active': False},
            'terminal_reason': 'spectrometer_not_ready'}), codes['FAILED'])
        # Mission status left the cursor on a preparation phase; that stale
        # phase used to keep FAILED latched after the stop was confirmed.
        self.bridge._mission_status_cb(SimpleNamespace(data='WAITING_STABLE'))
        self.assertEqual(self.bridge._status_code, codes['WAITING_STABLE'])
        self.assertEqual(self.feed({
            'status': 'stopped', 'running': False, 'preflight': {'active': False},
            'terminal_reason': 'spectrometer_not_ready'}), codes['IDLE'])
        self.assertEqual(self.bridge._last_mission_state, 'IDLE')

    def test_stopped_acquisition_is_reported_separately_from_the_task_state(self):
        codes = self.module.USVMavlinkRouterBridge._MISSION_STATE_CODES
        self.feed({'status': 'stopped', 'running': False, 'ads_state': 1,
                   'preflight': {'active': False}})
        # "ADS stopped" is its own field; it must not become a link or task fault.
        self.assertEqual(self.bridge._ads_state, 1)
        self.assertEqual(self.bridge._status_code, codes['IDLE'])


if __name__ == '__main__':
    unittest.main()
