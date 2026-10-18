"""The STOP hardware write must precede completion of an ADS transaction."""
import threading
import time
import unittest

import test_automation_preflight as preparation
import test_field_transactions as field
from test_mavlink_command_compat import _load_script
from types import SimpleNamespace
from command_fixtures import remote_command, wait_commands


class StopDuringAdsTests(unittest.TestCase):
    def test_stop_preempts_every_ads_wait_at_real_serial_boundary(self):
        for phase in ('mapping', 'config', 'start', 'frame', 'settling', 'backoff'):
            with self.subTest(phase=phase):
                fixture = field.FieldTransactionTests()
                fixture.setUp()
                node = fixture.node
                port = field.FakeSerial()
                port.open()
                node.serial_conn = port
                entered, stopped = threading.Event(), threading.Event()
                write = port.write
                def receive(data):
                    count = write(data)
                    command = data.decode().strip()
                    if command == 'STOPALL':
                        stopped.set()
                    reply = None
                    blocked = ((phase == 'mapping' and command.startswith('I2CMAP:'))
                               or (phase == 'config' and command.startswith('ADSCFG:'))
                               or (phase == 'start' and command == 'ADSSTART'))
                    if blocked:
                        entered.set()
                    elif command.startswith('I2CMAP:'):
                        reply = 'I2CMAP_ERR:I2C' if phase == 'backoff' else field.MAP_ACK
                    elif command.startswith('ADSCFG:'):
                        reply = field.CFG_ACK
                    elif command == 'ADSSTART':
                        reply = 'ADS_OK:START'
                    elif command == 'ADSSTOP':
                        reply = 'ADS_OK:STOP'
                    elif command == 'ADSSTATUS?':
                        reply = 'ADS_STATUS:STOPPED,CH=2'
                    if reply:
                        node._on_text_received(reply)
                    if (phase == 'frame' and command == 'ADSSTART') or (phase == 'backoff' and command == 'ADSSTATUS?'):
                        entered.set()
                    return count
                port.write = receive
                publish = node._publish_automation_status
                def observe(status):
                    publish(status)
                    if phase == 'settling' and node._spectro_txn_phase == 'settling':
                        entered.set()
                node._publish_automation_status = observe
                if phase == 'settling':
                    node._spectro_ready_after = time.monotonic() + 2
                result = []
                start = threading.Thread(target=lambda: result.append(
                    node._execute_control_action('spectrometer_start', {})))
                stop = threading.Thread(target=lambda: node._execute_control_action('manual_stop_all', {}))
                try:
                    start.start()
                    self.assertTrue(entered.wait(1), phase)
                    stop.start()
                    self.assertTrue(stopped.wait(0.25), phase)
                finally:
                    start.join(4)
                    if stop.ident:
                        stop.join(4)
                    fixture.doCleanups()
                self.assertFalse(start.is_alive() or stop.is_alive())
                self.assertFalse(result[0][0])
                commands = [data.decode().strip() for _, data, _ in port.writes]
                after_stop = commands[commands.index('STOPALL') + 1:]
                self.assertFalse(any(c == 'ADSSTART' or c.startswith(('I2CMAP:', 'ADSCFG:', 'PUMP:ON', 'PUMP:SET:60'))
                                     for c in after_stop), after_stop)

    def test_late_rpc_cannot_start_after_stop(self):
        fixture = preparation.PreflightWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        node = fixture.node
        issued = time.monotonic()
        node._execute_control_action('manual_stop_all', {})
        before = list(fixture.commands)
        for action in ('injection_on', 'spectrometer_start', 'automation_start'):
            self.assertFalse(node._execute_control_action(action, {'_issued_monotonic': issued})[0])
        self.assertEqual(fixture.commands, before)

    def test_survey_stop_does_not_wait_for_start_rpc_lock(self):
        module = _load_script('survey_stop_dispatch', 'scripts/mavlink_trigger_node.py')
        node = module.MAVLinkTriggerNode()
        node._survey_active = True
        node.current_sampling_context = {'source': 'survey', 'attempt_id': 'owned'}
        stopped = threading.Event()
        node._cleanup_sampling_attempt = lambda *args: (stopped.set() or True, False)
        with node._sampling_control_lock:
            stop = threading.Thread(target=node._stop_survey)
            stop.start()
            self.assertTrue(stopped.wait(0.25))
        stop.join(2)

    def test_subscriber_can_deliver_stop_while_start_service_is_blocked(self):
        module = _load_script('stop_dispatch_trigger', 'scripts/mavlink_trigger_node.py')
        node = module.MAVLinkTriggerNode()
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        replies = []
        node._send_command_ack = lambda *args: replies.append(args)

        def handle(command, *args):
            if command == 31018:
                entered.set()
                release.wait(3)
            elif command == 31011:
                stopped.set()
            return True
        node.handle_mavlink_command = handle

        def deliver():
            for command in (31018, 31011):
                node._mavlink_cmd_rx_cb(remote_command(node, command))
                if command == 31018:
                    entered.wait(1)
        receiver = threading.Thread(target=deliver)
        try:
            receiver.start()
            self.assertTrue(entered.wait(1))
            self.assertTrue(stopped.wait(0.25), 'subscriber is blocked in the first service')
        finally:
            release.set()
            receiver.join(3)
            wait_commands(node)
        self.assertIn((31018, module.MAV_RESULT_FAILED, 255, 190), replies)

    def test_stop_preempts_i2c_ack_wait(self):
        fixture = preparation.PreflightWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        node = fixture.node
        node.spectro_config['enabled'] = True
        fixture.module.SPECTRO_INIT_ATTEMPTS = 1
        entered, stopped = threading.Event(), threading.Event()
        send = fixture.send

        def withholding_ack(command, **kwargs):
            if command.startswith('I2CMAP:'):
                entered.set()
                return True
            if command.strip() == 'STOPALL':
                stopped.set()
            return send(command)

        node.send_command = withholding_ack
        start_result, stop_result = [], []
        start = threading.Thread(target=lambda: start_result.append(
            node._execute_control_action('spectrometer_start', {})))
        stop = threading.Thread(target=lambda: stop_result.append(
            node._execute_control_action('manual_stop_all', {})))
        try:
            start.start()
            self.assertTrue(entered.wait(1))
            stop.start()
            self.assertTrue(stopped.wait(0.25), 'STOPALL blocked behind ordinary ADS ACK wait')
        finally:
            start.join(5)
            if stop.ident:
                stop.join(5)
        self.assertFalse(start.is_alive() or stop.is_alive())
        self.assertFalse(start_result[0][0])
        self.assertNotIn('ADSSTART', fixture.commands)


if __name__ == '__main__':
    unittest.main()
