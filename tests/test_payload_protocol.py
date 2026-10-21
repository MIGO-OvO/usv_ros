import json
import tempfile
import unittest
from pathlib import Path

from scripts.lib.payload_protocol import RequestLedger, encode_event, request_context


class PayloadProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'requests.jsonl')
        self.ledger = RequestLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.context = request_context(1, 12, 3, 31010, 255, 190)

    def test_repeated_identity_replays_the_stored_event_without_reexecuting(self):
        self.assertTrue(self.ledger.admit(self.context, 0, 0)[0])
        first = self.ledger.event(self.context, 'request', 'accepted')
        admitted, reply = self.ledger.admit(self.context, 0, 0)
        self.assertFalse(admitted)
        self.assertEqual(reply, first)
        # The replay is the same answer, not a new progress tick.
        self.assertEqual(self.ledger.event(self.context, 'request', 'accepted'), first)

    def test_reservation_without_a_result_reports_in_flight_instead_of_a_fake_result(self):
        admitted, previous = self.ledger.reserve(self.context, 0, 0)
        self.assertTrue(admitted)
        self.assertIsNone(previous)
        self.assertFalse(self.ledger.reserve(self.context, 0, 0)[0])
        self.assertIsNone(self.ledger.reserve(self.context, 0, 0)[1])

    def test_old_stop_cannot_stop_new_work_even_after_eviction(self):
        stop = dict(self.context, command=31011)
        self.ledger.admit(stop, 0, 0)
        self.ledger.event(stop, 'request', 'accepted')
        for request in range(2, 400):
            self.ledger.admit(dict(self.context, request=request), 0, 0)
            self.ledger.event(dict(self.context, request=request), 'request', 'failed')
        with self.assertRaisesRegex(ValueError, 'Retired'):
            self.ledger.admit(stop, 0, 0)
        self.assertLessEqual(len(self.ledger.records), 256)

    def test_running_task_is_never_evicted(self):
        task = dict(self.context, command=31015)
        self.ledger.admit(task, 0, 0)
        self.ledger.event(task, 'request', 'accepted')
        self.ledger.event(task, 'progress', state='running', attempt_id='a' * 32)
        for request in range(2, 400):
            other = dict(self.context, request=request)
            self.ledger.admit(other, 0, 0)
            self.ledger.event(other, 'request', 'failed')
        admitted, previous = self.ledger.admit(task, 0, 0)
        self.assertFalse(admitted)
        self.assertEqual(previous['phase'], 'progress')

    def test_conflicting_identity_and_nonfinite_parameters_rejected(self):
        self.ledger.admit(self.context, 0, 0)
        with self.assertRaises(ValueError):
            self.ledger.admit(self.context, 1, 0)
        # The original record still answers its own retry.
        self.assertFalse(self.ledger.admit(self.context, 0, 0)[0])
        with self.assertRaises(ValueError):
            self.ledger.admit(dict(self.context, request=2), float('nan'), 0)
        for value in (0, -1, 1.5, float('inf'), 2**24):
            with self.assertRaises(ValueError):
                request_context(value, 12, 3, 31010, 255, 190)

    def test_full_attempt_terminal_preserved_and_ascii_bounded(self):
        self.ledger.admit(self.context, 0, 0)
        attempt = '1234567890abcdef1234567890abcdef'
        terminal = self.ledger.event(self.context, 'terminal', 'failed', '测量过期', attempt)
        late = self.ledger.event(self.context, 'request', 'accepted')
        self.assertEqual(terminal, late)
        encoded = encode_event(terminal)
        self.assertTrue(encoded.isascii())
        self.assertEqual(json.loads(encoded[6:])['attempt_id'], attempt)
        long_event = json.loads(encode_event(dict(terminal, reason='测量过期' * 80))[6:])
        self.assertEqual(long_event['attempt_id'], attempt)
        self.assertEqual(long_event['outcome'], 'failed')
        self.assertTrue(long_event['reason'].endswith('[truncated]'))
        self.assertLessEqual(len(encode_event(long_event)), 750)

    def test_watermark_never_regresses_and_admission_never_blocks(self):
        stop = dict(self.context, command=31011, request=5)
        self.assertTrue(self.ledger.reserve(stop, 0, 0)[0])
        self.assertFalse(self.ledger.reserve(stop, 0, 0)[0])
        self.assertEqual(self.ledger.watermarks['255:190'], (12, 5))
        # An older identity for the same sender is retired for this session.
        with self.assertRaisesRegex(ValueError, 'Retired'):
            self.ledger.reserve(dict(self.context, request=3), 0, 0)

    def test_unchanged_progress_keeps_its_sequence(self):
        self.ledger.admit(self.context, 0, 0)
        first = self.ledger.event(self.context, 'progress', state='waiting')
        self.assertEqual(first, self.ledger.event(self.context, 'progress', state='waiting'))
        changed = self.ledger.event(self.context, 'progress', state='running')
        self.assertGreater(changed['seq'], first['seq'])

    def test_survey_only_advances_after_success_to_new_attempt(self):
        context = dict(self.context, command=31015)
        self.ledger.admit(context, 0, 0)
        first = self.ledger.event(context, 'terminal', 'succeeded', attempt_id='a' * 32)
        self.assertEqual(first, self.ledger.event(context, 'request', 'accepted'))
        next_event = self.ledger.event(context, 'progress', attempt_id='b' * 32)
        self.assertGreater(next_event['seq'], first['seq'])
        failed = self.ledger.event(context, 'terminal', 'failed', attempt_id='b' * 32)
        self.assertEqual(failed, self.ledger.event(context, 'progress', attempt_id='c' * 32))

    def test_journal_failure_never_touches_admission(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # A path whose parent is a regular file cannot be opened for append.
        blocker = Path(directory.name) / 'blocker'
        blocker.write_text('x', encoding='utf-8')
        ledger = RequestLedger(str(blocker / 'journal.jsonl'))
        self.addCleanup(ledger.close)
        self.assertTrue(ledger.admit(self.context, 0, 0)[0])
        event = ledger.event(self.context, 'request', 'accepted')
        self.assertEqual(event['outcome'], 'accepted')
        self.assertFalse(ledger.admit(self.context, 0, 0)[0])
        self.assertEqual(ledger.event(self.context, 'terminal', 'succeeded')['phase'], 'terminal')

    def test_restart_starts_from_a_clean_correlation(self):
        self.ledger.admit(self.context, 0, 0)
        self.ledger.event(self.context, 'request', 'accepted')
        self.ledger.close()
        restarted = RequestLedger(self.path)
        self.addCleanup(restarted.close)
        # No cross-process exactly-once promise: reconnects re-sync device state.
        self.assertTrue(restarted.admit(self.context, 0, 0)[0])


if __name__ == '__main__':
    unittest.main()
