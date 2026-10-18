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


if __name__ == '__main__':
    unittest.main()
