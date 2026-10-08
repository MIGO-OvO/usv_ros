"""Cold serial sessions exercise real initialization and ACK/frame callbacks."""
import json
import threading
import time
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import test_field_transactions as field
from test_hardware_runtime_sync import _load_script, RecordingSocket


class SpectrometerColdStartTests(unittest.TestCase):
    setUp = field.FieldTransactionTests.setUp
    serial_environment = field.FieldTransactionTests.serial_environment
    ack_sender = field.FieldTransactionTests.ack_sender

    def status(self):
        return json.loads(self.pubs['/usv/automation_status'].messages[-1].data)

    def test_connect_reports_serial_separately_from_failed_configuration(self):
        self.serial_environment()
        self.ack_sender(mapping='I2CMAP_ERR:CHANNEL_RANGE')
        self.assertTrue(self.node.connect())  # transport remains available
        self.assertTrue(self.status()['serial_connected'])
        self.assertEqual(self.status()['spectrometer_config_state'], 'failed')
        self.assertIn('I2CMAP', self.status()['spectrometer_last_txn_error'])
        states = [m.data for m in self.pubs['/usv/pump_status'].messages]
        self.assertIn('serial_connected', states)
        self.assertNotEqual(states[-1], 'connected')

    def test_one_click_recovers_two_transient_i2c_start_failures(self):
        sent = self.ack_sender()
        sender = self.node.send_command
        attempts = []
        def send(command):
            if command == 'ADSSTART':
                attempts.append(command)
                if len(attempts) <= 2:
                    sent.append(command)
                    self.node._on_text_received('ADS_ERR:I2C')
                    return True
            if command == 'ADSSTATUS?':
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
                return True
            return sender(command)
        self.node.send_command = send
        self.assertTrue(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sum(c.startswith('I2CMAP:') for c in sent), 3)
        self.assertEqual(sum(c.startswith('ADSCFG:') for c in sent), 3)
        self.assertEqual(self.status()['spectrometer_txn_attempt'], 3)
        self.assertEqual(len(self.status()['spectrometer_retry_errors']), 2)
        self.assertEqual(self.status()['spectrometer_config_state'], 'ready')
        self.assertIsNone(self.status()['spectrometer_last_txn_error'])

    def test_retry_ack_mismatch_is_failure_and_never_starts(self):
        sent = self.ack_sender()
        sender = self.node.send_command
        maps = []
        def send(command):
            if command.startswith('I2CMAP:'):
                maps.append(command)
                if len(maps) == 1:
                    self.node._on_text_received('I2CMAP_ERR:I2C')
                else:
                    self.node._on_text_received('I2CMAP_OK:X=7,Y=3,Z=4,A=7,SPEC=2')
                return True
            if command == 'ADSSTATUS?':
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
                return True
            return sender(command)
        self.node.send_command = send
        ok, error = self.node.prepare_and_start_spectrometer()
        self.assertFalse(ok)
        self.assertEqual(len(maps), 2)
        self.assertIn('I2CMAP mismatch', error)
        self.assertNotIn('ADSSTART', sent)

    def test_start_ack_state_is_applied_before_transaction_wakes(self):
        self.ack_sender()
        publish = self.node._publish_spectro_status
        def on_status(state):
            if state == 'starting':
                self.assertFalse(self.node._text_txn['event'].is_set())
                self.assertIsNotNone(self.node._spectro_start_ack_at)
            return publish(state)
        self.node._publish_spectro_status = on_status
        self.assertTrue(self.node.prepare_and_start_spectrometer()[0])

    def test_cold_frame_grace_does_not_relax_runtime_freshness(self):
        sent = self.ack_sender(frame=False)
        self.node._spectro_cold_start_pending = True
        now = [100.0]
        def sleep(seconds):
            now[0] += seconds
            self.node._check_spectro_freshness()
            if now[0] >= 102.6 and self.node._last_valid_spectro_at is None:
                self.node._on_spectro_received({'valid': True, 'voltage': 1.2})
        clock = types.SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0], sleep=sleep)
        with patch.object(self.module, 'time', clock):
            self.assertTrue(self.node.prepare_and_start_spectrometer()[0])
            self.assertNotIn('ADSSTOP', sent)
            self.assertFalse(self.node._spectro_cold_start_pending)
            now[0] += 2.1
            self.node._check_spectro_freshness()
            self.assertEqual(self.node.spectro_state, 'stale')

    def test_disconnected_start_names_serial_stage(self):
        self.node.send_command = Mock(return_value=False)
        ok, error = self.node.prepare_and_start_spectrometer()
        self.assertFalse(ok)
        self.assertIn('serial', error.lower())
        self.assertEqual(self.status()['spectrometer_txn_phase'], 'serial_failed')

    def test_configuration_starts_after_serial_settle_and_reader_ready(self):
        self.serial_environment()
        self.ack_sender()
        sender = self.node.send_command
        def send(command):
            if command.startswith('I2CMAP:'):
                self.assertGreaterEqual(time.monotonic(), self.node._spectro_ready_after)
                self.node.serial_reader.start.assert_called_once()
                self.assertTrue(self.node._keepalive_thread.is_alive())
            return sender(command)
        self.node.send_command = send
        started = time.monotonic()
        self.assertTrue(self.node.connect())
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        self.assertEqual(self.status()['spectrometer_config_state'], 'ready')
        self.assertEqual(self.status()['spectrometer_state'], 'idle')

    def test_delayed_i2c_ack_is_verified_without_retry(self):
        self.ack_sender()
        sender = self.node.send_command
        def send(command):
            if command.startswith('I2CMAP:'):
                timer = threading.Timer(0.1, self.node._on_text_received, args=(field.MAP_ACK,))
                timer.start()
                self.addCleanup(timer.join)
                return True
            return sender(command)
        self.node.send_command = send
        self.assertTrue(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(self.status()['spectrometer_txn_attempt'], 1)

    def test_late_ack_is_drained_before_retry_and_new_ack_is_still_required(self):
        self.ack_sender()
        sender = self.node.send_command
        configs = []
        late_seen = []
        def send(command):
            if command.startswith('ADSCFG:'):
                configs.append(command)
                if len(configs) == 1:
                    return True  # first command completes after its ACK deadline
                self.node._on_text_received('ADS_OK:CFG,CH=7,ADDR=0x40,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=90')
                return True
            if command == 'ADSSTATUS?':
                self.node._on_text_received(field.CFG_ACK)
                late_seen.append(self.node._text_txn['event'].is_set())
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
                return True
            return sender(command)
        self.node.send_command = send
        wait = self.node._send_and_wait_text
        def short_deadline(command, success, errors, timeout=2.0):
            return wait(command, success, errors, timeout=0.04)
        self.node._send_and_wait_text = short_deadline
        ok, error = self.node.prepare_and_start_spectrometer()
        self.assertFalse(ok)
        self.assertEqual(late_seen, [False])
        self.assertEqual(len(configs), 2)
        self.assertIn('ADSCFG mismatch', error)
        self.assertEqual(self.status()['spectrometer_config_state'], 'failed')

    def test_retry_exhaustion_is_bounded_and_preserves_stage(self):
        sent = self.ack_sender(start='ADS_ERR:I2C')
        sender = self.node.send_command
        def send(command):
            if command == 'ADSSTATUS?':
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
                return True
            return sender(command)
        self.node.send_command = send
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(sent.count('ADSSTART'), 3)
        self.assertEqual(sent.count('ADSSTOP'), 3)
        self.assertEqual(self.status()['spectrometer_txn_phase'], 'start_failed')
        self.assertEqual(self.status()['spectrometer_last_txn_error'], 'ADSSTART: ADS_ERR:I2C')

    def test_failed_cleanup_forbids_retry(self):
        sent = self.ack_sender(start='ADS_ERR:I2C', stop='ADS_ERR:I2C')
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(sent.count('ADSSTART'), 1)
        self.assertEqual(self.node._spectro_cleanup_error, 'ADS_ERR:I2C')

    def test_cold_no_valid_frame_remains_bounded_and_next_start_can_recover(self):
        sent = self.ack_sender(frame=False)
        self.node._spectro_cold_start_pending = True
        now = [100.0]
        def sleep(seconds):
            now[0] += seconds
            self.node._on_spectro_received({'valid': False, 'i2c_error': True})
        clock = types.SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0], sleep=sleep)
        with patch.object(self.module, 'time', clock):
            self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertGreaterEqual(now[0], 104.0)
        self.assertLess(now[0], 104.1)
        self.assertEqual(sent[-1], 'ADSSTOP')
        self.assertEqual(self.status()['spectrometer_txn_phase'], 'frame_timeout')
        self.ack_sender()
        self.assertTrue(self.node.prepare_and_start_spectrometer()[0])
        self.assertIsNone(self.status()['spectrometer_last_txn_error'])

    def test_late_start_ack_drains_before_new_start_and_does_not_supply_its_frame(self):
        sent = self.ack_sender(frame=False)
        sender = self.node.send_command
        starts = []
        def send(command):
            if command == 'ADSSTART':
                starts.append(command)
                if len(starts) == 1:
                    return True
            if command == 'ADSSTOP':
                self.node._on_text_received('ADS_OK:START')
                self.node._on_spectro_received({'valid': True, 'voltage': 1.2})
            if command == 'ADSSTATUS?':
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
                return True
            return sender(command)
        self.node.send_command = send
        wait = self.node._send_and_wait_text
        self.node._send_and_wait_text = lambda cmd, ok, err, timeout=2.0: wait(cmd, ok, err, timeout=0.02)
        self.node._wait_first_valid_spectro_frame = Mock(return_value=False)
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(len(starts), 2)
        self.assertEqual(self.node._spectro_txn_phase, 'frame_timeout')
        self.assertIsNone(self.node._last_valid_spectro_at)

    def test_failed_barrier_requires_reconnect_before_another_start(self):
        sent = self.ack_sender(mapping=None)
        wait = self.node._send_and_wait_text
        self.node._send_and_wait_text = lambda cmd, ok, err, timeout=2.0: wait(cmd, ok, err, timeout=0.02)
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertIn('retry synchronization', self.node._last_spectro_txn_error)
        before = list(sent)
        self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertEqual(sent, before)
        self.assertIn('reconnect required', self.node._last_spectro_txn_error)

    def test_ack_after_send_deadline_cannot_count_as_success(self):
        now = [100.0]
        def send(command):
            now[0] += 2.1
            self.node._on_text_received(field.MAP_ACK)
            return True
        self.node.send_command = send
        with patch.object(self.module.time, 'monotonic', side_effect=lambda: now[0]):
            self.assertEqual(self.node._send_and_wait_text('I2CMAP:...', ('I2CMAP_OK:',), ('I2CMAP_ERR:',)),
                             (False, 'timeout'))

    def test_ads_configuration_without_verified_mapping_is_not_ready(self):
        self.ack_sender()
        self.assertTrue(self.node._apply_spectro_config())
        self.assertEqual(self.node._spectro_config_state, 'ads_configured')

    def test_warm_start_does_not_receive_cold_first_frame_grace(self):
        self.ack_sender(frame=False)
        now = [100.0]
        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 102.6:
                self.node._on_spectro_received({'valid': True, 'voltage': 1.2})
        clock = types.SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0], sleep=sleep)
        with patch.object(self.module, 'time', clock):
            self.assertFalse(self.node.prepare_and_start_spectrometer()[0])
        self.assertLess(now[0], 102.1)
        self.assertEqual(self.node._spectro_txn_phase, 'frame_timeout')

    def test_frame_received_after_first_frame_deadline_is_not_success(self):
        self.node._spectro_start_ack_at = 100.0
        self.node._last_valid_spectro_at = 104.01
        self.node.spectro_state = 'acquiring'
        with patch.object(self.module.time, 'monotonic', return_value=104.01):
            self.assertFalse(self.node._wait_first_valid_spectro_frame(timeout=4.0))

    def test_standalone_configuration_drains_late_ack_and_requires_new_ack(self):
        sent = []
        self.node._spectro_reply_sync_required = True
        self.node._spectro_i2c_verified = True
        def send(command):
            sent.append(command)
            if command == 'ADSSTATUS?':
                self.node._on_text_received(field.CFG_ACK)
                self.assertFalse(self.node._text_txn['event'].is_set())
                self.node._on_text_received('ADS_STATUS:STOPPED,CH=2')
            return True  # the new ADSCFG never receives an ACK
        self.node.send_command = send
        wait = self.node._send_and_wait_text
        self.node._send_and_wait_text = lambda cmd, ok, err, timeout=2.0: wait(cmd, ok, err, timeout=0.02)
        self.assertFalse(self.node._apply_spectro_config())
        self.assertEqual(sent[0], 'ADSSTATUS?')
        self.assertTrue(sent[1].startswith('ADSCFG:'))
        self.assertEqual(self.node._spectro_config_state, 'failed')
        self.assertEqual(self.node._last_spectro_txn_error, 'ADSCFG: timeout')

    def test_standalone_and_raw_setters_cannot_bypass_failed_barrier(self):
        self.node._spectro_reply_sync_failed = True
        self.node.send_command = Mock(return_value=True)
        self.assertFalse(self.node._apply_i2c_mapping())
        self.assertFalse(self.node._apply_spectro_config())
        for command in ('I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2', 'ADSCFG:CH=2', 'ADSSTART'):
            self.node._cmd_callback(self.String(command))
        self.node.send_command.assert_not_called()


