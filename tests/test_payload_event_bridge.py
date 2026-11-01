"""Production trigger ledger -> bridge cache -> MAVLink2 chunk sender."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from command_fixtures import remote_command, wait_commands
from test_mavlink_command_compat import _load_script


class EventBridgeTests(unittest.TestCase):
    def test_request_terminal_chunks_and_old_session_are_isolated(self):
        trigger_module = _load_script('event_trigger', 'scripts/mavlink_trigger_node.py')
        bridge_module = _load_script('event_bridge', 'scripts/usv_mavlink_router_bridge.py')
        node = trigger_module.MAVLinkTriggerNode()
        with patch.object(bridge_module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = bridge_module.USVMavlinkRouterBridge()
        bridge._tcp_session = node._mavlink_tcp_session = 1
        chunks = []
        bridge._conn = SimpleNamespace(mav=SimpleNamespace(statustext_send=lambda *args: chunks.append(args)))
        bridge._mav_send_with_retry = lambda sender, label: sender() is None
        node._event_pub.publish = bridge._payload_event_cb
        calls = []
        node.handle_mavlink_command = lambda command, *args: calls.append(command) or True
        request = remote_command(node, 31010)
        node._mavlink_cmd_rx_cb(request)
        wait_commands(node)
        context = trigger_module.request_context(1, 1, 1, 31010, 255, 190)
        node._emit_request_event(context, 'terminal', 'failed', '测量过期' * 80,
                                 attempt_id='1234567890abcdef1234567890abcdef', preflight_phase='settling')
        node._mavlink_cmd_rx_cb(request)
        wait_commands(node)
        self.assertEqual(calls, [31010])
        bridge._send_payload_event()
        self.assertGreater(len(chunks), 1)
        self.assertEqual([chunk[3] for chunk in chunks], list(range(len(chunks))))
        self.assertEqual(len({chunk[2] for chunk in chunks}), 1)
        payload = b''.join(chunk[1] for chunk in chunks).split(b'\0')[0]
        event = json.loads(payload[6:])
        self.assertEqual(event['attempt_id'], '1234567890abcdef1234567890abcdef')
        self.assertEqual(event['phase'], 'terminal')
        self.assertEqual(event['outcome'], 'failed')
        self.assertLessEqual(len(event['reason']), 80)
        bridge._tcp_session = 2
        bridge._recent_events.clear()
        node._emit_request_event(context, 'request', 'accepted')
        self.assertFalse(bridge._recent_events)

    def test_lost_request_and_replies_retry_once_and_restart_never_reexecutes(self):
        trigger = _load_script('retry_trigger', 'scripts/mavlink_trigger_node.py')
        bridge_module = _load_script('retry_bridge', 'scripts/usv_mavlink_router_bridge.py')
        node = trigger.MAVLinkTriggerNode()
        with patch.object(bridge_module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = bridge_module.USVMavlinkRouterBridge()
        bridge._conn = SimpleNamespace(mav=SimpleNamespace())
        bridge._tcp_session = node._mavlink_tcp_session = 1
        bridge._executor_id = node._executor_id
        delivered, actions = [], []
        bridge._payload_command_pub.publish = lambda msg: delivered.append(msg)
        node._event_pub.publish = bridge._payload_event_cb
        node.handle_mavlink_command = lambda command, *args: actions.append(command) or True
        request = remote_command(node, 31014, 7)
        # The first network copy is lost before the bridge. Copies 2 and 3
        # retain the exact identity/parameters even when every ACK is lost.
        for _ in range(2):
            bridge._forward_payload_command(request.data)
            node._payload_command_cb(delivered[-1])
            wait_commands(node)
        self.assertEqual(actions, [31014])
        self.assertEqual(json.loads(delivered[0].data), json.loads(delivered[1].data))
        key = (255, 190, 1, 1)
        self.assertEqual(json.loads(bridge._recent_events[key]['text'][6:])['outcome'], 'accepted')
        # Trigger restarts while TCP and the bridge remain alive, before the
        # bridge sees its next capability. The carried executor still rejects.
        restarted = trigger.MAVLinkTriggerNode()
        restarted._mavlink_tcp_session = 1
        restarted._event_pub.publish = bridge._payload_event_cb
        restarted.handle_mavlink_command = lambda command, *args: actions.append(command) or True
        restarted._payload_command_cb(delivered[-1])
        wait_commands(restarted)
        self.assertEqual(actions, [31014])
        # After the capability heartbeat the bridge also rejects old identities.
        bridge._executor_id = restarted._executor_id
        count = len(delivered)
        bridge._forward_payload_command(request.data)
        self.assertEqual(len(delivered), count)
        self.assertEqual(json.loads(bridge._recent_events[key]['text'][6:])['reason'], 'executor_restarted')
        fresh = remote_command(restarted, 31014)
        fresh.data[7] = 2
        bridge._forward_payload_command(fresh.data)
        restarted._payload_command_cb(delivered[-1])
        wait_commands(restarted)
        self.assertEqual(actions, [31014, 31014])


class EventReplayExpiryTests(unittest.TestCase):
    def setUp(self):
        self.trigger = _load_script('expiry_trigger', 'scripts/mavlink_trigger_node.py')
        module = _load_script('expiry_bridge', 'scripts/usv_mavlink_router_bridge.py')
        self.node = self.trigger.MAVLinkTriggerNode()
        self.addCleanup(lambda: self.node._ledger().close())
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = module.USVMavlinkRouterBridge()
        self.bridge._tcp_session = self.node._mavlink_tcp_session = 1
        self.bridge._executor_id = self.node._executor_id
        self.chunks, self.sent = [], []
        self.bridge._conn = SimpleNamespace(mav=SimpleNamespace(
            statustext_send=lambda *args: self.chunks.append(args)))
        self.bridge._payload_command_pub.publish = self.node._payload_command_cb
        self.node._event_pub.publish = self.bridge._payload_event_cb
        self.node.handle_mavlink_command = Mock(return_value=True)
        self.now = 100.0
        clock = patch.object(module, 'time', SimpleNamespace(monotonic=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)

    def start(self, command=31010):
        request = remote_command(self.node, command)
        self.bridge._forward_payload_command(request.data)
        wait_commands(self.node)
        context = self.trigger.request_context(1, 1, 1, command, 255, 190)
        return request, context

    def send(self):
        self.chunks.clear()
        self.bridge._send_payload_event()
        if self.chunks:
            raw = b''.join(chunk[1] for chunk in self.chunks).split(b'\0')[0]
            event = json.loads(raw[6:])
            self.sent.append((self.now, event))
            return event

    def periodic_status(self, status):
        message = SimpleNamespace(data=json.dumps(status))
        # Pump status at 1 Hz and the production bridge at 2 Hz for 3 minutes.
        for tick in range(361):
            self.now = 100.0 + tick * 0.5
            if tick % 2 == 0:
                self.node._automation_status_cb(message)
            self.send()
        self.assertTrue(self.sent)
        self.assertLess(self.sent[-1][0], 130.0, 'periodic status reopened the expired replay window')
        self.assertFalse(self.bridge._recent_events)

    def test_periodic_completed_sampling_expires_and_explicit_retry_keeps_original_result(self):
        request, context = self.start()
        self.node._emit_request_event(context, 'terminal', 'succeeded', 'completed',
                                     attempt_id='a' * 32, preflight_phase='complete')
        terminal = dict(self.node._ledger().records['255:190:1:1']['event'])
        self.periodic_status(dict(
            status='stopped', terminal_reason='completed', preflight_phase='complete',
            sampling_context=dict(mavlink_request=context, attempt_id='a' * 32)))
        self.now += 0.5
        self.bridge._forward_payload_command(request.data)
        wait_commands(self.node)
        self.assertEqual(self.send(), terminal)
        self.node.handle_mavlink_command.assert_called_once()

    def test_unchanged_progress_expires_and_changed_progress_is_sent(self):
        _, context = self.start()
        status = dict(status='waiting', preflight_phase='settling',
                      sampling_context=dict(mavlink_request=context, attempt_id='a' * 32))
        self.periodic_status(status)
        previous = self.sent[-1][1]
        self.now += 0.5
        status['status'] = 'running'
        self.node._automation_status_cb(SimpleNamespace(data=json.dumps(status)))
        event = self.send()
        self.assertEqual(event['state'], 'running')
        self.assertGreater(event['seq'], previous['seq'])

    def test_periodic_calibration_terminal_does_not_reopen_expired_replay(self):
        _, context = self.start(31014)
        self.periodic_status(dict(calibration=dict(
            mavlink_request=context, attempt_id='a' * 32, active=False,
            outcome='succeeded', reason='completed', phase='complete')))
        self.assertEqual(self.sent[-1][1]['phase'], 'terminal')
        self.assertEqual(self.sent[-1][1]['outcome'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
