"""Web automation hands manual outputs over under the real pump control lock."""
import json
import threading
import unittest
from unittest.mock import patch

import test_automation_preflight as preparation_tests


class WebAutomationHandoffTests(unittest.TestCase):
    setUp = preparation_tests.PreflightWorkerTests.setUp
    stop = preparation_tests.PreflightWorkerTests.stop
    send = preparation_tests.PreflightWorkerTests.send
    start = preparation_tests.PreflightWorkerTests.start
    join = preparation_tests.PreflightWorkerTests.join
    wait_phase = preparation_tests.PreflightWorkerTests.wait_phase

    def test_web_start_confirms_stop_then_leaves_manual_before_any_preparation_motion(self):
        self.node.manual_mode_enabled = True
        self.node.inject_pump_enabled = True
        self.block_phase = 'homing'
        self.assertTrue(self.start()[0])
        self.wait_phase('homing')
        self.assertEqual(self.commands[:2], ['PIDQUERY', 'STOPALL'])
        self.assertFalse(self.node.manual_mode_enabled)
        self.assertFalse(self.node.inject_pump_enabled)
        statuses = self.pubs['/usv/manual_status'].messages
        self.assertFalse(json.loads(statuses[-1].data)['enabled'])
        self.assertFalse(self.node._execute_control_action('manual_mode', {'enabled': True})[0])
        count = len(self.commands)
        self.assertFalse(self.node._execute_control_action('manual_step', {'axis': 'Y', 'angle': 10})[0])
        self.assertEqual(len(self.commands), count)

    def test_unconfirmed_manual_stop_blocks_preparation_and_latches_cleanup_failure(self):
        self.node.manual_mode_enabled = True
        sender = self.node.send_command

        def send(command):
            if command.strip() == 'STOPALL':
                self.commands.append('STOPALL')
                self.node._on_text_received('CMD_ERR:STOP_FAILED')
                return True
            return sender(command)

        self.node.send_command = send
        ok, message, _ = self.start()
        self.assertFalse(ok)
        self.assertIn('could not be confirmed', message)
        self.assertTrue(self.node.manual_mode_enabled)
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertEqual(self.commands, ['PIDQUERY', 'STOPALL'])
        self.node.automation_engine.start.assert_not_called()

    def test_invalid_or_unready_start_keeps_manual_mode_and_never_moves(self):
        self.node.manual_mode_enabled = True
        self.assertFalse(self.start(pid_precision='invalid')[0])
        self.assertTrue(self.node.manual_mode_enabled)
        self.assertFalse(self.commands)
        with patch.object(self.node, '_real_measurement_ready', return_value=False):
            self.assertFalse(self.start()[0])
        self.assertTrue(self.node.manual_mode_enabled)
        self.assertFalse(self.commands)

    def test_stop_preempts_manual_handoff_and_no_late_preflight_can_start(self):
        self.node.manual_mode_enabled = True
        waiting = threading.Event()
        sender = self.node.send_command

        def send(command):
            if command.strip() == 'PIDQUERY':
                self.commands.append('PIDQUERY')
                waiting.set()
                return True
            return sender(command)

        self.node.send_command = send
        results = []
        worker = threading.Thread(target=lambda: results.append(self.start()))
        worker.start()
        self.assertTrue(waiting.wait(2))
        cancel = self.node._action_cancel
        stopper = threading.Thread(target=lambda: self.node._auto_stop_callback(None))
        stopper.start()
        try:
            self.assertTrue(cancel.wait(1))
        finally:
            worker.join(3)
            stopper.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertFalse(results[0][0])
        self.assertIsNone(self.node._preflight_thread)
        self.assertIsNone(self.node._controller_fault)
        self.assertEqual(self.node._terminal_reason, 'operator_stop')
        self.assertFalse(any('R' in cmd and cmd[:1] in 'XYZA' for cmd in self.commands))
        self.node.automation_engine.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
