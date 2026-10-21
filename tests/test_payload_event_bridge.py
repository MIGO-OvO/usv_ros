"""Production trigger ledger -> bridge cache -> MAVLink2 chunk sender."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
