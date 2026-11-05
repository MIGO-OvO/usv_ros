"""Radio frames share the bridge socket owner and remain strictly addressed."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from scripts.lib import ros_radio as radio
from test_mavlink_command_compat import _load_script, FakeConnection


class RadioBridgeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('ros_radio_bridge_test', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(self.module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = self.module.USVMavlinkRouterBridge()

    def test_target_filter_and_main_loop_reply(self):
        def frame(system, component, message_type=radio.MESSAGE_TYPE):
            return SimpleNamespace(get_type=lambda: 'V2_EXTENSION', message_type=message_type,
                                   target_system=system, target_component=component,
                                   get_srcSystem=lambda: 254, get_srcComponent=lambda: 190,
                                   payload=radio.pack(radio.HELLO, 12, 0, 0))
        self.bridge._conn = FakeConnection([frame(2, 191), frame(1, 0), frame(1, 191, 1)])
        self.bridge._receive_mavlink_messages()
        self.assertIsNone(self.bridge._ros_radio)
        self.bridge._conn = FakeConnection([frame(self.bridge._sys_id, self.bridge._comp_id)])
        self.bridge._receive_mavlink_messages()
        server = self.bridge._ros_radio
        self.addCleanup(server.close)
        self.assertIsNotNone(server.peek())
        sent = []
        self.bridge._conn.mav = SimpleNamespace(v2_extension_send=lambda *a: sent.append(a))
        self.bridge._mav_send_with_retry = lambda callback, label: callback() is None
        self.assertEqual(sent, [])
        self.bridge._send_ros_radio()
        self.assertEqual(sent[0][:4], (0, 254, 190, radio.MESSAGE_TYPE))
        self.assertEqual(radio.unpack(sent[0][4])[0], radio.READY)
        self.assertIsNone(server.peek())


if __name__ == '__main__':
    unittest.main()
