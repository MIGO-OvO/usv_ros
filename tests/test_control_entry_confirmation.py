"""Remote commands cannot acknowledge fire-and-forget or revive a stopped task."""
import unittest

from test_mavlink_command_compat import _load_script


class ControlEntryConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('entry_confirmation_trigger', 'scripts/mavlink_trigger_node.py')
        self.node = self.module.MAVLinkTriggerNode()

    def test_spectrometer_and_calibration_use_confirmed_control_action(self):
        calls = []
        self.node._call_control_command = lambda action, payload=None: calls.append(action) or False
        for command in (31018, 31019, 31014):
            self.assertFalse(self.node.handle_mavlink_command(command))
        self.assertEqual(calls, ['spectrometer_start', 'spectrometer_stop', 'calibration_start'])

    def test_baseline_propagates_actual_rejection(self):
        self.node._call_control_command = lambda *args: False
        self.assertFalse(self.node.handle_mavlink_command(31017, 1.2))

    def test_late_pause_resume_does_not_override_stop(self):
        for action in ('pause', 'resume'):
            self.node.current_sampling_context = {'attempt_id': 'old'}
            self.node.is_sampling = True
            def stop_during_rpc(_):
                self.node.current_sampling_context = None
                self.node.is_sampling = False
                self.node.current_mission_state = self.module.MissionState.IDLE
                return True
            self.node._call_automation_service = stop_during_rpc
            self.assertFalse(getattr(self.node, '_' + action + '_sampling')())
            self.assertEqual(self.node.current_mission_state, self.module.MissionState.IDLE)

    def test_stop_keeps_first_failure_and_adds_cleanup_failure(self):
        emitted = []
        self.node._emit_sample_record = lambda context, outcome, reason: emitted.append((context, outcome, reason))
        self.node._publish_sampling_result = lambda *args: None
        context = {'source': 'manual', 'terminal_reason': 'spectrometer_stale', 'cancel_reason': 'operator_stop'}
        self.node._stop_sampling_context(context, False, False, False, stop_result=False)
        self.assertEqual(emitted[0][2], 'spectrometer_stale')
        self.assertTrue(emitted[0][0]['cleanup_failed'])


if __name__ == '__main__':
    unittest.main()
