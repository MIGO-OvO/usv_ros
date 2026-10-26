"""Startup/recovery through real pump methods with a fake detector UART."""
import json
import tempfile
import threading
import time
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_field_transactions as field
from test_mavlink_command_compat import _load_script as load_bridge


class SerialStartupRecoveryTests(unittest.TestCase):
    setUp = field.FieldTransactionTests.setUp

    def detector(self, *, arm=True, stale=False, stop_errors=0):
        ports, readers, commands = [], [], []
        def factory():
            port = field.FakeSerial()
            ports.append(port)
            return port
        self.enterContext(patch.object(self.module.serial, 'Serial', side_effect=factory))
        self.enterContext(patch.object(self.module, 'perform_detector_handshake',
                                      return_value=(True, 'DET_ID:USV_DETECTOR,CAP=WATCHDOG1')))
        reader = self.enterContext(patch.object(self.module, 'PumpSerialReader'))
        def started(**callbacks):
            readers.append(callbacks)
            def reply(data):
                nonlocal stop_errors
                command = data.decode().strip()
                commands.append(command)
                if command == 'WATCHDOG:ARM':
                    if stale:
                        callbacks['on_text']('WATCHDOG_TRIPPED')
                    if arm:
                        callbacks['on_text']('WATCHDOG_OK:ARM')
                elif command == 'PIDQUERY':
                    callbacks['on_text']('PIDPARAM:0.14,0.015,0.06,1,8')
                elif command == 'STOPALL':
                    callbacks['on_text']('STOPALL_OK')
                elif command == 'ADSSTOP':
                    stop_errors -= 1
                    callbacks['on_text']('ADS_ERR:STOP_I2C' if stop_errors >= 0 else 'ADS_OK:STOP')
                elif command.startswith('I2CMAP:'):
                    callbacks['on_text'](field.MAP_ACK)
                elif command.startswith('ADSCFG:'):
                    callbacks['on_text'](field.CFG_ACK.rsplit('PR=', 1)[0] + 'PR=' + command.rsplit('PR=', 1)[1])
            ports[-1].on_write = reply
        reader.return_value.start.side_effect = started
        return ports, readers, commands

    def test_arm_ack_is_required_before_configuration(self):
        ports, _, commands = self.detector(arm=False)
        wait = self.node._send_and_wait_text
        self.node._send_and_wait_text = lambda c, s, e, timeout=2: wait(c, s, e, timeout=0.04)
        self.assertFalse(self.node.connect())
        self.assertFalse(any(c.startswith('I2CMAP:') for c in commands))
        self.assertNotEqual(self.node._spectro_config_state, 'ready')

    def test_pre_arm_fault_is_drained_but_post_arm_fault_still_latches(self):
        _, readers, commands = self.detector(stale=True)
        self.assertTrue(self.node.connect())
        self.assertIsNone(self.node._controller_fault)
        self.assertEqual(self.node._spectro_config_state, 'ready')
        self.assertLess(commands.index('PIDQUERY'), commands.index('WATCHDOG:ARM'))
        readers[-1]['on_text']('WATCHDOG_TRIPPED')
        self.assertEqual(self.node._controller_fault, 'watchdog_tripped')

    def test_recovery_retries_ads_stop_without_reopening_or_starting(self):
        ports, _, commands = self.detector(stop_errors=2)
        self.node._controller_fault = 'watchdog_tripped'
        self.node.spectro_config['auto_start'] = True
        self.assertTrue(self.node.connect())
        self.assertEqual(commands.count('ADSSTOP'), 3)
        self.assertEqual(len(ports), 1)
        self.assertNotIn('ADSSTART', commands)
        self.assertIsNone(self.node._controller_fault)

    def test_persistent_stop_error_keeps_transport_for_next_retry(self):
        ports, _, commands = self.detector(stop_errors=3)
        self.node._controller_fault = 'watchdog_tripped'
        self.assertFalse(self.node.connect())
        self.assertIs(self.node.serial_conn, ports[0])
        self.assertTrue(ports[0].is_open)
        self.assertIn('ADS_ERR:STOP_I2C', self.node._serial_recovery_error)
        self.node._next_serial_recovery_at = 0
        self.node._retry_serial_connection()
        self.assertEqual(len(ports), 1)
        self.assertIsNone(self.node._controller_fault)
        self.assertFalse(self.node._serial_recovering)
        self.assertNotIn('ADSSTART', commands)

    def test_old_reader_cannot_fault_replacement_session(self):
        _, readers, _ = self.detector()
        self.assertTrue(self.node.connect())
        self.assertTrue(self.node.connect())
        readers[0]['on_text']('WATCHDOG_TRIPPED')
        self.assertIsNone(self.node._controller_fault)

    def test_failed_reply_barrier_reconnects_instead_of_retrying_poisoned_session(self):
        ports, _, commands = self.detector()
        self.assertTrue(self.node.connect())
        self.node._spectro_reply_sync_failed = True
        self.node._spectro_config_state = 'failed'
        self.node.spectro_config['auto_start'] = True
        self.node._next_serial_recovery_at = 0
        self.node._retry_serial_connection()
        self.assertEqual(len(ports), 2)
        self.assertFalse(self.node._spectro_reply_sync_failed)
        self.assertEqual(self.node._spectro_config_state, 'ready')
        self.assertNotIn('ADSSTART', commands)

    def test_maintenance_reconnect_does_not_auto_start_acquisition(self):
        _, _, commands = self.detector()
        self.assertTrue(self.node.connect())
        self.node.spectro_config['auto_start'] = True
        self.assertTrue(self.node._reconnect_callback(None).success)
        self.assertNotIn('ADSSTART', commands)

    def test_reader_failure_recovers_idle_and_invalidates_old_action(self):
        ports, _, commands = self.detector()
        self.assertTrue(self.node.connect())
        self.node.spectro_config['auto_start'] = True
        self.node.spectro_state = 'acquiring'
        token = self.node._action_cancel
        self.assertTrue(self.node._latch_serial_fault(ports[0]))
        self.node._reconnect_after_reader_error(ports[0])
        self.assertTrue(token.is_set())
        self.assertEqual(len(ports), 2)
        self.assertIsNone(self.node._controller_fault)
        self.assertNotIn('ADSSTART', commands)
        self.assertFalse(self.node._automation_is_active())

    def test_late_arm_ack_before_barrier_cannot_confirm_next_arm(self):
        ports, readers, _ = self.detector(arm=False)
        wait = self.node._send_and_wait_text
        self.node._send_and_wait_text = lambda c, s, e, timeout=2: wait(c, s, e, timeout=0.04)
        self.assertFalse(self.node.connect())
        reply = ports[0].on_write
        def late_ack(data):
            if data == b'PIDQUERY\r\n':
                readers[0]['on_text']('WATCHDOG_OK:ARM')
            reply(data)
        ports[0].on_write = late_ack
        self.node._next_serial_recovery_at = 0
        self.node._retry_serial_connection()
        self.assertFalse(self.node._watchdog_armed)
        self.assertTrue(self.node._serial_recovering)

    def test_stop_during_arm_cancels_configuration(self):
        _, _, commands = self.detector()
        wait = self.node._send_and_wait_text
        def stop_before_arm(command, success, errors, timeout=2):
            if command == 'WATCHDOG:ARM':
                self.node._request_output_stop()
            return wait(command, success, errors, timeout=0.04)
        self.node._send_and_wait_text = stop_before_arm
        try:
            self.assertFalse(self.node.connect())
        finally:
            self.node._finish_output_stop()
        self.assertNotIn('WATCHDOG:ARM', commands)
        self.assertFalse(any(c.startswith('ADSCFG:') for c in commands))

    def test_fault_after_arm_ack_is_not_cleared_by_recovery(self):
        ports, readers, commands = self.detector()
        wait = self.node._send_and_wait_text
        def trip_after_arm(command, success, errors, timeout=2):
            result = wait(command, success, errors, timeout)
            if command == 'WATCHDOG:ARM':
                readers[-1]['on_text']('WATCHDOG_TRIPPED')
            return result
        self.node._send_and_wait_text = trip_after_arm
        self.assertFalse(self.node.connect())
        self.assertEqual(self.node._controller_fault, 'watchdog_tripped')
        self.assertTrue(ports[0].is_open)
        self.assertFalse(any(c.startswith('ADSCFG:') for c in commands))

    def test_failed_configuration_retries_without_reopening_or_auto_start(self):
        ports, _, commands = self.detector()
        self.assertTrue(self.node.connect())
        self.node._spectro_config_state = 'failed'
        self.node.spectro_config['auto_start'] = True
        self.node._next_serial_recovery_at = 0
        self.node._retry_serial_connection()
        self.assertEqual(len(ports), 1)
        self.assertEqual(self.node._spectro_config_state, 'ready')
        self.assertNotIn('ADSSTART', commands)

    def test_timed_out_mapping_query_still_requires_reply_barrier(self):
        self.node.send_command = Mock(return_value=True)
        self.node._controller_fault = 'reconnecting'
        ok, message = self.node._send_and_wait_text(
            'I2CMAP?', ('I2CMAP_OK:',), ('I2CMAP_ERR:',), timeout=0.02)
        self.assertFalse(ok)
        self.assertEqual(message, 'timeout')
        self.assertTrue(self.node._spectro_reply_sync_required)

    def test_recovery_keepalive_survives_slow_stop_confirmation(self):
        ports, _, _ = self.detector()
        self.node._controller_fault = 'watchdog_tripped'
        entered, release = threading.Event(), threading.Event()
        confirm = self.node._confirm_all_outputs_stopped
        def slow_confirm():
            entered.set()
            release.wait(4)
            return confirm()
        self.node._confirm_all_outputs_stopped = slow_confirm
        result = []
        thread = threading.Thread(target=lambda: result.append(self.node.connect()))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(ports[0].wait_keepalives(6, 3.5))
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(result, [True])

    def test_first_open_failure_does_not_exit_node(self):
        ports, _, _ = self.detector()
        factory = self.module.serial.Serial.side_effect
        def first_missing():
            port = factory()
            if len(ports) == 1:
                port.open = Mock(side_effect=self.module.serial.SerialException('device not ready'))
            return port
        self.module.serial.Serial.side_effect = first_missing
        self.module.rospy.Rate = lambda hz: Mock(sleep=Mock(side_effect=RuntimeError('end test')))
        with self.assertRaisesRegex(RuntimeError, 'end test'):
            self.node._run_connected()
        self.assertEqual(len(ports), 2)
        self.assertTrue(ports[-1].is_open)
        self.assertIsNone(self.node._controller_fault)

    def test_saved_hardware_is_loaded_before_first_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sampling_config.json'
            path.write_text(json.dumps({'hardware': {'pump_serial_port': '/dev/serial/by-id/detector',
                'pump_baudrate': 57600, 'pump_timeout': 2, 'i2c_mapping': {'X': 0, 'Y': 3, 'Z': 4, 'A': 7},
                'spectro_channel': 2, 'auto_start': False}}), encoding='utf-8')
            with patch.object(self.module, 'CONFIG_FILE', str(path)):
                node = self.module.PumpControlNode()
            self.addCleanup(node.disconnect)
            self.addCleanup(node.injection_pump_worker.stop)
            self.assertEqual(node.serial_port, '/dev/serial/by-id/detector')
            self.assertEqual(node.baudrate, 57600)
            self.assertEqual(node.i2c_mapping['spectro_channel'], 2)


class RecoveryStatusTests(unittest.TestCase):
    def test_fault_is_not_overwritten_by_idle_and_clears_on_recovery(self):
        module = load_bridge('startup_status_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            node = module.USVMavlinkRouterBridge()
        data = {'status': 'stopped', 'running': False, 'controller_fault': 'watchdog_tripped',
                'serial_connected': False, 'source_monotonic': time.monotonic()}
        node._automation_status_cb(SimpleNamespace(data=json.dumps(data)))
        self.assertEqual(node._status_code, 3)
        node._mission_status_cb(SimpleNamespace(data='IDLE'))
        self.assertEqual(node._status_code, 3)
        data.update(controller_fault=None, serial_connected=True, source_monotonic=time.monotonic())
        node._automation_status_cb(SimpleNamespace(data=json.dumps(data)))
        self.assertEqual(node._status_code, 0)


if __name__ == '__main__':
    unittest.main()
