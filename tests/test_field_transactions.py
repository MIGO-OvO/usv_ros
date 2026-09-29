"""Field regressions using real pump methods, threads and serial ACK callbacks."""
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from test_hardware_runtime_sync import _load_script


MAP_ACK = 'I2CMAP_OK:X=0,Y=3,Z=4,A=7,SPEC=2'
CFG_ACK = 'ADS_OK:CFG,CH=2,ADDR=0x40,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=90'


class FakeSerial:
    def __init__(self):
        self.is_open = False
        self.writes = []
        self.changed = threading.Condition()

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def write(self, data):
        assert self.is_open
        with self.changed:
            self.writes.append((time.monotonic(), data, threading.current_thread()))
            self.changed.notify_all()

    def wait_keepalives(self, count, timeout=2):
        with self.changed:
            return self.changed.wait_for(
                lambda: len(self.keepalives()) >= count, timeout)

    def keepalives(self):
        return [entry for entry in self.writes if b'KEEPALIVE' in entry[1]]


class FieldTransactionTests(unittest.TestCase):
    def setUp(self):
        self.module, self.pubs, self.String = _load_script(
            'pump_field_transactions', 'scripts/pump_control_node.py')
        self.node = self.module.PumpControlNode()
        self.node._refresh_runtime_settings = Mock()
        self.module.rospy.is_shutdown = lambda: False
        self.addCleanup(self.node.disconnect)
        self.addCleanup(self.node.injection_pump_worker.stop)

    def serial_environment(self):
        ports = []

        def serial_factory():
            port = FakeSerial()
            ports.append(port)
            return port

        self.enterContext(patch.object(self.module.serial, 'Serial', side_effect=serial_factory))
        self.enterContext(patch.object(self.module, 'perform_detector_handshake',
                                      return_value=(True, 'DET_ID:USV_DETECTOR,CAP=WATCHDOG1')))
        self.enterContext(patch.object(self.module, 'PumpSerialReader'))
        return ports

    def ack_sender(self, *, mapping=MAP_ACK, config=CFG_ACK, start='ADS_OK:START',
                   stop='ADS_OK:STOP', frame=True):
        sent = []

        def send(command):
            sent.append(command)
            reply = (mapping if command.startswith('I2CMAP:') else
                     config if command.startswith('ADSCFG:') else
                     start if command == 'ADSSTART' else
                     stop if command == 'ADSSTOP' else None)
            if reply:
                self.node._on_text_received(reply)
            if frame and command == 'ADSSTART' and reply == 'ADS_OK:START':
                self.node._on_spectro_received({'valid': True, 'voltage': 1.2})
            return True

        self.node.send_command = send
        return sent

    def test_keepalive_survives_blocking_initialization_over_three_seconds(self):
        ports = self.serial_environment()
        entered, release = threading.Event(), threading.Event()

        def configure():
            entered.set()
            release.wait(5)

        self.node._apply_runtime_configuration = configure
        result = []
        caller = threading.Thread(target=lambda: result.append(self.node.connect()))
        caller.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(ports[0].wait_keepalives(7, 4.2))
            beats = ports[0].keepalives()
            self.assertGreaterEqual(beats[-1][0] - beats[0][0], 3.0)
            self.assertLess(max(b[0] - a[0] for a, b in zip(beats, beats[1:])), 1.0)
        finally:
            release.set()
            caller.join(2)
        self.assertEqual(result, [True])

    def test_reconnect_stops_old_worker_and_disconnect_stops_new_worker(self):
        ports = self.serial_environment()
        self.node._apply_runtime_configuration = Mock()
        self.assertTrue(self.node.connect())
        self.assertTrue(ports[0].wait_keepalives(1))
        old_worker = ports[0].keepalives()[0][2]
        self.assertTrue(self.node._reconnect_callback(None).success)
        self.assertFalse(old_worker.is_alive())
        self.assertTrue(ports[1].wait_keepalives(2))
        self.assertEqual(len({entry[2] for entry in ports[1].keepalives()}), 1)
        worker = ports[1].keepalives()[0][2]
        self.node.disconnect()
        self.assertFalse(worker.is_alive())
        self.assertFalse(ports[1].is_open)

    def test_keepalive_continues_while_ack_awaits_delayed_firmware_reply(self):
        """I2CMAP/ADSCFG ACK waits must not starve the hardware watchdog lease."""
        ports = self.serial_environment()
        self.node._apply_runtime_configuration = Mock()
        self.assertTrue(self.node.connect())
        self.assertTrue(ports[0].wait_keepalives(1))
        baseline = len(ports[0].keepalives())

        released = threading.Event()

        def late_reply():
            time.sleep(1.0)
            self.node._on_text_received(MAP_ACK)
            released.set()

        acker = threading.Thread(target=late_reply, daemon=True)
        acker.start()
        try:
            started = time.monotonic()
            ok, _ = self.node._send_and_wait_text(
                'I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2', ('I2CMAP_OK:',), ('I2CMAP_ERR:',), timeout=3.0)
            elapsed = time.monotonic() - started
        finally:
            released.set()
            acker.join(3)

        self.assertTrue(ok)
        self.assertGreaterEqual(elapsed, 0.9)
        # Keepalives kept flowing during the ACK wait window.
        self.assertTrue(ports[0].wait_keepalives(baseline + 2, 1.0))

    def test_watchdog_trip_never_rearms_or_restores_output(self):
        ports = self.serial_environment()
        self.node._apply_runtime_configuration = Mock()
        self.assertTrue(self.node.connect())
        self.assertTrue(ports[0].wait_keepalives(1))
        self.node._on_text_received('WATCHDOG_TRIPPED')
        count = len(ports[0].keepalives())
        self.assertFalse(ports[0].wait_keepalives(count + 1, 0.7))
        result = self.node._execute_control_action('injection_on', {'speed': 60})
        self.assertFalse(result[0])
        self.assertEqual(self.node._controller_fault, 'watchdog_tripped')
        self.assertEqual(sum(b'WATCHDOG:ARM' in e[1] for e in ports[0].writes), 1)
        self.assertFalse(any(b'PUMP:ON' in e[1] or b'PUMP:SET' in e[1] for e in ports[0].writes))

    def test_initialization_does_not_queue_old_query_ack_before_setter(self):
        sent = []
        query_pending = []

        def send(command):
            sent.append(command)
            if command == 'I2CMAP?':
                query_pending.append('I2CMAP_OK:X=7,Y=6,Z=5,A=4,SPEC=3')
            elif command.startswith('I2CMAP:'):
                for old in query_pending:
                    self.node._on_text_received(old)
                self.node._on_text_received(MAP_ACK)
            elif command.startswith('ADSCFG:'):
                self.node._on_text_received(CFG_ACK)
            return True

        self.node.send_command = send
        self.node._apply_runtime_configuration()
        self.assertNotIn('I2CMAP?', sent)
        self.assertIsNone(self.node._last_spectro_txn_error)

    def test_setter_mismatch_fails_even_if_second_echo_matches(self):
        def send(command):
            self.node._on_text_received('I2CMAP_OK:X=7,Y=3,Z=4,A=7,SPEC=2')
            self.node._on_text_received(MAP_ACK)
            return True
        self.node.send_command = send
        self.assertFalse(self.node._apply_i2c_mapping())
        self.assertEqual(self.node._spectro_txn_phase, 'i2c_map_failed')

    def test_setter_correct_echo_passes(self):
        self.ack_sender()
        self.assertTrue(self.node._apply_i2c_mapping())

    def test_start_ack_is_not_measurement_freshness(self):
        for timestamp in (None, 123.0):
            self.node._last_valid_spectro_at = timestamp
            self.node._on_text_received('ADS_OK:START')
            self.assertEqual(self.node.spectro_state, 'starting')
            self.assertEqual(self.node._last_valid_spectro_at, timestamp)
        self.node._on_spectro_received({'valid': True, 'voltage': 1.2})
        self.assertGreater(self.node._last_valid_spectro_at, 123)
        self.assertEqual(self.node.spectro_state, 'acquiring')

    def test_first_frame_timeout_stops_ads_and_keeps_primary_error(self):
        sent = self.ack_sender(frame=False, stop='ADS_ERR:I2C')
        self.node._wait_first_valid_spectro_frame = Mock(return_value=False)
        success, message = self.node.prepare_and_start_spectrometer()
        self.assertFalse(success)
        self.assertIn('no valid frame', message)
        self.assertIn('ADSSTOP', sent)
        self.assertEqual(self.node._spectro_txn_phase, 'frame_timeout')
        self.assertEqual(self.node._last_spectro_txn_error, 'no valid spectrometer frame after start')
        self.assertIn('ADS_ERR:I2C', self.node._spectro_cleanup_error)
        self.assertFalse(self.node.latest_spectro['valid'])

    def test_success_accepts_frame_in_same_reader_batch_as_start_ack(self):
        self.ack_sender()
        self.assertTrue(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(self.node._spectro_txn_phase, 'running')
        self.assertIsNone(self.node._last_spectro_txn_error)

    def test_real_first_frame_deadline_stops_ads(self):
        sent = self.ack_sender(frame=False)
        started = time.monotonic()
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2.0)
        self.assertLess(elapsed, 3.5)
        self.assertEqual(sent[-1], 'ADSSTOP')
        self.assertEqual(self.node._spectro_txn_phase, 'frame_timeout')
        self.assertIsNone(self.node._spectro_cleanup_error)
        self.assertEqual(self.node.spectro_state, 'stopped')
        self.assertFalse(self.node.latest_spectro['valid'])

    def test_missing_start_and_cleanup_ack_are_bounded(self):
        sent = self.ack_sender(start=None, stop=None, frame=False)
        started = time.monotonic()
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 3.0)
        self.assertLess(elapsed, 4.5)
        self.assertEqual(sent[-2:], ['ADSSTART', 'ADSSTOP'])
        self.assertEqual(self.node._spectro_txn_phase, 'start_failed')
        self.assertEqual(self.node._last_spectro_txn_error, 'ADSSTART: timeout')
        self.assertEqual(self.node._spectro_cleanup_error, 'Spectrometer stop timeout')

    def test_stage_errors_are_terminal_and_fail_fast(self):
        for overrides, phase in (({'mapping': 'I2CMAP_ERR:CHANNEL_RANGE'}, 'i2c_map_failed'),
                                 ({'config': 'ADS_ERR:CONFIG'}, 'ads_config_failed'),
                                 ({'start': 'ADS_ERR:I2C'}, 'start_failed')):
            with self.subTest(phase=phase):
                sent = self.ack_sender(**overrides)
                self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
                self.assertEqual(self.node._spectro_txn_phase, phase)
                if phase != 'start_failed':
                    self.assertNotIn('ADSSTART', sent)

    def test_loop_outward_contract(self):
        for current, total, expected in ((2, 1, 1), (6, 5, 5), (12, 0, 12)):
            with self.subTest(total=total):
                self.node.automation_engine._current_loop = current
                self.node.automation_engine.loop_count = total
                self.node._publish_automation_status('finished' if total else 'running')
                payload = json.loads(self.pubs['/usv/automation_status'].messages[-1].data)
                self.assertEqual((payload['current_loop'], payload['total_loops']), (expected, total))
                self.assertEqual(self.node.automation_engine._current_loop, current)

    def test_query_consumes_old_mapping_before_setter_can_send(self):
        query_sent, setter_attempted, setter_sent = (threading.Event() for _ in range(3))
        results = []

        def send(command):
            if command == 'I2CMAP?':
                query_sent.set()
            elif command.startswith('I2CMAP:'):
                setter_sent.set()
                self.node._on_text_received(MAP_ACK)
            return True

        self.node.send_command = send
        query = threading.Thread(target=lambda: results.append(self.node._query_i2c_mapping()))

        def set_map():
            setter_attempted.set()
            results.append(self.node._execute_control_action('spectrometer_i2c_map', {})[0])

        setter = threading.Thread(target=set_map)
        query.start()
        try:
            self.assertTrue(query_sent.wait(1))
            setter.start()
            self.assertTrue(setter_attempted.wait(1))
            self.assertFalse(setter_sent.wait(0.1))
            self.node._on_text_received('I2CMAP_OK:X=7,Y=6,Z=5,A=4,SPEC=3')
        finally:
            query.join(3)
            if setter.ident:
                setter.join(3)
        self.assertEqual(results, [True, True])
        self.assertIsNone(self.node._last_spectro_txn_error)

    def test_concurrent_service_topic_and_control_actions_do_not_replace_transaction(self):
        first_sent, release = threading.Event(), threading.Event()
        arrivals = threading.Barrier(3)
        results, transactions = [], []
        normal_send = self.ack_sender()
        sender = self.node.send_command

        def send(command):
            with self.node._text_txn_lock:
                transactions.append(self.node._text_txn)
            if not first_sent.is_set():
                first_sent.set()
                release.wait(2)
            return sender(command)

        self.node.send_command = send
        first = threading.Thread(target=lambda: results.append(
            self.node._execute_control_action('spectrometer_i2c_map', {})[0]))

        def configure_topic():
            arrivals.wait()
            self.node._spectro_cmd_callback(self.String(json.dumps({'cmd': 'configure'})))
            results.append(self.node._last_spectro_txn_error is None)

        def start_service():
            arrivals.wait()
            results.append(self.node._spectro_start_callback(None).success)

        workers = [first, threading.Thread(target=configure_topic), threading.Thread(target=start_service)]
        first.start()
        self.assertTrue(first_sent.wait(1))
        pending = self.node._text_txn
        workers[1].start()
        workers[2].start()
        arrivals.wait()
        self.assertIs(self.node._text_txn, pending)
        release.set()
        for worker in workers:
            worker.join(4)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, [True, True, True])
        self.assertEqual(len(transactions), len({id(t) for t in transactions}))
        self.assertEqual(normal_send.count('ADSSTART'), 1)
        self.assertIsNone(self.node._text_txn)

    def test_matcher_ignores_stop_ack_while_waiting_for_start(self):
        def send(command):
            self.node._on_text_received('ADS_OK:STOP')
            self.assertFalse(self.node._text_txn['event'].is_set())
            self.node._on_text_received('ADS_OK:START')
            return True
        self.node.send_command = send
        self.assertEqual(self.node._send_and_wait_text('ADSSTART', ('ADS_OK:START',), ('ADS_ERR:',)),
                         (True, 'ADS_OK:START'))

    def test_reader_fault_after_start_cleans_up_without_recovery(self):
        sent = self.ack_sender(frame=False)

        def reader_error(timeout):
            with patch.object(self.node, '_reconnect_after_reader_error'):
                self.node._on_serial_reader_error(OSError('reader gone'))
            return False

        self.node._wait_first_valid_spectro_frame = reader_error
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertIn('ADSSTOP', sent)
        self.assertEqual(self.node._spectro_txn_phase, 'no_valid_frame')
        self.assertEqual(self.node._controller_fault, 'disconnected')

    def test_shutdown_exception_stops_worker(self):
        ports = self.serial_environment()
        self.node._apply_runtime_configuration = Mock()
        self.module.rospy.Rate = lambda hz: Mock(sleep=Mock(side_effect=RuntimeError('shutdown')))
        with self.assertRaisesRegex(RuntimeError, 'shutdown'):
            self.node.run()
        self.assertIsNone(self.node._keepalive_thread)
        self.assertFalse(ports[0].is_open)

    def test_real_engine_terminal_loop_is_clamped_only_in_telemetry(self):
        for count in (1, 5):
            with self.subTest(count=count):
                self.node.lab_mode_enabled = True
                self.node.pid_mode = False
                self.node.send_command = Mock(return_value=True)
                self.node.automation_engine.set_steps([{'name': 'one', 'interval': 0}])
                self.node.automation_engine.loop_count = count
                with patch.object(self.node, '_wait_seconds_with_pause', return_value=True):
                    self.assertTrue(self.node.automation_engine.start())
                    self.node.automation_engine._thread.join(2)
                payload = json.loads(self.pubs['/usv/automation_status'].messages[-1].data)
                self.assertEqual(payload['status'], 'finished')
                self.assertEqual((payload['current_loop'], payload['total_loops']), (count, count))
                self.assertEqual(self.node.automation_engine._current_loop, count + 1)


if __name__ == '__main__':
    unittest.main()
