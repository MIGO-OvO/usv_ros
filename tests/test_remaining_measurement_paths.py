"""Reachable preflight/resume/fault regressions through production pump paths.

No serial device is opened. Writes, firmware replies and raw binary frames cross
the production serial/engine/parser seams; clock substitution is labelled below.
"""
import functools
import json
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_automation_preflight as preparation
from measurement_fixtures import prepare_measurement, fresh_measurement


class RemainingMeasurementPathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation.PreflightWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.node = self.fixture.node
        self.module = self.fixture.module
        self.step = {'name': 'measurement boundary', 'interval': 0,
                     'A': {'enable': 'E', 'direction': 'F', 'speed': '5', 'angle': '180'}}
        self.motion = threading.Event()
        self.writes = []
        self.formal_motion_count = 0
        self.reply_done = False
        self.node.serial_conn.write = self.serial_write
        self.node.send_command = functools.partial(self.module.PumpControlNode.send_command, self.node)
        self.node.automation_engine.send_command = self.node.send_command
        self.node.automation_engine.start = self.fixture.real_engine_start

    def serial_write(self, raw):
        command = raw.decode().strip()
        self.writes.append(command)
        preflight = self.node._preflight
        if command.startswith('AEFR180.0P') and preflight and not preflight.snapshot()['active']:
            self.formal_motion_count += 1
            self.motion.set()
            self.node._on_text_received('PID_START:A,delta=180.0,dir=F,prec=0.1')
            self.node._on_text_received('CMD_OK')
            if self.reply_done:
                self.node._on_text_received('PID_DONE:A,abs=180,err=0')
                fresh_measurement(self.node)
        else:
            self.fixture.send(command)
        return len(raw)

    def start_engine(self, *, lead=0, interval=0):
        step = dict(self.step, interval=interval)
        self.assertTrue(self.fixture.start(
            steps=[step], preflight={},
            injection_pump_policy={'mode': 'automation', 'speed': 60, 'lead_time_s': 0})[0])
        self.fixture.join()
        self.assertTrue(self.motion.wait(1), 'real engine never entered the formal step')
        self.node.injection_pump_policy['lead_time_s'] = lead

    def send_binary_fault(self, status=0x03):
        reader = self.module.PumpSerialReader(self.node.serial_conn)
        reader.on_spectro_received = self.node._on_spectro_received
        frame = bytearray(b'\x55\xdd' + struct.pack('<IBBif', 123, 2, status, 32767, 2.048))
        checksum = 0
        for value in frame[1:]:
            checksum ^= value
        frame.extend((checksum, 10))
        reader._process_data(bytes(frame))

    def await_terminal(self, timeout=0.7):
        deadline = time.monotonic() + timeout
        while not self.node._terminal_finalized and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.node._terminal_finalized, 'active task did not terminate promptly')
        self.node.automation_engine._thread.join(1)
        self.assertFalse(self.node.automation_engine._thread.is_alive())

    def make_preflight(self):
        self.node.sampling_context = {'attempt_id': 'preflight-current'}
        self.node._preflight_attempt_id = 'preflight-current'
        self.node._owner_last_seen = time.monotonic()
        self.node._preflight = self.module.AutomationPreflight(
            self.node._send_preflight_command, self.node._check_preflight_active,
            self.node._preflight_angles, self.node._publish_preflight_status)
        return self.node._preflight

    def test_mechanical_preparation_and_injection_prestart_do_not_gate_on_measurement(self):
        # Deterministic production function timing: admission age 1.9s,
        # PIDQUERY ACK after 0.4s, and no new frame at all afterwards. Homing,
        # the 360 deg compensation, the oil separation and the injection
        # pre-start do not consume samples, so none of them may stall here.
        clock = [100.0]
        writes = []
        with patch.object(self.module.time, 'monotonic', side_effect=lambda: clock[0]):
            prepare_measurement(self.node)
            preflight = self.make_preflight()
            clock[0] = 101.9
            def send(command, valid=None):
                writes.append((command, clock[0]))
                if command == 'PIDQUERY':
                    clock[0] += 0.4
                    self.node._on_text_received('PIDPARAM:0.14,0.015,0.06,1,8')
                elif command == 'PUMP:OFF':
                    self.node._on_text_received('PUMP_OK:OFF')
                elif command.startswith('PUMP:SET:'):
                    self.node._on_text_received('PUMP_OK:SET=60,ON')
                return True
            self.node.send_command = send
            # The formal hand-off still refuses to start without real data.
            with self.assertRaises(self.module.PreflightError) as raised:
                preflight.run([], {}, {'oil_axis': None, 'separation_turns': 0, 'separation_rpm': 5},
                              0.1, (60, 0))
        self.assertEqual(raised.exception.reason, 'spectrometer_not_ready')
        off_at = next(at for command, at in writes if command == 'PUMP:OFF')
        self.assertAlmostEqual(off_at, 102.3)
        on_at = next(at for command, at in writes if command == 'PUMP:SET:60')
        self.assertAlmostEqual(on_at, 102.3, msg='injection pre-start must not wait for a measurement')

    def test_finite_oil_stop_bypasses_stale_measurement_wait(self):
        preflight = self.make_preflight()
        preflight.stage('separating')
        waited = []
        def recover(_timeout):
            waited.append(True)
            fresh_measurement(self.node)
        preflight.changed.wait = recover
        with patch.object(self.module.time, 'monotonic', return_value=time.monotonic() + 3.0):
            self.assertTrue(self.node._send_preflight_command('ADFV0J0'))
        self.assertEqual(waited, [], 'finite-J output disable must not wait for ADS')
        self.assertIn('ADFV0J0', self.writes)

    def test_old_preflight_owner_cannot_send_stop_into_new_attempt(self):
        self.make_preflight()
        self.node.sampling_context = {'attempt_id': 'new-owner'}
        with self.assertRaises((self.module.PreflightCancelled, self.module.PreflightError)):
            self.node._send_preflight_command('PUMP:OFF')
        self.assertEqual(self.writes, [])

    def test_pause_resume_actual_three_second_lead_waits_for_recovered_measurement(self):
        paused = threading.Event()
        wait_if_paused = self.node.automation_engine._wait_if_paused
        def observe_pause():
            if self.node.automation_engine.is_paused():
                paused.set()
            return wait_if_paused()
        self.node.automation_engine._wait_if_paused = observe_pause
        self.start_engine(lead=3.0)
        self.assertTrue(self.node._auto_pause_callback(None).success)
        self.assertFalse(self.node.inject_pump_enabled, 'pause must perform physical OFF write')
        self.assertTrue(paused.wait(1), 'engine never observed physical pause before resume')
        self.motion.clear()
        self.assertTrue(self.node._auto_resume_callback(None).success)
        # The resume restarts injection and then waits the real configured lead.
        time.sleep(3.2)
        self.assertEqual(self.formal_motion_count, 1,
                         'resume emitted new motor motion with a >2s-old frame after lead')
        self.assertTrue(self.node.automation_engine.is_running(), 'short silence should permit recovery waiting')
        fresh_measurement(self.node)
        self.assertTrue(self.motion.wait(0.7), 'new real frame did not release the resumed step')
        self.assertEqual(self.formal_motion_count, 2)

    def test_saturated_binary_frame_is_retained_as_data_and_does_not_stop(self):
        self.start_engine()
        before = len(self.writes)
        self.send_binary_fault(0x09)  # VALID | SATURATED, a bubble/full-scale sample.
        time.sleep(0.15)
        raw_messages = [json.loads(message.data)
                        for message in self.fixture.pubs['/usv/spectrometer_raw'].messages[-10:]]
        saturated = [message for message in raw_messages if message.get('saturated')]
        self.assertTrue(saturated)
        self.assertTrue(saturated[-1]['valid'])
        time.sleep(0.1)
        self.assertFalse(self.node._terminal_finalized)
        self.assertIsNone(self.node._controller_fault)
        self.assertNotIn('STOPALL', self.writes[before:])
        self.assertTrue(self.node.automation_engine.is_running())

    def test_saturated_binary_frame_does_not_interrupt_long_engine_interval(self):
        self.reply_done = True
        waiting = threading.Event()
        wait_interval = self.node.automation_engine._wait_interval
        def interval(ms):
            waiting.set()
            return wait_interval(ms)
        self.node.automation_engine._wait_interval = interval
        self.start_engine(interval=6000)
        self.assertTrue(waiting.wait(1))
        self.send_binary_fault(0x09)
        time.sleep(0.1)
        self.assertFalse(self.node._terminal_finalized)
        self.assertIsNone(self.node._controller_fault)
        self.assertTrue(self.node.automation_engine.is_running())

    def test_explicit_spectro_error_is_not_relabelled_as_silence(self):
        self.send_binary_fault(0x03)  # VALID | I2C error.
        with patch.object(self.module.time, 'monotonic', return_value=time.monotonic() + 11):
            self.node._check_spectro_freshness()
        self.assertEqual(self.node.spectro_state, 'i2c_error')
        self.assertEqual(self.node.latest_spectro['status'], 'i2c_error')

    def resume_into_lead(self):
        paused, lead_started = threading.Event(), threading.Event()
        wait_if_paused = self.node.automation_engine._wait_if_paused
        wait_seconds = self.node._wait_seconds_with_pause
        def observe_pause():
            if self.node.automation_engine.is_paused():
                paused.set()
            return wait_if_paused()
        def observe_lead(seconds):
            lead_started.set()
            return wait_seconds(seconds)
        self.node.automation_engine._wait_if_paused = observe_pause
        self.start_engine(lead=3)
        self.assertTrue(self.node._auto_pause_callback(None).success)
        self.assertTrue(paused.wait(1))
        self.node._wait_seconds_with_pause = observe_lead
        self.assertTrue(self.node._auto_resume_callback(None).success)
        self.assertTrue(lead_started.wait(1))
        self.assertTrue(self.node.inject_pump_enabled)

    def test_stop_interrupts_resumed_injection_lead_without_new_motor_motion(self):
        self.resume_into_lead()
        self.assertTrue(self.node._auto_stop_callback(None).success)
        self.node.automation_engine._thread.join(1)
        self.assertFalse(self.node.automation_engine._thread.is_alive())
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertEqual(self.formal_motion_count, 1)
        self.assertEqual(self.node._terminal_reason, 'operator_stop')

    def test_fault_interrupts_resumed_injection_lead_and_keeps_first_reason(self):
        self.resume_into_lead()
        self.send_binary_fault(0x03)  # VALID | I2C_ERROR.
        self.await_terminal()
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertEqual(self.formal_motion_count, 1)
        self.assertEqual(self.node._terminal_reason, 'spectrometer_not_ready')
        self.node._auto_stop_callback(None)
        self.assertEqual(self.node._terminal_reason, 'spectrometer_not_ready')

    def test_owner_loss_interrupts_resumed_injection_lead(self):
        self.resume_into_lead()
        self.node._owner_last_seen = time.monotonic() - 6
        self.node._check_sampling_owner()
        self.node.automation_engine._thread.join(1)
        self.assertFalse(self.node.automation_engine._thread.is_alive())
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertEqual(self.formal_motion_count, 1)
        self.assertEqual(self.node._terminal_reason, 'owner_lost')

    def test_frame_ages_while_waiting_for_final_serial_lock_requires_new_frame(self):
        preflight = self.make_preflight()
        preflight.state.update(active=False, phase='complete')
        self.node.injection_pump_policy['mode'] = 'manual'
        generated = threading.Event()
        generate = self.node.command_generator.generate_command
        def command(step, mode):
            result = generate(step, mode)
            generated.set()
            return result
        self.node.command_generator.generate_command = command
        result = []
        worker = threading.Thread(target=lambda: result.append(self.node._send_automation_step(self.step)))
        self.node.automation_engine._thread = worker
        self.node.automation_engine._running.set()
        clock = [time.monotonic()]
        with patch.object(self.module.time, 'monotonic', side_effect=lambda: clock[0]):
            # The readiness check shares serial_lock with the reader, so while
            # the lock is held neither the check nor the write can proceed.
            with self.node.serial_lock:
                worker.start()
                self.assertFalse(generated.wait(0.2))
                clock[0] += 3
            self.assertFalse(generated.wait(0.2), 'stale frame admitted after lock contention')
            self.assertEqual(self.writes, [], 'write used a frame checked before serial-lock contention')
            fresh_measurement(self.node)
            self.assertTrue(generated.wait(1))
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(self.formal_motion_count, 1)

    def test_foreign_serial_session_cannot_deliver_fault_or_valid_measurement(self):
        old_connection = self.node.serial_conn
        self.node.serial_conn = SimpleNamespace(is_open=True, write=lambda raw: len(raw))
        sequence = self.node._spectro_sequence
        self.node._on_spectro_received({'valid': False, 'saturated': True}, old_connection)
        self.node._on_spectro_received({'valid': True, 'voltage': 1.2}, old_connection)
        self.assertEqual(self.node._spectro_sequence, sequence)
        self.assertIsNone(self.node._controller_fault)
        self.assertFalse(self.node._real_measurement_ready())

    def test_new_valid_frame_does_not_clear_latched_explicit_fault_or_resume_pumps(self):
        self.start_engine()
        self.send_binary_fault(0x03)
        self.await_terminal()
        fault = self.node._controller_fault
        before = len(self.writes)
        fresh_measurement(self.node)
        self.assertEqual(self.node._controller_fault, fault)
        self.assertFalse(self.node._real_measurement_ready())
        self.assertFalse(self.node._auto_resume_callback(None).success)
        self.assertFalse(self.fixture.start(attempt_id='after-fault')[0])
        self.assertEqual(self.writes[before:], [])
        self.assertFalse(self.node.inject_pump_enabled)

    def test_real_pid_wait_preserves_short_silence_recovery(self):
        self.start_engine()
        with patch.object(self.module.time, 'monotonic', return_value=time.monotonic() + 3):
            self.node._check_spectro_freshness()
        self.assertTrue(self.node.automation_engine.is_running())
        self.assertIsNone(self.node._terminal_reason)
        self.assertFalse(self.node.latest_spectro['valid'])
        fresh_measurement(self.node)
        self.node._on_text_received('PID_DONE:A,abs=180,err=0')
        self.await_terminal()
        self.assertEqual(self.node._terminal_reason, 'completed')


if __name__ == '__main__':
    unittest.main()
