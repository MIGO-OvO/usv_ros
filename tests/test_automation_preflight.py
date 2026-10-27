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
from measurement_fixtures import prepare_measurement, fresh_measurement


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
        prepare_measurement(self.node)
        self.node._on_angle_received({'X': 150.0, 'Y': 5.0, 'Z': 345.0, 'A': 18.0})
        self.node._on_health_received({'timestamp_ms': 1000})
        self.node._on_text_received('ANGLE_AGE_CH_MS:1,1,1,1')
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
        fresh_measurement(self.node)
        self.commands.append(command.strip())
        preparation = self.node._preflight
        phase = preparation.snapshot()['phase'] if preparation else 'idle'
        if command.strip() == 'PIDQUERY':
            self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
        elif command.strip() == 'STOPALL':
            self.node._on_text_received('STOPALL_OK')
        elif command.strip() == 'PUMP:OFF':
            self.node._on_text_received('PUMP_OK:OFF')
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
                    if phase == 'separating':
                        remaining = delta
                        while remaining > 0.00001:
                            step = min(30.0, remaining)
                            with preparation.lock:
                                preparation.travel['at'] -= step / (preparation.travel['rpm'] * 6)
                                angle = (preparation.travel['angle'] + step) % 360
                            self.node._on_angle_received({axis: angle})
                            remaining -= step
                    self.node._on_text_received('PID_DONE:%s,abs=360.0,err=0.01' % axis)
            if self.missing_ack != phase:
                self.node._on_text_received('CMD_OK')
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
                    (phase not in ('homing', 'compensating', 'separating') or self.node._preflight.pending)):
                return
            time.sleep(0.005)
        self.fail('worker did not reach ' + phase)

    def test_oil_separation_uses_one_pid_move_for_ten_turns(self):
        self.assertTrue(self.start(preflight={
            'oil_axis': 'A', 'separation_turns': 10, 'separation_rpm': 0.1})[0])
        self.join()
        moves = [float(delta) for command in self.commands
                 for delta in re.findall(r'AEFR([\d.]+)P', command) if float(delta) > 0]
        self.assertAlmostEqual(sum(moves), 3600)
        self.assertEqual(moves, [3600.0])
        self.assertFalse(any('J' in command for command in self.commands))
        self.node.automation_engine.start.assert_called_once()

    def test_low_pid_floor_keeps_fractional_turns_in_one_pid_move(self):
        sender = self.send
        def send(command):
            if command == 'PIDQUERY':
                self.commands.append(command)
                self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,0.1,8')
                return True
            return sender(command)
        self.node.send_command = send
        self.assertTrue(self.start(preflight={
            'oil_axis': 'A', 'separation_turns': 1.25, 'separation_rpm': 20})[0])
        self.join()
        moves = [float(delta) for command in self.commands
                 for delta in re.findall(r'AEFR([\d.]+)P', command)]
        self.assertEqual(moves, [450.0])
        self.node.automation_engine.start.assert_called_once()

    def test_separation_ack_and_full_travel_cannot_replace_pid_done(self):
        self.block_phase = 'separating'
        self.assertTrue(self.start()[0])
        self.wait_phase('separating')
        preparation = self.node._preflight
        for step in [30] * 24:
            with preparation.lock:
                preparation.travel['at'] -= step / 48
                angle = (preparation.travel['angle'] + step) % 360
            self.node._on_angle_received({'A': angle})
        self.assertEqual(self.commands.count('AEFR720.000P0.1'), 1)
        self.node.automation_engine.start.assert_not_called()
        self.assertNotIn('PUMP:SET:60', self.commands)
        self.node._on_text_received('PID_DONE:A,abs=720,err=0')
        self.join()
        self.assertEqual(self.commands.count('AEFR720.000P0.1'), 1)
        self.node.automation_engine.start.assert_called_once()

    def test_stop_during_multi_turn_separation_prevents_late_restart(self):
        self.block_phase = 'separating'
        self.assertTrue(self.start()[0])
        self.wait_phase('separating')
        self.assertIn('AEFR720.000P0.1', self.commands)
        self.assertTrue(self.node._auto_stop_callback(None).success)
        self.join()
        stopped_at = self.commands.index('STOPALL')
        self.node._on_text_received('PID_DONE:A,abs=720,err=0')
        self.assertFalse(any('AEFR' in cmd for cmd in self.commands[stopped_at:]))
        self.assertFalse(self.node.inject_pump_enabled)
        self.node.automation_engine.start.assert_not_called()

    def test_separation_pid_timeout_stops_before_injection(self):
        self.block_phase = 'separating'
        with patch.object(self.node, 'pid_wait_timeout', 0.04):
            self.assertTrue(self.start()[0])
            self.join()
        self.assertEqual(self.status()['terminal_reason'], 'pid_timeout')
        self.assertIn('STOPALL', self.commands)
        self.assertNotIn('PUMP:SET:60', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_extended_firmware_settling_timeout_still_stops_and_retains_reason(self):
        self.block_phase = 'compensating'
        self.assertTrue(self.start()[0])
        self.wait_phase('compensating')
        self.node._on_text_received('PID_SETTLE:X,err=9.9,elapsed_ms=100000')
        self.assertEqual(self.node._preflight.pending, {'X', 'Z'})
        self.node._on_text_received(
            'PID_TIMEOUT:X,abs=359.7,err=0.3,phase=settling,elapsed_ms=280000,settle_ms=180000')
        self.join()
        self.node._on_text_received('PID_DONE:X,abs=360,err=0.01')
        self.assertEqual(self.status()['terminal_reason'], 'pid_timeout')
        self.assertIn('phase=settling', self.node.last_automation_failure['reason'])
        self.assertIn('STOPALL', self.commands)
        self.assertNotIn('PUMP:SET:60', self.commands)
        self.node.automation_engine.start.assert_not_called()

    def test_full_order_relative_zero_full_turns_then_injection_then_engine(self):
        saved = Path(self.node.calibration_file).read_bytes()
        self.assertTrue(self.start()[0])
        self.join()
        self.assertEqual(self.phases, ['homing', 'compensating', 'separating'])
        self.assertIn('XEBR27.000P0.1ZEBR135.000P0.1', self.commands)
        self.assertIn('XEFR360.000P0.1ZEFR360.000P0.1', self.commands)
        self.assertLess(self.commands.index('AEFR720.000P0.1'), self.commands.index('PUMP:SET:60'))
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
        for phase in ('homing', 'compensating', 'separating'):
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
        for phase in ('homing', 'compensating', 'separating'):
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

    def test_preflight_failure_logs_the_phase_and_specific_cause(self):
        self.failure_phase = 'separating'
        with patch.object(self.module.rospy, 'logerr') as log_error:
            self.assertTrue(self.start()[0])
            self.join()
        log_error.assert_any_call(
            'Preflight failed: attempt_id=%s phase=%s reason=%s detail=%s',
            'preflight-1', 'separating', 'pid_fail', 'PID_FAIL:A=SENSOR_ERR')
        self.assertIn('STOPALL', self.commands)

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
        with patch.object(self.node, 'pid_wait_timeout', 0.03):
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
                self.assertTrue(self.start(attempt_id='preflight-' + fault)[0])
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


class SeparationPIDSettlingTests(unittest.TestCase):
    def run_separation(self, *, done=True, turns=5.0, position=None, finish_at=14):
        clock = [100.0]
        raw = {'A': 350.0}
        move = {}
        commands, completions = [], []

        def send(command, before_send=None):
            if before_send:
                before_send()
            commands.append(command)
            if command == 'PIDQUERY':
                runner.notify_text('PIDPARAM:0.14,0.015,0.06,1,6')
            else:
                match = re.fullmatch(r'AE([FB])R([\d.]+)P[\d.]+', command)
                self.assertIsNotNone(match)
                direction, delta = match.groups()
                runner.notify_text('PID_START:A,delta=%.1f,dir=%s,prec=0.1' % (float(delta), direction))
                runner.notify_text('CMD_OK')
                if runner.snapshot()['phase'] == 'separating':
                    move.update(at=clock[0], start=raw['A'], delta=float(delta))
                else:
                    runner.notify_text('PID_DONE:A,abs=360,err=0.09')
            return True

        def feedback(_timeout):
            clock[0] += 0.05
            elapsed = clock[0] - move['at']
            target = move['delta']
            travel_time = max(8.0, target / 30.0)
            finished = done and elapsed >= max(finish_at, travel_time + 6)
            # Travel within the PID speed limit, then correct +/-0.2 deg before DONE.
            # The 0.1-deg firmware stability gate has not passed during hunting.
            if position is not None:
                degrees = position(elapsed, target)
            elif elapsed < travel_time:
                degrees = target * elapsed / travel_time
            else:
                degrees = target + (0.2 if int(elapsed * 4) % 2 else -0.2)
            if finished and position is None:
                degrees = target
            raw['A'] = (move['start'] + degrees) % 360
            runner.notify_angles(raw)
            if finished:
                completions.append(target)
                runner.notify_text('PID_DONE:A,abs=%.2f,err=0.00' % target)

        runner = AutomationPreflight(send, lambda require_measurement: None,
                                     lambda axes, require_health=False: dict(raw), lambda: None)
        runner.changed = Mock(wait=Mock(side_effect=feedback))
        with patch('scripts.lib.automation_preflight.time.monotonic', side_effect=lambda: clock[0]):
            runner.run(['A'], {'A': 350.0}, normalize_preflight({
                'oil_axis': 'A', 'separation_turns': turns, 'separation_rpm': 5.0}), 0.1, None)
        return runner, commands, completions

    def test_five_turns_use_one_1800_degree_pid_move_and_wait_for_done(self):
        runner, commands, completions = self.run_separation()
        self.assertEqual(runner.snapshot()['phase'], 'complete')
        self.assertAlmostEqual(runner.snapshot()['travel_degrees'], 1800.0)
        self.assertEqual(completions, [1800.0])
        self.assertEqual(commands.count('AEFR1800.000P0.1'), 1)
        self.assertFalse(any('J' in command for command in commands))

    def test_at_target_without_done_still_reaches_pid_timeout(self):
        with self.assertRaises(PreflightError) as caught:
            self.run_separation(done=False)
        self.assertEqual(caught.exception.reason, 'pid_timeout')

    def test_stall_short_of_target_keeps_forward_progress_timeout(self):
        with self.assertRaisesRegex(PreflightError, 'no verified forward progress'):
            self.run_separation(done=False, position=lambda elapsed, target: min(30 * elapsed, target / 2))

    def test_overshoot_can_settle_but_still_requires_real_done(self):
        with self.assertRaises(PreflightError) as caught:
            self.run_separation(done=False, position=lambda elapsed, target: min(30 * elapsed, target + 1))
        self.assertEqual(caught.exception.reason, 'pid_timeout')

    def test_real_done_after_long_settling_completes_multi_turn_move(self):
        runner, _, completions = self.run_separation(finish_at=150)
        self.assertEqual(runner.snapshot()['phase'], 'complete')
        self.assertEqual(completions, [1800.0])

    def test_small_separation_uses_its_existing_five_percent_travel_tolerance(self):
        runner, _, completions = self.run_separation(
            turns=0.01, position=lambda elapsed, target: min(target * elapsed / 8, target - 0.16))
        self.assertEqual(runner.snapshot()['phase'], 'complete')
        self.assertAlmostEqual(sum(completions), 3.6)
        with self.assertRaisesRegex(PreflightError, 'without verified separation travel'):
            self.run_separation(
                turns=0.01, position=lambda elapsed, target: min(target * elapsed / 8, target - 0.20))

    def test_target_settling_still_requires_fresh_angles_and_health(self):
        angles = Mock(return_value={'A': 114.0})
        runner = AutomationPreflight(Mock(), Mock(), angles, Mock())
        runner.travel = {'axis': 'A', 'at': 13.0, 'degrees': 114.0,
                         'target_degrees': 114.0, 'tolerance': 0.5, 'progress_at': 8.0}
        with patch('scripts.lib.automation_preflight.time.monotonic', return_value=13.1):
            runner._check()
            angles.assert_called_with(['A'], require_health=True)
            angles.side_effect = PreflightError('controller_fault', 'stale health')
            with self.assertRaisesRegex(PreflightError, 'stale health'):
                runner._check()
            angles.side_effect = None
            runner.travel['at'] = 12.0
            with self.assertRaisesRegex(PreflightError, 'angle feedback timeout'):
                runner._check()


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
        self.assertEqual(calibrated_offsets({'offsets': {'X': 0}, 'configured_axes': ['X']}), {'X': 0.0})
        self.assertEqual(calibrated_offsets({'offsets': {'X': 0}}), {})
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
        runner.travel = {'axis': 'X', 'angle': 10, 'at': time.monotonic(), 'degrees': 0, 'base_degrees': 0, 'rpm': 5,
                         'target_degrees': 114.0, 'tolerance': 0.5,
                         'progress_at': time.monotonic(), 'progress_degrees': 0}
        runner.notify_angles({'X': 100})
        self.assertIsInstance(runner.error, PreflightError)
        runner.error = None
        runner.travel['at'] = time.monotonic() - 2
        runner.notify_angles({'X': 10})
        self.assertIsInstance(runner.error, PreflightError)


if __name__ == '__main__':
    unittest.main()
