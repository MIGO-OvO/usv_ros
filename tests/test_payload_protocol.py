import json
import tempfile
import unittest
from pathlib import Path

from scripts.lib.payload_protocol import RequestLedger, encode_event, request_context


class PayloadProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'requests.db')
        self.ledger = RequestLedger(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.context = request_context(1, 12, 3, 31010, 255, 190)

    def test_restart_duplicate_is_not_reexecuted(self):
        self.assertTrue(self.ledger.admit(self.context, 0, 0)[0])
        self.ledger.event(self.context, 'request', 'accepted')
        self.ledger.close()
        self.ledger = RequestLedger(self.path)
        admitted, reply = self.ledger.admit(self.context, 0, 0)
        self.assertFalse(admitted)
        self.assertEqual(reply['outcome'], 'accepted')

    def test_old_stop_cannot_stop_new_work_even_after_reply_eviction(self):
        stop = dict(self.context, command=31011)
        self.ledger.admit(stop, 0, 0)
        for request in range(2, 260):
            self.ledger.admit(dict(self.context, request=request), 0, 0)
        with self.assertRaisesRegex(ValueError, 'Retired'):
            self.ledger.admit(stop, 0, 0)
        self.assertLessEqual(self.ledger.db.execute('SELECT COUNT(*) FROM reply').fetchone()[0], 256)

    def test_conflicting_identity_and_nonfinite_parameters_rejected(self):
        self.ledger.admit(self.context, 0, 0)
        with self.assertRaises(ValueError):
            self.ledger.admit(self.context, 1, 0)
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

    def test_stop_reservation_never_waits_for_disk_and_watermark_never_regresses(self):
        ordinary = self.context
        stop = dict(ordinary, command=31011, request=2)
        self.ledger.reserve(ordinary, 0, 0)
        with self.ledger.lock:
            self.assertTrue(self.ledger.reserve(stop, 0, 0)[0])
            self.assertFalse(self.ledger.reserve(stop, 0, 0)[0])
        self.ledger.persist(stop, 0, 0)
        self.ledger.persist(ordinary, 0, 0)
        self.assertEqual(self.ledger.db.execute('SELECT request FROM watermark').fetchone()[0], 2)

    def test_unchanged_progress_does_not_write_or_increment_sequence(self):
        self.ledger.admit(self.context, 0, 0)
        first = self.ledger.event(self.context, 'progress', state='waiting')
        writes = self.ledger.db.total_changes
        self.assertEqual(first, self.ledger.event(self.context, 'progress', state='waiting'))
        self.assertEqual(writes, self.ledger.db.total_changes)

    def test_survey_only_advances_after_success_to_new_attempt(self):
        context = dict(self.context, command=31015)
        self.ledger.admit(context, 0, 0)
        first = self.ledger.event(context, 'terminal', 'succeeded', attempt_id='a' * 32)
        self.assertEqual(first, self.ledger.event(context, 'request', 'accepted'))
        next_event = self.ledger.event(context, 'progress', attempt_id='b' * 32)
        self.assertGreater(next_event['seq'], first['seq'])
        failed = self.ledger.event(context, 'terminal', 'failed', attempt_id='b' * 32)
        self.assertEqual(failed, self.ledger.event(context, 'progress', attempt_id='c' * 32))


if __name__ == '__main__':
    unittest.main()
