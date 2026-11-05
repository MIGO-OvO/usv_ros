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
from test_mavlink_command_compat import _load_script, FakeCommandLong, FakeConnection
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
            timesync_send=lambda *args: send('timesync', *args),
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

    def test_state_read_recovers_only_the_request_owners_original_result(self):
        request = self.start()
        context = request_context(1, 1, 84, 31010, 255, 190)
        self.node._emit_request_event(context, 'terminal', 'failed', 'measurement_stale',
                                     attempt_id=self.node.current_sampling_context['attempt_id'])
        original = self.event(request)
        self.bridge._recent_events.clear()
        for component in (191, 0):
            for sender, command, recovered in ((253, 31010, False), (255, 31014, False), (255, 31010, True)):
                self.bridge._recent_events.clear()
                query = FakeCommandLong(512, 251, request.data[8], 1, component, sender, 190)
                query.param3, query.param4 = request.data[7], command
                self.bridge._conn.recv_match = FakeConnection([query]).recv_match
                self.bridge._receive_mavlink_messages()
                self.bridge._recover_payload_results()
                self.assertEqual(bool(self.bridge._recent_events), recovered)
        self.assertEqual(self.event(request), original)
        self.node._call_automation_service.assert_called_once_with('start')
        self.assertIn((512, 0, 255, 190), self.bridge._pending_acks)

    def test_operator_state_read_requests_link_recovery_without_starting_payload(self):
        self.bridge._link_recovery_pub = Mock()
        for target, flag, expected in ((191, 0, 0), (191, 1, 1), (0, 1, 1), (1, 1, 0)):
            with self.subTest(target=target, flag=flag):
                self.bridge._link_recovery_pub.reset_mock()
                query = FakeCommandLong(512, 251, 0, 1, target, 255, 190)
                query.param5 = flag
                self.bridge._conn.recv_match = FakeConnection([query]).recv_match
                self.bridge._receive_mavlink_messages()
                self.assertEqual(self.bridge._link_recovery_pub.publish.call_count, expected)
        self.node._call_automation_service.assert_not_called()

    def test_silent_router_session_is_retired_without_replaying_start(self):
        request = self.start()
        self.bridge._router_connected_at = 100.0
        self.bridge._gps_cache.records['HEARTBEAT'] = {'received_monotonic': 100.0}
        self.bridge._conn.recv_match = FakeConnection([]).recv_match
        with patch.object(self.module.time, 'monotonic', return_value=111.0), \
                patch.object(self.module.rospy, 'is_shutdown', side_effect=[False, True]), \
                patch.object(self.bridge, '_send_heartbeat'), \
                patch.object(self.bridge, '_send_payload'), \
                patch.object(self.bridge, '_publish_gps_evidence'):
            self.bridge.run()
        wait_commands(self.node)
        self.assertTrue(self.port.closed)
        self.assertIsNone(self.bridge._conn)
        self.assertIn('heartbeat', self.bridge._reconnect_reason.lower())
        self.reconnect()
        self.bridge._recover_payload_results()
        self.assertEqual(self.event(request)['reason'], 'transport_session_lost')
        self.assertEqual(sum(call.args == ('start',) for call in self.node._call_automation_service.call_args_list), 1)

    def test_live_fcu_telemetry_and_standalone_lab_do_not_retire_tcp(self):
        self.bridge._router_connected_at = 100.0
        for records in ({}, {'HEARTBEAT': {'received_monotonic': 100.0},
                             'SYS_STATUS': {'received_monotonic': 110.0}}):
            self.bridge._gps_cache.records = records
            self.bridge._fcu_seen = False
            with patch.object(self.module.time, 'monotonic', return_value=111.0):
                self.bridge._check_router_liveness()
            self.assertFalse(self.port.closed)
            self.assertIsNotNone(self.bridge._conn)
        self.node._call_automation_service.assert_not_called()

    def test_long_idle_receive_only_socket_cannot_keep_blocked_writes_alive(self):
        self.bridge._router_connected_at = 100.0
        self.bridge._transport.last_write_at = 100.0
        self.port.send = Mock(side_effect=BlockingIOError(errno.EWOULDBLOCK, 'full'))
        self.bridge._conn.mav.heartbeat_send = lambda *args: self.bridge._transport.write(b'heartbeat')
        now = 100.0 + 16 * 60 * 60
        self.bridge._gps_cache.records['HEARTBEAT'] = {'received_monotonic': now}
        with patch.object(self.module.time, 'monotonic', return_value=now), \
                patch('scripts.lib.mavlink_tcp_transport.select.select', return_value=([], [], [])):
            self.bridge._transport.begin_cycle()
            self.assertFalse(self.bridge._send_heartbeat())
            self.bridge._check_router_liveness()
        self.assertTrue(self.port.closed, 'fresh FCU input must not hide a stuck payload writer')
        self.assertIsNone(self.bridge._conn)
        wait_commands(self.node)
        self.node._call_automation_service.assert_not_called()

    def test_router_retirement_still_stops_remote_calibration_after_its_rpc_returns(self):
        calibration = {'attempt_id': 'remote-zero', 'active': True,
                       'mavlink_request': request_context(1, 1, 84, 31014, 255, 190)}
        self.node._automation_status_cb(SimpleNamespace(data=json.dumps({'calibration': calibration})))
        self.bridge._retire_router('TCP EOF during remote calibration')
        wait_commands(self.node)
        self.node._call_automation_service.assert_called_once_with('stop')

    def test_web_or_finished_calibration_does_not_turn_router_retirement_into_global_stop(self):
        for calibration in (
                {'attempt_id': 'web-zero', 'active': True},
                {'attempt_id': 'remote-zero', 'active': False,
                 'mavlink_request': request_context(1, 1, 84, 31014, 255, 190)}):
            with self.subTest(calibration=calibration):
                self.node._mavlink_tcp_session = 84
                self.node._automation_status_cb(SimpleNamespace(data=json.dumps({'calibration': calibration})))
                self.bridge._retire_router('TCP EOF without active remote calibration')
                wait_commands(self.node)
                self.node._call_automation_service.assert_not_called()

    def test_router_retirement_still_preempts_a_remote_start_rpc_without_sampling_context(self):
        entered, release = threading.Event(), threading.Event()
        def transaction(action, payload=None):
            self.assertEqual(action, 'spectrometer_start')
            entered.set()
            self.assertTrue(release.wait(2))
            return False, {}
        self.node._call_control_transaction = transaction
        self.node._call_automation_service.side_effect = lambda action: release.set() or True
        request = remote_command(self.node, 31018)
        try:
            self.bridge._forward_payload_command(request.data)
            self.assertTrue(entered.wait(1))
            self.assertIsNone(self.node.current_sampling_context)
            self.bridge._retire_router('TCP EOF during spectrometer startup')
            wait_commands(self.node)
            self.node._call_automation_service.assert_called_once_with('stop')
        finally:
            release.set()
            wait_commands(self.node)

    def test_live_fcu_and_successful_socket_writes_do_not_hide_a_lost_roundtrip(self):
        request = self.start()
        self.bridge._router_connected_at = 100.0
        self.bridge._fcu_seen = True
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            self.bridge._transport.begin_cycle()
            self.bridge._send_router_probe()
        self.assertEqual(self.frames[-1][0], 'timesync')
        for now in (105.0, 110.0):
            with patch.object(self.module.time, 'monotonic', return_value=now):
                self.bridge._transport.begin_cycle()
                self.bridge._send_router_probe()
        self.assertEqual(len({args[1] for kind, args in self.frames if kind == 'timesync'}), 1)
        self.bridge._gps_cache.records['HEARTBEAT'] = {'received_monotonic': 116.0}
        self.bridge._transport.last_write_at = 116.0
        with patch.object(self.module.time, 'monotonic', return_value=116.0):
            self.bridge._check_router_liveness()
        wait_commands(self.node)
        self.assertTrue(self.port.closed)
        self.assertIn('roundtrip', self.bridge._reconnect_reason)
        self.assertFalse(self.node.is_sampling)
        self.reconnect()
        self.bridge._recover_payload_results()
        self.assertEqual(self.event(request)['reason'], 'transport_session_lost')
        self.assertEqual(sum(call.args == ('start',) for call in self.node._call_automation_service.call_args_list), 1)

    def test_long_uptime_probe_accepts_only_current_selected_fcu_echo(self):
        self.bridge._fcu_seen = True
        now = 100.0 + 16 * 60 * 60
        self.bridge._router_connected_at = 100.0
        def receive(system, component, tc1, token):
            message = SimpleNamespace(get_type=lambda: 'TIMESYNC', get_srcSystem=lambda: system,
                                      get_srcComponent=lambda: component, tc1=tc1, ts1=token)
            self.bridge._conn.recv_match = FakeConnection([message]).recv_match
            self.bridge._receive_mavlink_messages()
        for _ in range(8):
            self.bridge._gps_cache.records['HEARTBEAT'] = {'received_monotonic': now}
            with patch.object(self.module.time, 'monotonic', return_value=now):
                self.bridge._transport.begin_cycle()
                self.bridge._send_router_probe()
                token = self.frames[-1][1][1]
                for system, component, tc1, response in (
                        (1, 240, 123, token), (2, 1, 123, token),
                        (1, 1, 0, token), (1, 1, 123, token - 1)):
                    receive(system, component, tc1, response)
                    self.assertIsNotNone(self.bridge._fcu_probe)
                receive(1, 1, 123, token)
                self.assertIsNone(self.bridge._fcu_probe)
                self.bridge._check_router_liveness()
                self.assertIsNotNone(self.bridge._conn)
            now += 5.0
        self.assertEqual(len(self.frames), 8)
        self.node._call_automation_service.assert_not_called()

    def test_bridge_pacing_survives_ros_wall_clock_correction(self):
        self.bridge._conn.recv_match = FakeConnection([]).recv_match
        rate = Mock()
        rate.sleep.side_effect = RuntimeError('ROS time moved backwards')
        with patch.object(self.module.rospy, 'Rate', return_value=rate), \
                patch.object(self.module.rospy, 'is_shutdown', side_effect=[False, False, True]), \
                patch.object(self.module.time, 'sleep'), \
                patch.object(self.bridge, '_send_heartbeat', return_value=True), \
                patch.object(self.bridge, '_send_payload') as send_payload, \
                patch.object(self.bridge, '_publish_gps_evidence'):
            self.bridge.run()
        self.assertEqual(send_payload.call_count, 2)

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

    def test_stop_preempts_long_event_under_persistent_backpressure(self):
        request = self.start()
        sender = self.bridge._conn.mav.statustext_send
        def send(*args):
            sender(*args)
            if self.bridge._active_event_send['event']['command'] == 31010:
                self.bridge._transport.deadline = 0
        self.bridge._conn.mav.statustext_send = send
        # The old event can send one complete frame per assembly window, but
        # the shorter STOP can finish whenever it gets a scheduling turn.
        for _ in range(3):
            self.bridge._transport.begin_cycle()
            self.bridge._send_payload_event()
            self.bridge._active_event_send['started_at'] -= 1.1
        old_id = self.bridge._active_event_send['id']
        stop = remote_command(self.node, 31011)
        self.bridge._forward_payload_command(stop.data)
        wait_commands(self.node)
        self.frames.clear()
        for _ in range(2):
            self.bridge._transport.begin_cycle()
            self.bridge._send_payload_event()
            if not self.bridge._recent_events[(255, 190, 1, 2)]['pending']:
                break
            self.bridge._active_event_send['started_at'] -= 1.1
        self.assertFalse(self.bridge._recent_events[(255, 190, 1, 2)]['pending'])
        chunks = [args for kind, args in self.frames if kind == 'text' and len(args) == 4]
        self.assertNotEqual(chunks[0][2], old_id)
        self.assertEqual([chunk[3] for chunk in chunks], list(range(len(chunks))))
        raw = b''.join(chunk[1] for chunk in chunks).split(b'\0')[0]
        self.assertEqual(json.loads(raw[6:]), self.event(stop))
        self.assertTrue(self.bridge._recent_events[(255, 190, 1, 1)]['pending'])

    def test_stop_preempts_unexpired_event_at_a_complete_frame_boundary(self):
        self.start()
        sender = self.bridge._conn.mav.statustext_send
        def send(*args):
            sender(*args)
            self.bridge._transport.deadline = 0
        self.bridge._conn.mav.statustext_send = send
        self.bridge._transport.begin_cycle()
        self.bridge._send_payload_event()
        old_id = self.bridge._active_event_send['id']
        stop = remote_command(self.node, 31011)
        self.bridge._forward_payload_command(stop.data)
        wait_commands(self.node)
        self.frames.clear()
        self.bridge._conn.mav.statustext_send = sender
        self.bridge._transport.begin_cycle()
        self.bridge._send_payload_event()
        chunks = [args for kind, args in self.frames if kind == 'text' and len(args) == 4]
        self.assertEqual(chunks[0][3], 0)
        raw = b''.join(chunk[1] for chunk in chunks).split(b'\0')[0]
        self.assertEqual(json.loads(raw[6:]), self.event(stop))
        self.assertNotEqual(chunks[0][2], old_id)

    def test_transport_loss_before_request_binding_preserves_terminal_and_recovery(self):
        self._check_transport_loss_before_request_binding(0)

    def test_fcu_transport_loss_before_request_binding_preserves_terminal_and_recovery(self):
        self._check_transport_loss_before_request_binding(42)

    def _check_transport_loss_before_request_binding(self, sample_id):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def bind(context):
            entered.set()
            self.assertTrue(release.wait(2))
        self.node._bind_sample_position = bind
        self.node._cleanup_sampling_attempt = Mock(return_value=(True, False))
        self.node.sampling_result_pub.publish = lambda msg: stopped.set()
        request = remote_command(self.node, 31010, param2=sample_id)
        try:
            self.bridge._forward_payload_command(request.data)
            self.assertTrue(entered.wait(1))
            attempt = self.node.current_sampling_context['attempt_id']
            self.bridge._retire_router('TCP EOF before automation preparation')
            self.assertTrue(stopped.wait(1))
            release.set()
            wait_commands(self.node)
            terminal = dict(self.node._ledger().records['255:190:1:1']['event'])
            self.assertEqual((terminal['phase'], terminal['outcome'], terminal['reason']),
                             ('terminal', 'cancelled', 'transport_session_lost'))
            self.assertEqual(terminal['attempt_id'], attempt)
            self.reconnect()
            self.bridge._recover_payload_results()
            recovered = self.event(request)
            self.assertEqual((recovered['seq'], recovered['reason'], recovered['attempt_id']),
                             (terminal['seq'], 'transport_session_lost', attempt))
            request.data[9] = 85
            self.bridge._forward_payload_command(request.data)
            wait_commands(self.node)
            self.node._call_automation_service.assert_called_once_with('stop')
            self.assertFalse(self.node.is_sampling)
        finally:
            release.set()
            wait_commands(self.node)

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
