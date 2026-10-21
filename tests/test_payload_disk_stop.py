"""Request correlation and journal faults: neither may gate a command or STOP."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
            wait_commands(node)
            self.assertFalse(release.is_set())
            self.assertEqual(dispatch.actions, [31010, 31011], 'STOP waited for the journal')
            self.assertEqual(_results(node), [0, 0])
        finally:
            release.set()
            wait_commands(node)
            ledger.close()
        self.assertEqual(dispatch.actions, [31010, 31011])
        self.assertTrue(all(result == 0 for result in _results(node)))

    def test_busy_rejection_is_a_result_that_a_retry_replays(self):
        node = self.node
        entered, release = threading.Event(), threading.Event()
        calls = []
        def service(**kwargs):
            calls.append(kwargs['action'])
            entered.set()
            self.assertTrue(release.wait(3))
            return SimpleNamespace(success=True, result_json='{}')
        first = remote_command(node, 31018)
        second = remote_command(node, 31018)
        with patch.object(self.module.rospy, 'ServiceProxy', return_value=service):
            try:
                node._mavlink_cmd_rx_cb(first)
                self.assertTrue(entered.wait(2))
                node._mavlink_cmd_rx_cb(second)
                rejected = _events(node)[-1]
                self.assertEqual(rejected['reason'], 'command_lane_busy')
                self.assertEqual(_results(node)[-1], 1)
                generation = node._command_generation
                # Rejection keeps its original sequence; an event-less running
                # request answers IN_PROGRESS without making a new event.
                node._mavlink_cmd_rx_cb(second)
                self.assertEqual(_results(node)[-1], 1)
                self.assertEqual(_events(node)[-1], rejected)
                event_count = len(_events(node))
                node._mavlink_cmd_rx_cb(first)
                self.assertEqual(_results(node)[-1], 5)
                self.assertEqual(len(_events(node)), event_count)
                self.assertEqual(node._command_generation, generation)
            finally:
                release.set()
                wait_commands(node)
        self.assertEqual(calls, ['spectrometer_start'])

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

    def test_replay_ack_tracks_the_cached_phase_without_repeating_work(self):
        for phase, outcome, expected in (
                ('request', 'accepted', 0), ('progress', '', 5),
                ('terminal', 'succeeded', 0), ('terminal', 'failed', 4),
                ('terminal', 'cancelled', 4)):
            with self.subTest(phase=phase, outcome=outcome):
                node = self.module.MAVLinkTriggerNode()
                calls = []
                def service(**kwargs):
                    calls.append(kwargs['action'])
                    return SimpleNamespace(success=True, result_json='{}')
                with patch.object(self.module.rospy, 'ServiceProxy', return_value=service):
                    message = remote_command(node, 31018)
                    node._mavlink_cmd_rx_cb(message)
                    wait_commands(node)
                    original = _events(node)[-1]
                    if phase != 'request':
                        node._emit_request_event(original, phase, outcome, attempt_id='a' * 32)
                    cached = _events(node)[-1]
                    generation = node._command_generation
                    node._mavlink_cmd_rx_cb(message)
                    self.assertEqual(_results(node)[-1], expected)
                    self.assertEqual(_events(node)[-1], cached)
                    self.assertEqual(calls, ['spectrometer_start'])
                    self.assertEqual(node._command_generation, generation)
                    self.assertEqual(node._command_lanes[False]['busy'], 0)


class SurveyCancellationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('trigger_survey_cancel', 'scripts/mavlink_trigger_node.py')
        self.node = self.module.MAVLinkTriggerNode()
        self.addCleanup(lambda: self.node._ledger().close())

    def stop_and_wait(self):
        stopped = threading.Event()
        publish = self.node._event_pub.publish
        def observe(message):
            publish(message)
            if json.loads(message.data)['command'] == 31016:
                stopped.set()
        self.node._event_pub.publish = observe
        self.node._mavlink_cmd_rx_cb(remote_command(self.node, 31016))
        self.assertTrue(stopped.wait(2), 'survey STOP blocked behind ordinary work')
        self.assertEqual(_events(self.node)[-1]['outcome'], 'accepted')

    def test_no_survey_stop_preserves_independent_detector_start(self):
        node = self.node
        entered, release = threading.Event(), threading.Event()
        calls = []
        def service(**kwargs):
            calls.append(kwargs['action'])
            entered.set()
            self.assertTrue(release.wait(3))
            return SimpleNamespace(success=True, result_json='{}')
        with patch.object(self.module.rospy, 'ServiceProxy', return_value=service):
            try:
                message = remote_command(node, 31018)
                node._mavlink_cmd_rx_cb(message)
                self.assertTrue(entered.wait(2))
                generation = node._command_generation
                node._mavlink_cmd_rx_cb(message)  # in-flight duplicate
                self.assertEqual(_results(node)[-1], 5)
                self.stop_and_wait()
                self.assertEqual(node._command_generation, generation)
            finally:
                release.set()
                wait_commands(node)
        self.assertEqual(calls, ['spectrometer_start'])
        self.assertEqual(_results(node)[-1], 0)
        self.assertEqual(_events(node)[-1]['outcome'], 'accepted')
        self.assertFalse(any(e.get('reason') == 'command_rejected_or_cancelled' for e in _events(node)))
        self.assertIsNone(node._command_lanes[False]['command'])

    def test_stop_cancels_survey_before_activation_and_scheduler_creation(self):
        node = self.node
        entered, release = threading.Event(), threading.Event()
        def config():
            entered.set()
            self.assertTrue(release.wait(3))
            return node._get_default_config()
        node._load_config = config
        try:
            node._mavlink_cmd_rx_cb(remote_command(node, 31015))
            self.assertTrue(entered.wait(2))
            self.assertFalse(node._survey_active)
            self.stop_and_wait()
        finally:
            release.set()
            wait_commands(node)
        self.assertFalse(node._survey_active)
        self.assertIsNone(node._survey_thread, 'cancelled START created a scheduler')
        self.assertFalse(node.is_sampling)
        self.assertEqual(node.pump_command_pub.messages, [])
        self.assertNotIn('survey_started', [m.data for m in node.status_pub.messages])
        self.assertEqual(_results(node)[-1], 4)
        self.assertEqual(_events(node)[-1]['reason'], 'superseded_by_stop')
        self.assertIsNone(node._command_lanes[False]['command'])

    def test_full_stop_queue_rejects_without_cancellation_or_lane_marker(self):
        node = self.node
        lane = node._command_lanes[True]
        # Occupy only the hardware/RPC boundary of the running STOP, then fill
        # its bounded queue using the original receive and handler paths.
        entered, release = threading.Event(), threading.Event()
        def service(**kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return SimpleNamespace(success=True, result_json='{}')
        with patch.object(self.module.rospy, 'ServiceProxy', return_value=service):
            try:
                node._mavlink_cmd_rx_cb(remote_command(node, 31019))
                self.assertTrue(entered.wait(2))
                for _ in range(lane['queue'].maxsize):
                    node._mavlink_cmd_rx_cb(remote_command(node, 31019))
                generation = node._command_generation
                refused = remote_command(node, 31011)
                node._mavlink_cmd_rx_cb(refused)
                self.assertEqual(_results(node)[-1], 1)
                self.assertEqual(node._command_generation, generation)
                cached = _events(node)[-1]
                node._mavlink_cmd_rx_cb(refused)
                self.assertEqual(_events(node)[-1], cached)
                self.assertEqual(node._command_generation, generation)
                self.assertIsNone(node._command_lanes[False]['command'])
            finally:
                release.set()
                wait_commands(node)

    def test_admitted_survey_is_marked_even_before_worker_enters_handler(self):
        node = self.node
        with node._sampling_control_lock:
            node._mavlink_cmd_rx_cb(remote_command(node, 31015))
            self.assertEqual(node._command_lanes[False]['command'], 31015)
            self.stop_and_wait()
        wait_commands(node)
        self.assertFalse(node._survey_active)
        self.assertIsNone(node._survey_thread)
        self.assertIsNone(node._command_lanes[False]['command'])

    def test_active_survey_stops_its_scheduler_and_owned_sample(self):
        node = self.node
        started, stopped = threading.Event(), threading.Event()
        self.module.rospy.is_shutdown = lambda: False
        self.module.rospy.sleep = lambda seconds: stopped.wait(3)
        node._load_config = node._get_default_config
        publish = node.status_pub.publish
        def status(message):
            publish(message)
            if message.data == 'sampling_started':
                started.set()
            if message.data == 'survey_stopped':
                stopped.set()
        node.status_pub.publish = status
        calls = []
        def service(**kwargs):
            payload = json.loads(kwargs['payload_json'])
            calls.append((kwargs['action'], payload.get('attempt_id')))
            return SimpleNamespace(success=True, result_json=json.dumps(
                {'attempt_id': payload.get('attempt_id'), 'cleanup': 'stopped'}))
        with patch.object(self.module.rospy, 'ServiceProxy', return_value=service):
            try:
                node._mavlink_cmd_rx_cb(remote_command(node, 31015))
                self.assertTrue(started.wait(2))
                wait_commands(node)
                self.assertTrue(node._survey_active)
                attempt = node.current_sampling_context['attempt_id']
                self.assertTrue(node.is_sampling and node._survey_sample_active)
                self.stop_and_wait()
                self.assertFalse(node._survey_active or node.is_sampling or node._survey_sample_active)
                self.assertIsNone(node.current_sampling_context)
                self.assertEqual(calls, [('automation_start', attempt), ('automation_cleanup', attempt)])
            finally:
                stopped.set()
                if node._survey_thread:
                    node._survey_thread.join(3)
                wait_commands(node)
        self.assertFalse(node._survey_thread.is_alive())
        self.assertEqual(node.pump_command_pub.messages, [])
        states = [m.data for m in node.status_pub.messages]
        self.assertEqual(states.count('survey_started'), 1)
        self.assertEqual(states.count('sampling_started'), 1)

    def test_handler_exception_releases_ordinary_marker(self):
        node = self.node
        with patch.object(self.module.rospy, 'ServiceProxy', side_effect=RuntimeError('RPC failed')):
            node._mavlink_cmd_rx_cb(remote_command(node, 31018))
            wait_commands(node)
        self.assertEqual(_results(node)[-1], 4)
        self.assertIsNone(node._command_lanes[False]['command'])
        self.assertEqual(node._command_lanes[False]['busy'], 0)


if __name__ == '__main__':
    unittest.main()
