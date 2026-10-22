"""Production transport/bridge/trigger regressions with local I/O boundaries."""
import errno
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from command_fixtures import remote_command, wait_commands
from scripts.lib.mavlink_tcp_transport import MAVLinkTCPTransport
from scripts.lib.payload_protocol import request_context
from test_mavlink_command_compat import _load_script
from test_mavlink_tcp_transport import Port


class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.trigger_module = _load_script('recovery_trigger', 'scripts/mavlink_trigger_node.py')
        self.node = self.trigger_module.MAVLinkTriggerNode()
        self.module = _load_script('recovery_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(self.module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = self.module.USVMavlinkRouterBridge()
        self.bridge._tcp_session = self.node._mavlink_tcp_session = 84
        self.bridge._executor_id = self.node._executor_id
        self.bridge._executor_session = 84
        self.bridge._payload_command_pub.publish = self.node._payload_command_cb
        self.bridge._session_pub.publish = self.node._mavlink_session_cb
        self.node._event_pub.publish = self.bridge._payload_event_cb
        self.node.cmd_ack_pub.publish = self.bridge._cmd_ack_cb
        self.node._call_automation_service = Mock(return_value=True)
        self.node._bind_sample_position = Mock()
        self.frames = []
        self.attach_port(Port([]))

    def attach_port(self, port):
        self.port = port
        self.bridge._transport = MAVLinkTCPTransport(port, self.bridge._transport_counts)
        def send(kind, *args):
            self.bridge._transport.write((kind + repr(args)).encode('ascii'))
            self.frames.append((kind, args))
        self.bridge._conn = SimpleNamespace(close=port.close, mav=SimpleNamespace(
            command_ack_send=lambda *args: send('ack', *args),
            statustext_send=lambda *args: send('text', *args),
            named_value_float_send=lambda *args: send('nvf', *args)))

    def start(self):
        request = remote_command(self.node, 31010)
        self.bridge._forward_payload_command(request.data)
        wait_commands(self.node)
        self.assertTrue(self.node.is_sampling)
        return request

    def reconnect(self):
        self.attach_port(Port([]))
        self.bridge._tcp_session = 85
        self.node._mavlink_session_cb(SimpleNamespace(data=[85]))
        self.bridge._protocol_cb(SimpleNamespace(data=json.dumps({
            'version': 1, 'executor': self.node._executor_id,
            'tcp_session': 85, 'source_monotonic': time.monotonic()})))

    def event(self, request):
        key = (255, 190, int(request.data[8]), int(request.data[7]))
        return json.loads(self.bridge._recent_events[key]['text'][6:])

    def test_delayed_healthy_send_keeps_queues_session_and_task(self):
        request = self.start()
        self.bridge._pending_statustexts.append(('status', 5))
        self.bridge._pending_usv_done.append(17)
        before = self.node._command_generation
        self.bridge._transport.begin_cycle()
        time.sleep(0.04)
        self.bridge._flush_pending_sends()
        self.bridge._send_payload_event()
        self.assertFalse(self.port.closed)
        self.assertTrue(self.node.is_sampling)
        self.assertEqual(self.node._command_generation, before)
        self.assertEqual(len(self.bridge._pending_acks), 1)
        self.assertEqual(list(self.bridge._pending_usv_done), [17])
        self.assertTrue(self.bridge._recent_events[(255, 190, 1, 1)]['pending'])
        self.bridge._transport.begin_cycle()
        self.bridge._flush_pending_sends()
        self.bridge._send_payload_event()
        self.bridge._flush_pending_sends()
        self.assertFalse(self.bridge._pending_acks)
        self.assertFalse(self.bridge._pending_statustexts)
        self.assertFalse(self.bridge._pending_usv_done)
        self.assertEqual(self.event(request)['outcome'], 'accepted')
        self.assertFalse(self.bridge._recent_events[(255, 190, 1, 1)]['pending'])

    def test_partial_write_failure_stops_and_recovery_returns_original_terminal(self):
        request = self.start()
        attempt = self.node.current_sampling_context['attempt_id']
        self.attach_port(Port([2, OSError(errno.EPIPE, 'broken pipe')]))
        self.bridge._flush_pending_sends()
        wait_commands(self.node)
        self.assertTrue(self.port.closed)
        self.assertIsNone(self.bridge._conn)
        self.assertFalse(self.node.is_sampling)
        self.node._call_automation_service.assert_called_with('stop')
        terminal = self.node._ledger().records['255:190:1:1']['event']
        self.assertEqual(terminal['reason'], 'transport_session_lost')
        self.assertEqual(terminal['attempt_id'], attempt)
        self.bridge._retire_router('connect refused before a replacement exists')
        self.reconnect()
        self.bridge._recover_payload_results()
        self.assertEqual(self.event(request)['tcp_session'], 85)
        self.assertEqual(self.event(request)['outcome'], 'cancelled')
        # An actual same-identity retry returns the very same terminal too.
        request.data[9] = 85
        self.bridge._forward_payload_command(request.data)
        wait_commands(self.node)
        self.assertEqual(self.event(request)['seq'], terminal['seq'])
        self.assertEqual(self.node._call_automation_service.call_count, 2)  # one start, one stop
        self.assertEqual(sum(call.args == ('start',) for call in self.node._call_automation_service.call_args_list), 1)

    def test_deferred_event_tail_resumes_without_skipping_chunks_or_log_interleaving(self):
        request = self.start()
        original = self.bridge._conn.mav.statustext_send
        def send(*args):
            original(*args)
            if len(args) == 4 and args[3] == 1:
                self.bridge._transport.deadline = time.monotonic() - 1
        self.bridge._conn.mav.statustext_send = send
        self.bridge._transport.begin_cycle()
        self.bridge._send_payload_event()
        self.assertEqual(self.bridge._active_event_send['offset'], 100)
        first_id = self.bridge._active_event_send['id']
        self.assertFalse(self.port.closed)
        self.bridge._pending_statustexts.append(('ordinary log', 5))
        self.bridge._conn.mav.statustext_send = original
        self.bridge._transport.begin_cycle()
        self.bridge._flush_pending_sends()
        self.assertTrue(self.bridge._pending_statustexts)
        self.bridge._send_payload_event()
        chunks = [args for kind, args in self.frames if kind == 'text' and len(args) == 4]
        self.assertEqual([chunk[3] for chunk in chunks], list(range(len(chunks))))
        self.assertEqual({chunk[2] for chunk in chunks}, {first_id})
        payload = b''.join(chunk[1] for chunk in chunks).split(b'\0')[0]
        self.assertEqual(json.loads(payload[6:]), self.event(request))
        self.bridge._flush_pending_sends()
        self.assertFalse(self.bridge._pending_statustexts)

    def test_event_delayed_past_assembly_window_replays_from_chunk_zero(self):
        self.start()
        sender = self.bridge._conn.mav.statustext_send
        def send(*args):
            sender(*args)
            self.bridge._transport.deadline = time.monotonic() - 1
        self.bridge._conn.mav.statustext_send = send
        self.bridge._transport.begin_cycle()
        self.bridge._send_payload_event()
        old_id = self.bridge._active_event_send['id']
        self.bridge._active_event_send['started_at'] -= 1.1
        self.bridge._conn.mav.statustext_send = sender
        self.bridge._transport.begin_cycle()
        self.bridge._send_payload_event()
        chunks = [args for kind, args in self.frames if kind == 'text' and len(args) == 4]
        replay = [chunk for chunk in chunks if chunk[2] != old_id]
        self.assertEqual([chunk[3] for chunk in replay], list(range(len(replay))))
        self.assertIn(b'\0', replay[-1][1])

    def test_transport_loss_during_start_rpc_preserves_first_cancellation_reason(self):
        entered, release = threading.Event(), threading.Event()
        def service(action):
            if action == 'start':
                entered.set()
                self.assertTrue(release.wait(2))
                return False
            release.set()
            return True
        self.node._call_automation_service = Mock(side_effect=service)
        self.node._cleanup_sampling_attempt = Mock(return_value=(True, False))
        request = remote_command(self.node, 31010)
        try:
            self.bridge._forward_payload_command(request.data)
            self.assertTrue(entered.wait(1))
            self.bridge._retire_router('TCP EOF during start')
            wait_commands(self.node)
            self.reconnect()
            self.bridge._recover_payload_results()
            terminal = self.event(request)
            self.assertEqual((terminal['outcome'], terminal['reason']), ('cancelled', 'transport_session_lost'))
            self.assertEqual(self.node._call_automation_service.call_count, 2)
        finally:
            release.set()
            wait_commands(self.node)

    def test_recovery_before_stop_finishes_routes_the_later_terminal(self):
        request = self.start()
        entered, release = threading.Event(), threading.Event()
        def service(action):
            if action == 'stop':
                entered.set()
                self.assertTrue(release.wait(2))
            return True
        self.node._call_automation_service = Mock(side_effect=service)
        try:
            self.bridge._retire_router('TCP EOF')
            self.assertTrue(entered.wait(1))
            self.reconnect()
            self.bridge._recover_payload_results()
            self.assertEqual(self.event(request)['tcp_session'], 85)
            release.set()
            wait_commands(self.node)
            self.assertEqual(self.event(request)['phase'], 'terminal')
            self.assertEqual(self.event(request)['reason'], 'transport_session_lost')
        finally:
            release.set()
            wait_commands(self.node)

    def test_unknown_conflicting_or_old_executor_recovery_never_starts(self):
        request = remote_command(self.node, 31010)
        envelope = {'executor': self.node._executor_id, 'values': request.data, 'recover_only': True}
        self.node._payload_command_cb(SimpleNamespace(data=json.dumps(envelope)))
        self.node._call_automation_service.assert_not_called()
        self.bridge._forward_payload_command(request.data)
        wait_commands(self.node)
        self.assertTrue(self.node.is_sampling)
        calls = self.node._call_automation_service.call_count
        envelope['values'] = list(request.data)
        envelope['values'][1] = 9
        self.node._payload_command_cb(SimpleNamespace(data=json.dumps(envelope)))
        envelope['values'][1] = 0
        envelope['executor'] = '0' * 32
        self.node._payload_command_cb(SimpleNamespace(data=json.dumps(envelope)))
        self.assertEqual(self.node._call_automation_service.call_count, calls)

    def test_repeated_cycles_and_late_old_session_events_do_not_pollute_new_task(self):
        first = None
        for _ in range(8):
            request = self.start()
            first = first or request
            self.bridge._forward_payload_command(request.data)
            stop = remote_command(self.node, 31011)
            self.bridge._forward_payload_command(stop.data)
            wait_commands(self.node)
            self.assertEqual(self.event(request)['outcome'], 'cancelled')
            self.assertEqual(self.event(stop)['outcome'], 'accepted')
        old = self.event(first)
        current = self.start()
        current_event = self.event(current)
        for value in (old, dict(old, tcp_session=83, seq=old['seq'] + 100)):
            self.bridge._payload_event_cb(SimpleNamespace(data=json.dumps(value)))
        self.assertEqual(self.event(current), current_event)
        self.assertTrue(self.node.is_sampling)
        self.assertEqual(sum(call.args == ('start',) for call in self.node._call_automation_service.call_args_list), 9)


if __name__ == '__main__':
    unittest.main()
