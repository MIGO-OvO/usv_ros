"""Isolated TCP integration with the installed pymavlink, never a vehicle port."""
import socket
import tempfile
import time
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from scripts.lib.mavlink_tcp_transport import connect_router, MAVLinkTCPTransport
from scripts.lib.payload_protocol import next_transport_session


class RealMAVLinkTCPTests(unittest.TestCase):
    def test_durable_transport_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'sessions.db')
            self.assertEqual(next_transport_session(path), 1)
            self.assertEqual(next_transport_session(path), 2)

    def test_v2_frame_chunks_eof_and_fresh_parser_after_reconnect(self):
        try:
            from pymavlink import mavutil
            from pymavlink.dialects.v20 import ardupilotmega as dialect
        except ImportError:
            self.skipTest('Real pymavlink required; run this file separately in the WSL toolchain')
        if not hasattr(mavutil, 'mavtcp'):
            self.skipTest('ROS test stubs loaded; real pymavlink integration runs separately in WSL')
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        listener.settimeout(1)
        url = 'tcp:127.0.0.1:{}'.format(listener.getsockname()[1])
        connection = connect_router(mavutil, url, 1, 191)
        self.addCleanup(connection.close)
        peer, _ = listener.accept()
        self.addCleanup(peer.close)
        peer.settimeout(1)
        transport = MAVLinkTCPTransport(connection.port, defaultdict(int))
        connection.write = transport.write
        connection.recv = lambda n=None: transport.recv(n or connection.mav.bytes_needed())
        with patch('time.time', return_value=-1000000):
            transport.begin_cycle()
            connection.mav.statustext_send(5, b'USVE1:full request', 41, 2)
        raw = peer.recv(512)
        parser = dialect.MAVLink(None)
        messages = parser.parse_buffer(raw)
        self.assertEqual(len(messages), 1)
        self.assertEqual((messages[0].id, messages[0].chunk_seq), (41, 2))
        self.assertEqual(transport.counters['fully_written_frames'], 1)
        token = time.monotonic_ns()
        transport.begin_cycle()
        connection.mav.timesync_send(0, token)
        probe = parser.parse_buffer(peer.recv(512))[0]
        self.assertEqual((probe.tc1, probe.ts1), (0, token))
        fcu = dialect.MAVLink(None, srcSystem=1, srcComponent=1)
        peer.sendall(fcu.timesync_encode(123, token).pack(fcu))
        echo = connection.recv_match(blocking=True, timeout=1)
        self.assertEqual((echo.get_srcSystem(), echo.get_srcComponent(), echo.tc1, echo.ts1),
                         (1, 1, 123, token))
        # A broken incoming prefix belongs only to this parser/session.
        peer.sendall(raw[:8])
        connection.recv_match(blocking=False)
        peer.close()
        with self.assertRaises(OSError):
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                connection.recv_match(blocking=False)
        replacement = connect_router(mavutil, url, 1, 191)
        self.addCleanup(replacement.close)
        next_peer, _ = listener.accept()
        self.addCleanup(next_peer.close)
        next_peer.sendall(raw)
        message = replacement.recv_match(blocking=True, timeout=1)
        self.assertEqual(message.text, 'USVE1:full request')
        self.assertEqual(message.id, 41)


if __name__ == '__main__':
    unittest.main()
