"""Cold-start regressions; real engine and pump hooks with no serial hardware."""
import json
import unittest
from unittest.mock import Mock, patch

from test_hardware_runtime_sync import _load_script


class AutomationColdStartTests(unittest.TestCase):
    def setUp(self):
        self.module, self.publishers, _ = _load_script(
            'pump_cold_start', 'scripts/pump_control_node.py')
        self.node = self.module.PumpControlNode()
        self.node.send_command = Mock(return_value=True)
        self.node.automation_engine.send_command = self.node.send_command
        self.step = {'name': 'cold start', 'interval': 0,
                     'A': {'enable': 'E', 'direction': 'F', 'speed': '8', 'angle': '1440'}}

    def test_first_real_step_without_spectrometer_frames_cannot_move(self):
        self.node.pid_mode = False
        self.node.automation_engine._running.set()
        self.assertFalse(self.node._send_automation_step(self.step))
        self.node.send_command.assert_not_called()

    def test_configured_lab_cold_start_cannot_report_real_completion(self):
        self.node.lab_mode_enabled = True
        self.node.spectro_state = 'configured'
        self.node.pid_mode = False
        self.node.automation_engine.set_steps([self.step])
        with patch.object(self.node, '_wait_seconds_with_pause', return_value=True):
            self.assertTrue(self.node.automation_engine.start())
            self.node.automation_engine._thread.join(2)
        self.assertFalse(self.node.automation_engine.is_running())
        statuses = [json.loads(m.data) for m in self.publishers['/usv/automation_status'].messages]
        self.assertFalse(any(s['status'] == 'finished' for s in statuses))
        self.assertEqual(statuses[-1]['terminal_reason'], 'spectrometer_not_ready')
        self.assertFalse(any(call.args and call.args[0].startswith('AEF') for call in self.node.send_command.call_args_list))

    def test_mechanical_debug_uses_explicit_manual_control_without_sample_success(self):
        self.node.manual_mode_enabled = True
        ok, _, _ = self.node._execute_control_action('manual_step', {'step': self.step})
        self.assertTrue(ok)
        self.node.send_command.assert_called_once()
        statuses = [json.loads(m.data) for m in self.publishers['/usv/automation_status'].messages]
        self.assertFalse(any(s['status'] == 'finished' for s in statuses))

    def test_real_mode_without_acquisition_still_rejects_sample_wait(self):
        self.node.lab_mode_enabled = False
        self.node.spectro_state = 'configured'
        self.assertFalse(self.node._wait_for_new_spectro_sample(None))

    def test_first_valid_average_sets_timestamp(self):
        self.node.automation_engine._running.set()
        self.node._send_automation_step(self.step)
        with patch.object(self.module.time, 'time', return_value=1234.5):
            self.node._emit_spectro_average([{'voltage': 1.2, 'valid': True}])
        self.assertEqual(self.node._latest_spectro_received_at, 1234.5)
