"""Actual byte-boundary regressions; only local socketpair endpoints are used."""
import errno
import socket
import time
import unittest
from collections import defaultdict
from unittest.mock import patch

from scripts.lib.mavlink_tcp_transport import MAVLinkTCPTransport
from test_mavlink_command_compat import _load_script, FakeConnection


class Port:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.accepted = bytearray()
        self.closed = False

    def send(self, data):
        result = next(self.responses, len(data))
        if isinstance(result, Exception):
            raise result
        self.accepted.extend(data[:result])
        return result

    def close(self):
        self.closed = True


class TCPTransportTests(unittest.TestCase):
    def test_short_writes_and_eagain_keep_exact_tail(self):
        port = Port([3, BlockingIOError(errno.EAGAIN, 'full'), 2])
        counts = defaultdict(int)
        writer = MAVLinkTCPTransport(port, counts)
        with patch('scripts.lib.mavlink_tcp_transport.select.select', return_value=([], [port], [])):
            self.assertEqual(writer.write(b'abcdefghij'), 10)
        self.assertEqual(port.accepted, b'abcdefghij')
        self.assertEqual(counts['fully_written_frames'], 1)
        self.assertEqual(counts['short_writes'], 2)
        self.assertEqual(counts['would_block'], 1)

    def test_zero_write_or_disconnect_after_prefix_cannot_count_success(self):
        for failure in (0, OSError(errno.EBADF, 'closed')):
            port = Port([2, failure])
            counts = defaultdict(int)
            writer = MAVLinkTCPTransport(port, counts)
            with self.assertRaises(OSError):
                writer.write(b'abcdef')
            self.assertEqual(port.accepted, b'ab')
            self.assertTrue(port.closed)
            self.assertEqual(counts['fully_written_frames'], 0)

    def test_real_backpressure_is_bounded_and_retires_session(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        sender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sender.close)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        sender.connect(listener.getsockname())
        receiver, _ = listener.accept()
        self.addCleanup(receiver.close)
        sender.setblocking(False)
        # Windows may accept a large first send despite a small SO_SNDBUF.
        # Fill the real socket until it reports EAGAIN; do not assume its size.
        for _ in range(1024):
            try:
                sender.send(b'x' * 65536)
            except BlockingIOError:
                break
        else:
            self.fail('local socket never reached backpressure')
        counts = defaultdict(int)
        writer = MAVLinkTCPTransport(sender, counts)
        started = time.monotonic()
        with self.assertRaises(OSError):
            writer.write(b'y' * 65536)
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(sender.fileno(), -1)
        self.assertEqual(counts['fully_written_frames'], 0)
        self.assertGreater(counts['would_block'], 0)

    def test_eof_and_ebadf_are_errors_not_idle(self):
        for closed in (False, True):
            sender, receiver = socket.socketpair()
            self.addCleanup(sender.close)
            self.addCleanup(receiver.close)
            receiver.close()
            if closed:
                sender.close()
            with self.assertRaises(OSError):
                MAVLinkTCPTransport(sender, defaultdict(int)).recv(1)

    def test_receive_flood_has_message_budget_including_bad_data(self):
        module = _load_script('bridge_rx_budget', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        from types import SimpleNamespace
        calls = []
        def receive(**kwargs):
            calls.append(1)
            return SimpleNamespace(get_type=lambda: 'BAD_DATA')
        bridge._conn = SimpleNamespace(recv_match=receive)
        bridge._receive_mavlink_messages()
        self.assertLessEqual(len(calls), 64)
        self.assertGreater(len(calls), 0)


if __name__ == '__main__':
    unittest.main()
