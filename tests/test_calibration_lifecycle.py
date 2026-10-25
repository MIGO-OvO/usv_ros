"""Saved relative-zero homing uses PID completion and the existing STOP path."""
import json
import re
import time
import unittest
from pathlib import Path

import test_automation_preflight as preparation
from command_fixtures import remote_command, wait_commands
from test_mavlink_command_compat import _load_script


class CalibrationLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixture = preparation.PreflightWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.node = fixture.node
        self.commands = []
        self.node.send_command = fixture.module.PumpControlNode.send_command.__get__(self.node)
        self.node.serial_conn.write = self.write
        self.addCleanup(self.join)

    def join(self):
        if self.node._calibration_preflight:
            self.node._calibration_preflight.cancel()
        if self.node._calibration_thread:
            self.node._calibration_thread.join(2)
            self.assertFalse(self.node._calibration_thread.is_alive())

    def write(self, raw):
        command = raw.decode().strip()
        self.commands.append(command)
        if command == 'PIDQUERY':
            self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
        elif command == 'STOPALL':
            self.node._on_text_received('STOPALL_OK')
        elif re.match(r'[XYZA]E[FB]R', command):
            for axis, direction, delta in re.findall(r'([XYZA])E([FB])R([\d.]+)P', command):
                self.node._on_text_received('PID_START:%s,delta=%.1f,dir=%s' % (axis, float(delta), direction))
            self.node._on_text_received('CMD_OK')
        return len(raw)

    def start(self, axes='XYZA'):
        success, message, _ = self.node._execute_control_action('calibration_start', {'axes': axes})
        self.assertTrue(success, message)
        deadline = time.monotonic() + 2
        while not any(re.match(r'[XYZA]E[FB]R', cmd) for cmd in self.commands):
            if time.monotonic() > deadline:
                self.fail('relative PID was not sent')
            time.sleep(0.001)

    def test_saved_nonzero_shortest_moves_and_all_axes_must_complete(self):
        before = time.monotonic()
        self.start()
        self.assertEqual(self.node._calibration_preflight.PID_TIMEOUT, 610.0)
        self.assertGreaterEqual(self.node._calibration['deadline'], before + 615.0)
        self.assertLessEqual(self.node._calibration['deadline'], time.monotonic() + 615.0)
        self.assertIn('XEBR27.000P0.1YEFR40.000P0.1ZEBR135.000P0.1AEFR0.000P0.1', self.commands)
        self.assertFalse(any(cmd.startswith('CAL') or 'R360' in cmd or cmd.startswith('PUMP:SET') for cmd in self.commands))
        for axis in 'XYZ':
            self.node._on_text_received('PID_DONE:' + axis)
        self.assertTrue(self.node._calibration['active'])
        self.node._on_text_received('PID_DONE:A')
        self.node._calibration_thread.join(2)
        self.assertEqual(self.node._calibration['outcome'], 'succeeded')
        self.assertEqual(self.node._calibration['done'], 'XYZA')

    def test_wraparound_different_offsets_and_missing_calibration(self):
        path = Path(self.node.calibration_file)
        path.write_text(json.dumps({'offsets': {'X': 350, 'Y': 10}, 'configured_axes': ['X', 'Y']}))
        self.node._on_angle_received({'X': 10, 'Y': 350})
        self.start('XY')
        self.assertIn('XEBR20.000P0.1YEFR20.000P0.1', self.commands)
        self.node._execute_control_action('manual_stop_all', {'confirm_stop': True})
        before = len(self.commands)
        self.assertFalse(self.node._execute_control_action('calibration_start', {'axes': 'Z'})[0])
        self.assertEqual(len(self.commands), before)

    def test_active_calibration_blocks_new_motion_and_stop_prevents_late_completion(self):
        self.start()
        for action in ('automation_start', 'injection_on', 'calibration_start', 'manual_mode'):
            self.assertFalse(self.node._execute_control_action(action, {})[0])
        self.assertFalse(self.node.send_command('XEFV10'))
        self.assertTrue(self.node._execute_control_action('manual_stop_all', {'confirm_stop': True})[0])
        self.node._calibration_thread.join(2)
        for axis in 'XYZA':
            self.node._on_text_received('PID_DONE:' + axis)
        self.assertEqual(self.node._calibration['outcome'], 'cancelled')
        self.assertEqual(self.commands[-2:], ['PIDQUERY', 'STOPALL'])
        self.assertFalse(any('R' in command for command in self.commands[self.commands.index('STOPALL') + 1:] if command != 'PIDQUERY'))

    def test_axis_failure_stops_outputs_and_cannot_be_erased_by_late_success(self):
        self.start()
        self.node._on_text_received('PID_FAIL:X=SENSOR_ERR')
        self.node._calibration_thread.join(2)
        for axis in 'XYZA':
            self.node._on_text_received('PID_DONE:' + axis)
        self.assertEqual(self.node._calibration['outcome'], 'failed')
        self.assertEqual(self.node._calibration['reason'], 'pid_fail')
        self.assertFalse(self.node._calibration['cleanup_failed'])
        self.assertEqual(self.commands[-2:], ['PIDQUERY', 'STOPALL'])

    def test_injection_off_preserves_calibration_monitor_through_service_and_control(self):
        from test_preflight_web_contract import PreflightWebContractTests
        web = PreflightWebContractTests()
        web.setUp()
        self.addCleanup(web.doCleanups)
        web.server._call_control_command = self.node._execute_control_action
        self.node._on_angle_received({'X': 150, 'Y': 5, 'Z': 345, 'A': 18})
        self.node._on_text_received('ANGLE_AGE_CH_MS:1,1,1,1')
        self.start()
        token = self.node._action_cancel
        before = len(self.commands)
        self.assertTrue(self.node._execute_control_action('injection_off', {})[0])
        self.assertTrue(self.node._injection_off_callback(None).success)
        self.assertTrue(web.client.post('/api/injection-pump/off').get_json()['success'])
        self.assertIs(self.node._action_cancel, token)
        self.assertFalse(token.is_set())
        self.assertTrue(self.node._calibration['active'])
        self.assertNotIn('STOPALL', self.commands[before:])
        for axis in 'XYZA':
            self.node._on_text_received('PID_DONE:' + axis)
        self.node._calibration_thread.join(2)
        self.assertFalse(self.node._calibration_thread.is_alive())
        self.assertEqual(self.node._calibration['outcome'], 'succeeded')
        self.node._on_text_received('PID_DONE:X')
        self.assertEqual(self.node._calibration['outcome'], 'succeeded')

    def test_timeout_and_stale_feedback_fail_closed(self):
        self.node.latest_angle_received_at = time.time() - 2
        self.assertFalse(self.node._execute_control_action('calibration_start', {})[0])
        self.assertFalse(self.commands)
        self.node._on_angle_received({'X': 150, 'Y': 5, 'Z': 345, 'A': 18})
        self.start()
        self.node._calibration['deadline'] = time.monotonic() - 1
        self.node._check_calibration()
        self.node._calibration_thread.join(2)
        self.assertEqual(self.node._calibration['outcome'], 'failed')
        self.assertEqual(self.node._calibration['reason'], 'calibration_timeout')
        self.assertEqual(self.commands[-2:], ['PIDQUERY', 'STOPALL'])

    def test_cleanup_failure_is_known_before_first_terminal_status(self):
        self.start()
        self.node._confirm_all_outputs_stopped = lambda: False
        self.node._on_text_received('PID_FAIL:X=SENSOR_ERR')
        self.node._calibration_thread.join(2)
        states = [json.loads(msg.data)['calibration'] for msg in self.node.automation_status_pub.messages]
        terminal = [state for state in states if state.get('outcome') == 'failed'
                    and not state.get('active') and not state.get('stopping')]
        self.assertTrue(terminal)
        self.assertTrue(all(state.get('cleanup_failed') for state in terminal))

    def test_serial_disconnect_does_not_claim_outputs_stopped(self):
        self.start()
        self.node._latch_serial_fault(self.node.serial_conn)
        self.node._calibration_thread.join(2)
        self.assertEqual(self.node._calibration['reason'], 'serial_disconnected')
        self.assertTrue(self.node._calibration['cleanup_failed'])

    def test_same_request_retries_send_one_motion_even_after_terminal(self):
        module = _load_script('calibration_retry_trigger', 'scripts/mavlink_trigger_node.py')
        trigger = module.MAVLinkTriggerNode()
        trigger._mavlink_tcp_session = 1
        def control(action, payload=None):
            payload = dict(payload or {}, mavlink_request=trigger._command_context.request)
            return self.node._execute_control_action(action, payload)[0]
        trigger._call_control_command = control
        request = remote_command(trigger, 31014)
        for _ in range(2):
            trigger._mavlink_cmd_rx_cb(request)
            wait_commands(trigger)
        deadline = time.monotonic() + 2
        while not any(re.match(r'[XYZA]E[FB]R', command) for command in self.commands):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.001)
        attempt = self.node._calibration['attempt_id']
        for axis in 'XYZA':
            self.node._on_text_received('PID_DONE:' + axis)
        self.node._calibration_thread.join(2)
        trigger._mavlink_cmd_rx_cb(request)
        wait_commands(trigger)
        self.assertEqual(self.node._calibration['attempt_id'], attempt)
        self.assertEqual(sum(bool(re.match(r'[XYZA]E[FB]R', command)) for command in self.commands), 1)

    def test_bridge_keeps_calibration_status_over_idle_mission_heartbeat(self):
        module = _load_script('calibration_bridge', 'scripts/usv_mavlink_router_bridge.py')
        bridge = module.USVMavlinkRouterBridge()
        self.start()
        message = module.String()
        message.data = json.dumps({'status': 'idle', 'source_monotonic': time.monotonic(), 'calibration': self.node._calibration})
        bridge._automation_status_cb(message)
        message.data = 'IDLE'
        bridge._mission_status_cb(message)
        self.assertEqual(bridge._status_code, 4)


if __name__ == '__main__':
    unittest.main()
