"""Real pymavlink frames for the native QGC parser/controller integration.

Run separately in a real pymavlink environment. The optional fixture contains
only progress after a deliberately lost accepted delivery, never invented ACKs.
"""
import errno
import json
import os
from collections import defaultdict
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.lib.mavlink_tcp_transport import MAVLinkTCPTransport
from scripts.lib.payload_protocol import RequestLedger, request_context
from test_mavlink_command_compat import _load_script


class WirePort:
    def __init__(self):
        self.accepted = bytearray()
        self.responses = iter((3, BlockingIOError(errno.EAGAIN, 'backpressure'), 2))

    def send(self, data):
        result = next(self.responses, len(data))
        if isinstance(result, Exception):
            raise result
        self.accepted.extend(data[:result])
        return result


class BridgeQMLWireTests(unittest.TestCase):
    def test_lost_accepted_then_production_progress_frames(self):
        try:
            from pymavlink.dialects.v20 import ardupilotmega as dialect
        except ImportError:
            self.skipTest('real pymavlink required; run separately in WSL')
        module = _load_script('qml_wire_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        bridge._tcp_session = 1
        wire, counts = WirePort(), defaultdict(int)
        transport = MAVLinkTCPTransport(wire, counts)
        system_id = int(os.environ.get('USV_QA_SYSTEM_ID', '1'))
        self.assertTrue(1 <= system_id <= 255)
        bridge._transport = transport
        bridge._conn = SimpleNamespace(mav=dialect.MAVLink(transport, srcSystem=system_id, srcComponent=191))
        ledger = RequestLedger(':memory:')
        self.addCleanup(ledger.close)
        context = request_context(1, 1, 1, 31010, 255, 190)
        ledger.admit(context, 0, 0)
        accepted = ledger.event(context, 'request', 'accepted')
        progress = ledger.event(context, 'progress', '', attempt_id='1' * 32,
                                preflight_phase='homing', state='running')
        for event in (accepted, progress):
            bridge._payload_event_cb(SimpleNamespace(data=json.dumps(event)))
        with patch('scripts.lib.mavlink_tcp_transport.select.select', return_value=([], [wire], [])):
            transport.deadline = 0  # Healthy port, exhausted before the first byte.
            bridge._send_payload_event()
            self.assertFalse(wire.accepted)
            self.assertIsNotNone(bridge._conn)
            transport.begin_cycle()
            bridge._send_payload_event()
            # Model link packet loss of the earlier accepted event, rather than
            # weakening the production parser's legal progress contract.
            wire.accepted.clear()
            original_write = transport.write
            held = []
            def write(frame):
                size = original_write(frame)
                if not held:
                    held.append(True)
                    transport.deadline = 0  # Defer the next complete MAVLink frame.
                return size
            transport.write = write
            transport.begin_cycle()
            bridge._send_payload_event()
            self.assertIsNotNone(bridge._active_event_send)
            transport.begin_cycle()
            bridge._send_payload_event()
        frames = dialect.MAVLink(None).parse_buffer(bytes(wire.accepted))
        self.assertTrue(frames)
        self.assertTrue(all(frame.get_type() == 'STATUSTEXT' for frame in frames))
        self.assertTrue(all((frame.get_srcSystem(), frame.get_srcComponent()) == (system_id, 191) for frame in frames))
        self.assertEqual([frame.chunk_seq for frame in frames], list(range(len(frames))))
        raw = b''.join(frame.text.encode('ascii') for frame in frames)
        self.assertEqual(json.loads(raw.split(b'\0')[0][6:]), progress)
        self.assertEqual(progress['outcome'], '')
        self.assertEqual(progress['state'], 'running')
        self.assertGreater(counts['short_writes'], 0)
        self.assertGreater(counts['would_block'], 0)
        self.assertEqual(counts['deferred_frames'], 2)
        self.assertEqual(counts['fully_written_frames'], bridge._diag_tx_total)
        fixture = os.environ.get('USV_QA_EVENT_FRAMES')
        if fixture:
            with open(fixture, 'w', encoding='utf-8') as output:
                json.dump({'frames': [bytes(frame.get_msgbuf()).hex() for frame in frames]}, output)


if __name__ == '__main__':
    unittest.main()
