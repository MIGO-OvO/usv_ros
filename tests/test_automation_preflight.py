"""Real preflight worker/engine contracts with deterministic detector feedback."""
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.lib.automation_preflight import (
    AutomationPreflight, PreflightError, calibrated_offsets, involved_axes, normalize_preflight,
)
from test_hardware_runtime_sync import _load_script


class PreflightWorkerTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pubs, self.String = _load_script('preflight_pump', 'scripts/pump_control_node.py')
        self.module.rospy.is_shutdown = lambda: False
        with patch.object(self.module, 'InjectionPumpWorker', return_value=None):
            self.node = self.module.PumpControlNode()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.node.calibration_file = str(Path(self.tmp.name) / 'calibration.json')
        self.zeros = {'X': 123.0, 'Y': 45.0, 'Z': 210.0, 'A': 18.0}
        Path(self.node.calibration_file).write_text(json.dumps({'offsets': self.zeros}), encoding='utf-8')
        self.node.serial_conn = SimpleNamespace(is_open=True)
        self.node.spectro_config['enabled'] = False
        self.node.current_angles.update(X=150.0, Y=5.0, Z=345.0, A=18.0)
        self.node.latest_angle_received_at = time.time()
        self.commands = []
        self.phases = []
        self.block_phase = None
        self.failure_phase = None
        self.missing_ack = None
        self.node.send_command = self.send
        self.node.automation_engine.send_command = self.send
        self.real_engine_start = self.node.automation_engine.start
        self.node.automation_engine.start = Mock(return_value=True)
        self.node._preflight_connection = self.node.serial_conn
        self.addCleanup(self.stop)

    def stop(self):
        self.node._auto_stop_callback(None)
        if self.node._preflight_thread and self.node._preflight_thread.is_alive():
            self.node._preflight_thread.join(2)

    def send(self, command):
        self.commands.append(command.strip())
        preparation = self.node._preflight
        phase = preparation.snapshot()['phase'] if preparation else 'idle'
        if command.strip() == 'PIDQUERY':
            self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
        elif command.startswith('PUMP:SET:'):
            speed = int(command.strip().split(':')[-1])
            if self.missing_ack != 'injection':
                self.node._on_text_received('PUMP_OK:SET=%d,%s' % (speed, 'ON' if speed else 'OFF'))
        elif 'R' in command and re.match(r'[XYZA]E[FB]R', command):
            self.phases.append(phase)
            for axis, direction, delta, precision in re.findall(r'([XYZA])E([FB])R([\d.]+)P([\d.]+)', command):
                delta = float(delta)
                self.node._on_text_received('PID_START:%s,delta=%.1f,dir=%s,prec=%s' % (axis, delta, direction, precision))
                if self.block_phase == phase:
                    continue
                if self.failure_phase == phase:
                    self.node._on_text_received('PID_FAIL:%s=SENSOR_ERR' % axis)
                else:
                    self.node._on_text_received('PID_DONE:%s,abs=360.0,err=0.01' % axis)
            if self.missing_ack != phase:
                self.node._on_text_received('CMD_OK')
        elif preparation and phase == 'separating' and command.startswith('AEFV'):
            if self.missing_ack != phase:
                self.node._on_text_received('CMD_OK')
            if self.block_phase == phase:
                return True
            # Simulate 90°/0.75s at 20rpm, including wraparound and multiple turns.
            for _ in range(8):
                with preparation.lock:
                    preparation.travel['at'] -= 0.75
                    angle = (preparation.travel['angle'] + 90.0) % 360
                self.node._on_angle_received({'A': angle})
        else:
            self.node._on_text_received('CMD_OK')
        return True

    def start(self, **overrides):
        config = {'attempt_id': 'preflight-1', 'source': 'web', 'pid_mode': True,
                  'steps': [{'X': {'enable': 'E'}, 'Y': {'enable': 'D'}},
                            {'Z': {'enable': 'E', 'continuous': True}}],
                  'preflight': {'oil_axis': 'A', 'separation_turns': 2, 'separation_rpm': 20},
                  'injection_pump_policy': {'mode': 'automation', 'speed': 60, 'lead_time_s': 0}}
        config.update(overrides)
        return self.node._execute_control_action('automation_start', config)

    def join(self):
        self.node._preflight_thread.join(3)
        self.assertFalse(self.node._preflight_thread.is_alive())

    def status(self):
        return json.loads(self.pubs['/usv/automation_status'].messages[-1].data)

    def wait_phase(self, phase):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (self.node._preflight.snapshot()['phase'] == phase and
                    (phase not in ('homing', 'compensating') or self.node._preflight.pending)):
                return
            time.sleep(0.005)
        self.fail('worker did not reach ' + phase)

    def test_full_order_relative_zero_full_turns_then_injection_then_engine(self):
        saved = Path(self.node.calibration_file).read_bytes()
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.phases, ['homing', 'compensating'])
        self.assertIn('XEBR27.000P0.1ZEBR135.000P0.1', self.commands)
        self.assertIn('XEFR360.000P0.1ZEFR360.000P0.1', self.commands)
        self.assertLess(self.commands.index('AEFV20J720.000'), self.commands.index('PUMP:SET:60'))
        self.assertEqual(self.node._preflight.snapshot()['travel_degrees'], 720)
        self.node.automation_engine.start.assert_called_once()
        self.assertEqual(saved, Path(self.node.calibration_file).read_bytes())
        self.assertFalse(any(command.startswith('CAL') for command in self.commands))
        self.assertTrue(self.node._automation_injection_prepared)

    def test_engine_does_not_start_on_pid_start_ack_or_wrapped_angle_only(self):
        self.block_phase = 'compensating'
        self.assertTrue(self.start()[0])
        self.wait_phase('compensating')
        self.node._on_angle_received({'X': 123.0, 'Z': 210.0})
        self.node.automation_engine.start.assert_not_called()
        self.assertNotIn('PUMP:SET:60', self.commands)
        self.assertEqual(self.status()['automation_step'], 0)
        self.assertTrue(self.status()['running'])
        self.node._on_text_received('PID_DONE:X,abs=360,err=0')
        self.node.automation_engine.start.assert_not_called()
        self.node._on_text_received('PID_DONE:Z,abs=360,err=0')
        self.join()
        self.node.automation_engine.start.assert_called_once()

    def test_each_pid_failure_stops_all_outputs_and_never_enters_engine(self):
        for phase in ('homing', 'compensating'):
            with self.subTest(phase=phase):
                self.failure_phase = phase
                self.assertTrue(self.start()[0])
                self.join()
                self.assertEqual(self.status()['terminal_reason'], 'pid_fail')
                self.assertEqual(self.status()['preflight']['phase'], 'failed')
                self.assertIn('STOPALL', self.commands)
                self.node.automation_engine.start.assert_not_called()

    def test_unconfigured_relative_zero_fails_before_motion(self):
        Path(self.node.calibration_file).write_text(json.dumps({'offsets': self.zeros, 'configured_axes': ['X']}))
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'configuration_failed')
        self.assertFalse(any('R27' in command for command in self.commands))
        self.node.automation_engine.start.assert_not_called()

    def test_stale_angle_fails_before_motion(self):
        self.node.latest_angle_received_at = time.time() - 2
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'controller_fault')
        self.node.automation_engine.start.assert_not_called()

    def test_injection_ack_is_required_before_engine_start(self):
        self.missing_ack = 'injection'
        with patch.object(self.module.AutomationPreflight, 'ACK_TIMEOUT', 0.03):
            self.assertTrue(self.start()[0])
            self.join()
        self.assertEqual(self.status()['terminal_reason'], 'controller_fault')
        self.assertIn('STOPALL', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_stop_during_injection_lead_halts_without_engine_or_late_restart(self):
        self.assertTrue(self.start(injection_pump_policy={
            'mode': 'automation', 'speed': 60, 'lead_time_s': 5})[0])
        self.wait_phase('injection')
        deadline = time.monotonic() + 2
        while not self.node.inject_pump_enabled and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.node.inject_pump_enabled)
        self.assertTrue(self.node._auto_stop_callback(None).success)
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'operator_stop')
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertIn('STOPALL', self.commands)
        self.assertNotIn('PUMP:SET:60', self.commands[self.commands.index('STOPALL'):])
        self.node.automation_engine.start.assert_not_called()

    def test_pid_command_ack_is_required_even_after_all_done_feedback(self):
        for phase in ('homing', 'compensating'):
            with self.subTest(phase=phase):
                self.missing_ack = phase
                with patch.object(self.module.AutomationPreflight, 'ACK_TIMEOUT', 0.03):
                    self.assertTrue(self.start()[0])
                    self.join()
                self.assertEqual(self.status()['terminal_reason'], 'controller_fault')
                self.assertIn('STOPALL', self.commands)
                self.node.automation_engine.start.assert_not_called()

    def test_oil_ack_and_missing_motion_feedback_both_fail_closed(self):
        for failure in ('ack', 'feedback'):
            with self.subTest(failure=failure):
                self.missing_ack = 'separating' if failure == 'ack' else None
                self.block_phase = 'separating' if failure == 'feedback' else None
                with patch.object(self.module.AutomationPreflight, 'ACK_TIMEOUT', 0.03), \
                        patch.object(self.module.AutomationPreflight, 'ANGLE_TIMEOUT',
                                     0.03 if failure == 'feedback' else 1.0):
                    self.assertTrue(self.start()[0])
                    self.join()
                self.assertEqual(self.status()['terminal_reason'], 'controller_fault')
                self.assertIn('STOPALL', self.commands)
                self.assertNotIn('PUMP:SET:60', self.commands)
                self.node.automation_engine.start.assert_not_called()

    def test_oil_baseline_is_armed_at_the_serial_write(self):
        sender = self.node._send_preflight_command
        def delayed_sender(command, before_send=None):
            if before_send:
                self.assertIsNone(self.node._preflight.travel)
                # A frame received while waiting to write belongs to the
                # previous action and must only update the new baseline.
                self.node._on_angle_received({'A': 108.0})
            return sender(command, before_send)
        self.node._send_preflight_command = delayed_sender
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.node._preflight.snapshot()['travel_degrees'], 720)
        self.node.automation_engine.start.assert_called_once()

    def test_preparation_cleanup_failure_preserves_root_reason_and_latches_fault(self):
        self.failure_phase = 'homing'
        self.node.stop_all_pumps = Mock(return_value=False)
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'pid_fail')
        self.assertTrue(self.status()['cleanup_failed'])
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertFalse(self.start(attempt_id='next')[0])
        self.node.automation_engine.start.assert_not_called()

    def test_preflight_worker_start_failure_cleans_up_before_returning(self):
        with patch.object(self.module.threading.Thread, 'start', side_effect=RuntimeError('cannot start thread')):
            self.assertFalse(self.start()[0])
        self.assertEqual(self.status()['terminal_reason'], 'configuration_failed')
        self.assertEqual(self.status()['preflight']['phase'], 'failed')
        self.assertFalse(self.node._automation_is_active())
        self.assertIn('STOPALL', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_pid_completion_timeout_stops_and_preserves_reason(self):
        self.block_phase = 'homing'
        with patch.object(self.module.AutomationPreflight, 'PID_TIMEOUT', 0.03):
            self.assertTrue(self.start()[0])
            self.join()
        self.assertEqual(self.status()['terminal_reason'], 'pid_timeout')
        self.assertIn('STOPALL', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_stop_cleanup_is_scoped_and_old_worker_cannot_continue(self):
        self.block_phase = 'homing'
        self.assertTrue(self.start()[0])
        self.wait_phase('homing')
        self.assertFalse(self.node._auto_pause_callback(None).success)
        self.assertFalse(self.node._execute_control_action('automation_cleanup', {'attempt_id': 'old'})[0])
        self.assertTrue(self.node._execute_control_action('automation_cleanup', {'attempt_id': 'preflight-1'})[0])
        self.join()
        self.node._on_text_received('PID_DONE:X')
        self.assertEqual(self.status()['terminal_reason'], 'operator_stop')
        self.node.automation_engine.start.assert_not_called()

    def test_owner_renewal_can_acquire_control_lock_while_waiting(self):
        self.block_phase = 'homing'
        self.assertTrue(self.start()[0])
        self.wait_phase('homing')
        before = self.node._owner_last_seen
        self.node._owner_heartbeat_cb(self.String(json.dumps({'attempt_id': 'preflight-1'})))
        self.assertGreaterEqual(self.node._owner_last_seen, before)
        self.assertFalse(self.node._execute_control_action('manual_step', {})[0])
        self.assertFalse(self.start(attempt_id='other')[0])

    def test_owner_watchdog_and_disconnect_use_existing_terminal_reasons(self):
        for fault, reason in (('owner', 'owner_lost'), ('watchdog', 'watchdog_tripped'),
                              ('serial', 'serial_disconnected')):
            with self.subTest(fault=fault):
                self.node._controller_fault = None
                self.block_phase = 'homing'
                self.assertTrue(self.start()[0])
                self.wait_phase('homing')
                if fault == 'owner':
                    self.node._owner_last_seen = time.monotonic() - 6
                    self.node._check_sampling_owner()
                elif fault == 'watchdog':
                    self.node._on_text_received('WATCHDOG_TRIPPED')
                else:
                    self.node._latch_serial_fault(self.node.serial_conn)
                    self.node._auto_stop_callback(None)
                self.join()
                self.assertEqual(self.status()['terminal_reason'], reason)
                self.node.automation_engine.start.assert_not_called()

    def test_engine_start_rejection_is_terminal_after_preparation(self):
        self.node.automation_engine.start.return_value = False
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.status()['terminal_reason'], 'configuration_failed')
        self.assertIn('STOPALL', self.commands)

    def test_real_engine_runs_multiple_loops_with_one_preflight_and_one_injection_start(self):
        self.node.automation_engine.start = self.real_engine_start
        step = {'X': {'enable': 'E', 'direction': 'F', 'angle': '10', 'speed': '5'}, 'interval': 0}
        self.assertTrue(self.start(steps=[step], loop_count=2, preflight={})[0])
        self.join()
        self.node.automation_engine._thread.join(2)
        self.assertFalse(self.node.automation_engine.is_running())
        self.assertEqual(self.phases.count('homing'), 1)
        self.assertEqual(self.phases.count('compensating'), 1)
        self.assertEqual(self.commands.count('PUMP:SET:60'), 1)
        self.assertEqual(self.status()['terminal_reason'], 'completed')


class PreflightContractTests(unittest.TestCase):
    def test_active_axes_include_continuous_and_ignore_disabled_and_legacy_pump(self):
        self.assertEqual(involved_axes([{'X': {'enable': 'E'}, 'pump': {'enable': True}},
                                       {'A': {'enable': 'E', 'continuous': True}, 'Y': {'enable': 'D'}}]), ['X', 'A'])

    def test_configuration_limits_match_firmware_and_fail_closed(self):
        for config in ({'oil_axis': 'B'}, {'separation_turns': 1}, {'separation_turns': float('nan')},
                       {'separation_rpm': 21}, {'separation_rpm': 0}, {'separation_turns': True},
                       {'oil_axis': 'X', 'separation_turns': 11}, {'oil_axis': 'X', 'separation_turns': 0.001}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                normalize_preflight(config)

    def test_saved_zero_zero_degrees_is_explicit_and_not_a_missing_default(self):
        self.assertEqual(calibrated_offsets({'offsets': {'X': 0}}), {'X': 0.0})
        self.assertEqual(calibrated_offsets({'offsets': {'X': 0}, 'configured_axes': []}), {})

    def test_done_before_matching_start_never_satisfies_preparation(self):
        runner = AutomationPreflight(lambda command: True, lambda: None, lambda axes: {}, lambda: None)
        runner.expected = {'X': (360.0, 'F')}
        runner.pending = {'X'}
        runner.notify_text('PID_DONE:X,abs=360,err=0')
        self.assertEqual(runner.pending, {'X'})
        runner.notify_text('PID_START:X,delta=360.0,dir=F,prec=0.1')
        runner.notify_text('PID_DONE:X,abs=360,err=0')
        self.assertFalse(runner.pending)

    def test_separation_rejects_angle_jumps_and_stale_stream(self):
        runner = AutomationPreflight(lambda command: True, lambda: None, lambda axes: {}, lambda: None)
        runner.travel = {'axis': 'X', 'angle': 10, 'at': time.monotonic(), 'degrees': 0, 'rpm': 5}
        runner.notify_angles({'X': 100})
        self.assertIsInstance(runner.error, PreflightError)
        runner.error = None
        runner.travel['at'] = time.monotonic() - 2
        runner.notify_angles({'X': 10})
        self.assertIsInstance(runner.error, PreflightError)


if __name__ == '__main__':
    unittest.main()
