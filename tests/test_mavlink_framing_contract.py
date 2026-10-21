"""Inbound framing contract: only fully validated frames may dispatch commands.

The field stream contained a complete COMMAND_LONG inside the payload of an
outer frame. That command was never executed, and it must stay that way: a
payload that merely looks like a frame is not a command. These tests pin the
real pymavlink parser the bridge uses, plus the diagnostics classification.
"""
import struct
import unittest
from unittest.mock import patch

from test_mavlink_command_compat import _load_script as load_bridge

# Imported at module scope: other test modules install fake ROS modules into
# sys.modules while they run, so a late import could see a polluted tree.
try:
    from pymavlink.dialects.v20 import ardupilotmega as _ARDUPILOTMEGA
    _PYMAVLINK_ERROR = None
except ImportError as _exc:  # pragma: no cover - offline environments
    _ARDUPILOTMEGA = None
    _PYMAVLINK_ERROR = _exc


def build_unknown_outer_frame(payload):
    """A syntactically valid v2 frame with an id the dialect does not know."""
    body = bytes([len(payload), 0, 0, 2, 1, 190]) + bytes([250, 0, 0]) + payload
    header = b'\xfd' + body
    crc = 0xFFFF
    for byte in header[1:]:
        tmp = byte & 0xFF ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return header + struct.pack('<H', crc)


class FramingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _ARDUPILOTMEGA is None:  # pragma: no cover - offline environments
            raise unittest.SkipTest('pymavlink required for framing contracts: %s' % _PYMAVLINK_ERROR)
        cls.dialect = _ARDUPILOTMEGA

    def parser(self):
        class Sink:
            def write(self, data):
                return len(data)

            def recv(self, size=None):
                return b''

        mav = self.dialect.MAVLink(Sink(), srcSystem=255, srcComponent=190)
        mav.robust_parsing = True
        return mav

    def command_frame(self, command=31019):
        mav = self.parser()
        message = self.dialect.MAVLink_command_long_message(
            0, 0, command, 0, 0, 0, 0, 0, 0, 0, 0)
        return message.pack(mav)

    def test_half_frames_and_sticky_packets_use_frame_not_tcp_boundaries(self):
        frame = self.command_frame()
        mav = self.parser()
        self.assertEqual(mav.parse_buffer(frame[:7]), None)
        parsed = mav.parse_buffer(frame[7:] + frame)
        self.assertEqual([m.get_type() for m in parsed], ['COMMAND_LONG', 'COMMAND_LONG'])

    def test_corrupt_frame_is_rejected_and_never_dispatched(self):
        frame = bytearray(self.command_frame())
        frame[12] ^= 0xFF
        parsed = self.parser().parse_buffer(bytes(frame))
        self.assertEqual([m.get_type() for m in parsed], ['BAD_DATA'])
        self.assertIn('crc', parsed[0].reason.lower())

    def test_valid_outer_frame_does_not_dispatch_its_payload_as_a_command(self):
        inner = self.command_frame()
        outer = build_unknown_outer_frame(inner)
        parsed = self.parser().parse_buffer(outer)
        types = [m.get_type() for m in parsed]
        self.assertEqual(len(types), 1)
        self.assertNotIn('COMMAND_LONG', types, 'payload bytes must never become a control command')

    def test_bad_data_buckets_distinguish_crc_from_unknown_ids(self):
        module = load_bridge('framing_bridge', 'scripts/usv_mavlink_router_bridge.py')
        bridge = module.USVMavlinkRouterBridge.__new__(module.USVMavlinkRouterBridge)
        import threading
        bridge._lock = threading.Lock()
        bridge._bad_data_counts = {}
        bridge._bad_data_last_reason = ''
        bridge._count_bad_data('invalid MAVLink CRC in msgID 76 0x9e00 should be 0x74ea')
        bridge._count_bad_data('unknown MAVLink message ID 250')
        bridge._count_bad_data('invalid MAVLink signature')
        self.assertEqual(bridge._bad_data_counts['bad_crc'], 1)
        self.assertEqual(bridge._bad_data_counts['unknown_message_id'], 1)
        self.assertEqual(bridge._bad_data_counts['bad_signature'], 1)
        self.assertEqual(bridge._bad_data_counts['total'], 3)

    def test_unknown_message_and_bad_data_do_not_retire_the_session(self):
        module = load_bridge('framing_bridge_loop', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        bad = self.dialect.MAVLink_bad_data(b'\xfd\x00', 'unknown MAVLink message ID 250')
        replies = iter([bad, None])

        conn = type('Conn', (), {})()
        conn.recv_match = lambda blocking=False: next(replies, None)
        conn.mav = type('Mav', (), {})()
        bridge._conn = conn
        bridge._retire_router = lambda reason: self.fail('bad data retired the session: %s' % reason)
        published = []
        bridge._cmd_rx_pub = type('Pub', (), {'publish': lambda self, msg: published.append(msg)})()
        bridge._receive_mavlink_messages()
        # The rejected frame is counted, not fatal, and not forwarded.
        self.assertEqual(bridge._bad_data_counts.get('unknown_message_id'), 1)
        self.assertEqual(published, [])


if __name__ == '__main__':
    unittest.main()
