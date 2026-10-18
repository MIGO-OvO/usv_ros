"""Real dispatch lanes: blocked persistence cannot delay physical STOP."""
import threading
import unittest

from command_fixtures import remote_command, wait_commands
from test_mavlink_command_compat import _load_script


class DiskStopTests(unittest.TestCase):
    def setUp(self):
        module = _load_script('trigger_disk_stop', 'scripts/mavlink_trigger_node.py')
        self.node = module.MAVLinkTriggerNode()

    def test_fsync_stall_stop_first_no_late_start_or_old_ack_session(self):
        node = self.node
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        ledger = node._ledger()
        persist = ledger.persist
        actions = []

        def blocked(context, *args):
            if context['command'] == 31010:
                entered.set()
                self.assertTrue(release.wait(3))
            return persist(context, *args)

        def dispatch(command, *args):
            actions.append(command)
            if command == 31011:
                stopped.set()
            return True

        ledger.persist = blocked
        node.handle_mavlink_command = dispatch
        try:
            node._mavlink_cmd_rx_cb(remote_command(node, 31010))
            self.assertTrue(entered.wait(1))
            node._mavlink_cmd_rx_cb(remote_command(node, 31011))
            self.assertTrue(stopped.wait(1), 'STOP waited for persistence')
            node._mavlink_tcp_session = 2
        finally:
            release.set()
            wait_commands(node)
        self.assertEqual(actions, [31011])
        self.assertTrue(all(msg.data[4] == 1 for msg in node.cmd_ack_pub.messages))

    def test_disk_failure_locks_actions_but_allows_stop(self):
        node = self.node
        node._protocol_failed = True
        actions = []
        node.handle_mavlink_command = lambda cmd, *args: actions.append(cmd) or True
        node._mavlink_cmd_rx_cb(remote_command(node, 31010))
        node._mavlink_cmd_rx_cb(remote_command(node, 31011))
        wait_commands(node)
        self.assertEqual(actions, [31011])


if __name__ == '__main__':
    unittest.main()
