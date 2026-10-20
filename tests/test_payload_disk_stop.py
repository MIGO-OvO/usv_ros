"""Request correlation and journal faults: neither may gate a command or STOP."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from command_fixtures import remote_command, wait_commands
from test_mavlink_command_compat import _load_script


def _results(node):
    return [int(msg.data[1]) for msg in node.cmd_ack_pub.messages]


def _events(node):
    return [json.loads(msg.data) for msg in node._event_pub.messages]


class BlockingDispatch(object):
    """Runs the first sampled command synchronously so the lane is really busy."""

    def __init__(self, node):
        self.actions = []
        self.release = threading.Event()
        self.entered = threading.Event()
        node.handle_mavlink_command = self.dispatch

    def dispatch(self, command, *args):
        self.actions.append(command)
        if command == 31010:
            self.entered.set()
            self.release.wait(3)
        return True


class JournalFaultTests(unittest.TestCase):
    def setUp(self):
        from scripts.lib import payload_protocol
        self.protocol = payload_protocol
        self.module = _load_script('trigger_journal_fault', 'scripts/mavlink_trigger_node.py')
        self.node = self.module.MAVLinkTriggerNode()

    def test_unusable_journal_keeps_ordinary_commands_and_their_acks(self):
        node = self.node
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        blocker = Path(directory.name) / 'blocker'
        blocker.write_text('x', encoding='utf-8')
        # A path whose parent is a regular file can never be opened.
        node._request_ledger = self.protocol.RequestLedger(str(blocker / 'journal' / 'trace.jsonl'))
        dispatch = BlockingDispatch(node)
        dispatch.release.set()
        node._mavlink_cmd_rx_cb(remote_command(node, 31010))
        wait_commands(node)
        self.assertEqual(dispatch.actions, [31010])
        self.assertEqual(_results(node)[-1], 0)
        # The channel stays usable: no latched maintenance lock.
        node._mavlink_cmd_rx_cb(remote_command(node, 31019))
        wait_commands(node)
        self.assertEqual(dispatch.actions, [31010, 31019])
        self.assertEqual(_results(node)[-1], 0)

    def test_stop_never_waits_for_the_journal_writer(self):
        node = self.node
        started, release = threading.Event(), threading.Event()
        protocol = self.protocol

        class SlowHandle(object):
            def write(self, data):
                started.set()
                release.wait(3)
                return len(data)

            def flush(self):
                pass

            def close(self):
                pass

        class SlowJournal(protocol._Journal):
            def _open(self):
                self._handle = SlowHandle()

        ledger = node._ledger()
        ledger.journal = SlowJournal('unused.jsonl')
        dispatch = BlockingDispatch(node)
        dispatch.release.set()
        try:
            node._mavlink_cmd_rx_cb(remote_command(node, 31010))
            self.assertTrue(started.wait(2), 'journal writer was not exercised')
            node._mavlink_cmd_rx_cb(remote_command(node, 31011))
            self.assertTrue(dispatch.entered.wait(2), 'STOP waited for the journal')
        finally:
            release.set()
            wait_commands(node)
            ledger.close()
        self.assertEqual(dispatch.actions, [31010, 31011])
        self.assertTrue(all(result == 0 for result in _results(node)))

    def test_busy_rejection_is_a_result_that_a_retry_replays(self):
        node = self.node
        dispatch = BlockingDispatch(node)
        first = remote_command(node, 31010)
        second = remote_command(node, 31010)
        try:
            node._mavlink_cmd_rx_cb(first)
            self.assertTrue(dispatch.entered.wait(2))
            # A different identity arrives while the lane is occupied.
            node._mavlink_cmd_rx_cb(second)
            rejected = [event for event in _events(node) if event['outcome'] == 'rejected']
            self.assertTrue(rejected)
            self.assertEqual(rejected[-1]['reason'], 'command_lane_busy')
            self.assertEqual(_results(node)[-1], 1)
            # The same identity retried replays the rejection and its ACK.
            node._mavlink_cmd_rx_cb(second)
            self.assertEqual(_results(node)[-1], 1)
            self.assertEqual(len([event for event in _events(node) if event['outcome'] == 'rejected']), 2)
            # And the running request answers its own duplicate as in progress.
            node._mavlink_cmd_rx_cb(first)
            self.assertEqual(_results(node)[-1], 5)
        finally:
            dispatch.release.set()
            wait_commands(node)
        self.assertEqual(dispatch.actions, [31010])

    def test_rejected_identity_still_replays_after_the_lane_is_free(self):
        node = self.node
        dispatch = BlockingDispatch(node)
        accepted_request = remote_command(node, 31010)
        rejected_request = remote_command(node, 31010)
        try:
            node._mavlink_cmd_rx_cb(accepted_request)
            self.assertTrue(dispatch.entered.wait(2))
            node._mavlink_cmd_rx_cb(rejected_request)
            self.assertEqual(_results(node)[-1], 1)
        finally:
            dispatch.release.set()
            wait_commands(node)
        self.assertEqual(dispatch.actions, [31010])
        self.assertEqual(_results(node)[-1], 0)  # the admitted request completed
        # The lane is idle now, yet the rejected identity must not execute.
        node._mavlink_cmd_rx_cb(rejected_request)
        self.assertEqual(_results(node)[-1], 1)
        self.assertEqual(dispatch.actions, [31010])

    def test_consecutive_different_stops_both_run_and_both_answer(self):
        """31016 followed immediately by 31019 must not be rejected as busy."""
        node = self.node
        order, gate = [], threading.Event()

        def dispatch(command, *args):
            order.append(command)
            if command == 31016:
                gate.wait(3)
            return True

        node.handle_mavlink_command = dispatch
        survey_stop = remote_command(node, 31016)
        spectro_stop = remote_command(node, 31019)
        node._mavlink_cmd_rx_cb(survey_stop)
        node._mavlink_cmd_rx_cb(spectro_stop)
        gate.set()
        wait_commands(node)
        self.assertEqual(order, [31016, 31019])
        self.assertEqual(_results(node), [0, 0])
        events = _events(node)
        self.assertEqual([event['command'] for event in events], [31016, 31019])
        self.assertEqual([event['outcome'] for event in events], ['accepted', 'accepted'])
        # Each request kept its own correlation instead of overwriting the other.
        self.assertNotEqual(events[0]['request'], events[1]['request'])


if __name__ == '__main__':
    unittest.main()
