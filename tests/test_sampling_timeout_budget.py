"""Timeout tolerance must not turn missing/failed hardware feedback into success."""
import types
import unittest
from unittest.mock import Mock, patch

from scripts.lib.automation_engine import AutomationEngine
from scripts.lib.automation_preflight import AutomationPreflight, PreflightError
import test_field_transactions as field
from measurement_fixtures import prepare_measurement


class TimeoutBudgetTests(unittest.TestCase):
    def test_engine_allows_delayed_done_but_still_has_absolute_deadline(self):
        for finish_at in (90, None):
            with self.subTest(finish_at=finish_at):
                engine = AutomationEngine(Mock(), Mock(return_value=True), Mock())
                engine._running.set()
                engine._pending_pid_motors = {'X'}
                now = [0.0]
                def tick(timeout):
                    now[0] += 30
                    if finish_at is not None and now[0] >= finish_at:
                        engine.notify_pid_complete('X')
                engine._pid_complete_event = Mock()
                engine._pid_complete_event.wait.side_effect = tick
                clock = types.SimpleNamespace(monotonic=lambda: now[0])
                with patch('scripts.lib.automation_engine.time', clock):
                    self.assertEqual(engine._wait_for_pid_complete(), finish_at is not None)
                if finish_at is None:
                    self.assertTrue(engine._failed)
                    self.assertIn('120s', engine._last_error)

    def test_preflight_waits_for_real_done_after_old_deadline(self):
        runner = AutomationPreflight(Mock(), Mock(), Mock(), Mock())
        runner.command = lambda _, **kwargs: runner.notify_text('PID_START:X,delta=360.0,dir=F')
        now = [0.0]
        def tick(_timeout):
            now[0] += 30
            if now[0] >= 90:
                runner.notify_text('PID_DONE:X')
        runner.changed = Mock()
        runner.changed.wait.side_effect = tick
        with patch('scripts.lib.automation_preflight.time.monotonic', side_effect=lambda: now[0]):
            runner.pid_moves({'X': (360.0, 'F')}, 0.1)
        self.assertEqual(now[0], 90)

    def test_oil_startup_slack_still_rejects_stall_and_missing_angles(self):
        runner = AutomationPreflight(Mock(), Mock(), Mock(), Mock())
        runner.travel = {'axis': 'A', 'angle': 0, 'at': 3.0, 'degrees': 0,
                         'rpm': 5, 'progress_at': 0.0, 'progress_degrees': 0}
        with patch('scripts.lib.automation_preflight.time.monotonic', return_value=3.0):
            runner._check()  # Old 2-second no-progress deadline rejected this.
        runner.travel['at'] = 5.1
        with patch('scripts.lib.automation_preflight.time.monotonic', return_value=5.1):
            with self.assertRaisesRegex(PreflightError, 'forward progress'):
                runner._check()
        runner.travel.update(at=0.0, progress_at=1.1)
        with patch('scripts.lib.automation_preflight.time.monotonic', return_value=1.1):
            with self.assertRaisesRegex(PreflightError, 'angle feedback timeout'):
                runner._check()

    def test_oil_pid_segments_respect_the_firmware_deadline(self):
        runner = AutomationPreflight(Mock(return_value=True), Mock(),
                                     lambda axes, **kw: {'A': 0}, Mock())
        runner.command = Mock(return_value='PIDPARAM:0.14,0.015,0.06,1,8')
        def finish(moves, precision, before_send=None, timeout=None):
            if before_send:
                before_send()
                runner.travel['degrees'] = moves['A'][0]
        runner.pid_moves = Mock(side_effect=finish)
        runner.run([], {}, {'oil_axis': 'A', 'separation_turns': 1, 'separation_rpm': 5}, 0.1, None)
        segments = [call for call in runner.pid_moves.call_args_list if call.kwargs.get('before_send')]
        self.assertEqual(len(segments), 4)
        self.assertTrue(all(call.kwargs['timeout'] == 30.0 for call in segments))


class SpectroTimeoutBudgetTests(unittest.TestCase):
    setUp = field.FieldTransactionTests.setUp

    def test_short_gap_invalidates_data_without_aborting_then_long_gap_stops(self):
        self.node.sampling_context = {'attempt_id': 'stale-owner'}
        self.node.automation_engine._running.set()  # formal acquisition
        self.node.send_command = Mock(return_value=True)
        self.node._auto_stop_locked = Mock(
            return_value=self.module.TriggerResponse(success=True, message='stopped'))
        self.node._last_valid_spectro_at = 100.0
        self.node.latest_spectro = {'valid': True}
        with patch.object(self.module.time, 'monotonic', return_value=103.0):
            self.node._check_spectro_freshness()
        self.assertFalse(self.node.latest_spectro['valid'])
        self.assertEqual(self.node.spectro_state, 'stale')
        self.node._auto_stop_locked.assert_not_called()
        with patch.object(self.module.time, 'monotonic', return_value=111.0):
            self.node._check_spectro_freshness()
        # The background check stops the attempt it owns, judged by its own
        # confirmed stop evidence, rather than issuing a blind global STOP.
        self.node._auto_stop_locked.assert_called_once()
        self.assertTrue(self.node._auto_stop_locked.call_args.kwargs['confirm_stop'])
        self.assertEqual(self.node._terminal_reason, 'spectrometer_stale')

    def test_sample_wait_recovers_with_new_frame_after_old_two_second_deadline(self):
        prepare_measurement(self.node)
        self.node._spectro_start_ack_at = 98.0
        self.node._spectro_reference_context = self.node._spectro_reference_signature()
        self.node.spectro_state = 'stale'
        self.node._last_valid_spectro_at = 99.0
        self.node.latest_spectro = {'valid': False, 'status': 'stale', 'stale': True}
        self.node.automation_engine.is_running = Mock(return_value=True)
        self.node.automation_engine.is_paused = Mock(return_value=False)
        now = [102.0]
        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 105.0:
                self.node.spectro_state = 'acquiring'
                self.node._last_valid_spectro_at = now[0]
                self.node.latest_spectro = {'valid': True}
                self.node._latest_spectro_received_at = now[0]
        clock = types.SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
        with patch.object(self.module, 'time', clock):
            self.assertTrue(self.node._wait_for_new_spectro_sample(99.0))
        self.assertGreaterEqual(now[0], 105.0)

    def test_warm_start_has_same_bounded_grace_as_cold_start(self):
        self.node._spectro_cold_start_pending = False
        self.assertEqual(self.node._first_spectro_frame_timeout(), 4.0)
        self.assertEqual(self.node.measurement_timeout, 2.0)

    def test_pid_parameter_is_consumed_by_engine_and_preflight_start(self):
        original = self.module.rospy.get_param
        def param(name, default=None):
            return 180.0 if name == '/automation/pid_wait_timeout' else original(name, default)
        with patch.object(self.module.rospy, 'get_param', side_effect=param), \
                patch.object(self.module, 'InjectionPumpWorker', return_value=None):
            node = self.module.PumpControlNode()
        self.assertEqual(node.automation_engine.PID_WAIT_TIMEOUT, 180.0)
        prepare_measurement(node)
        node._reject_if_manual_mode = Mock(return_value=None)
        node.automation_engine.steps = [{}]
        node._configuration_ready = True
        node._owner_last_seen = self.module.time.monotonic()
        with patch.object(self.module.threading, 'Thread'):
            self.assertTrue(node._auto_start_locked(None).success)
        self.assertEqual(node._preflight.PID_TIMEOUT, 180.0)


if __name__ == '__main__':
    unittest.main()