class SpectrometerWebStatusTests(unittest.TestCase):
    def test_socket_snapshot_keeps_transport_config_acquisition_and_error(self):
        module, _, String = _load_script('web_spectro_cold_status', 'scripts/web_config_server.py')
        with tempfile.TemporaryDirectory() as directory:
            manager = module.ConfigManager
            module.ConfigManager = lambda: manager(str(Path(directory) / 'sampling_config.json'))
            server = module.WebConfigServer(standalone=False)
            server.socketio = RecordingSocket()
            diagnostics = {'serial_connected': True, 'spectrometer_config_state': 'failed',
                           'spectrometer_state': 'idle', 'spectrometer_txn_phase': 'ads_config_failed',
                           'spectrometer_last_txn_error': 'ADSCFG: timeout', 'spectrometer_txn_attempt': 2,
                           'spectrometer_retry_errors': [{'attempt': 1, 'phase': 'i2c_map_failed', 'error': 'I2CMAP: timeout'}],
                           'spectrometer_age_s': None, 'owner_age_s': None, 'controller_fault': None,
                           'terminal_reason': None}
            server._automation_status_cb(String(json.dumps(diagnostics)))
            checks = iter([False, True])
            module.rospy.is_shutdown = lambda: next(checks, True)
            server._data_push_loop()
            status = next(payload for event, payload in server.socketio.events if event == 'status')
            for key, expected in diagnostics.items():
                self.assertEqual(status[key], expected, key)


if __name__ == '__main__':
    unittest.main()
