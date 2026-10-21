"""Versioned payload requests: volatile correlation plus an optional journal.

Admission is a dictionary lookup under one lock. Nothing here decides whether a
command may run, so a closed, unwritable or failing journal cannot block a
command, a STOP or the release of the command lane. The journal only records
requests and results that already exist; it never mints an identity or a seq.
"""
import json
import logging
import math
import os
import queue
import threading
from collections import OrderedDict

MAX_ID = (1 << 24) - 1
MAX_EVENT_BYTES = 750
SAFETY_COMMANDS = (31011, 31016, 31019)
# Requests whose accepted answer starts work instead of finishing it.
TASK_COMMANDS = (31010, 31014, 31015)
MAX_REPLIES = 256
MAX_SENDERS = 64
JOURNAL_QUEUE = 512

_LOG = logging.getLogger(__name__)


def next_transport_session(path):
    """Durable transport session id so a restarted bridge never reuses one.

    Best effort by design: a missing counter must not stop the bridge from
    carrying commands. Falling back to a time-derived id keeps the session
    ordered and distinct for the life of this process.
    """
    try:
        import sqlite3
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        db = sqlite3.connect(path, timeout=0.05)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS transport '
                           '(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER)')
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT value FROM transport WHERE id=1').fetchone()
                value = row[0] + 1 if row else 1
                if value > MAX_ID:
                    raise ValueError('Transport session identity exhausted')
                db.execute('INSERT OR REPLACE INTO transport VALUES (1,?)', (value,))
            return value
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - diagnostics only, never a startup gate
        _LOG.warning('Transport session counter unavailable (%s); using a volatile id', exc)
        import time
        return (int(time.time()) % MAX_ID) + 1


def exact_id(value):
    return (not isinstance(value, bool) and isinstance(value, (float, int))
            and math.isfinite(value) and int(value) == value and 1 <= value <= MAX_ID)


def request_context(request, session, tcp_session, command, sender_system, sender_component):
    if not all(exact_id(value) for value in (request, session, tcp_session)):
        raise ValueError('Invalid payload request identity')
    if not 31010 <= command <= 31019 or not 1 <= sender_system <= 255 or not 0 <= sender_component <= 255:
        raise ValueError('Invalid payload request source/command')
    return dict(v=1, request=int(request), session=int(session), tcp_session=int(tcp_session),
                command=command, sender_system=sender_system, sender_component=sender_component)


def bounded_event(event):
    if event.get('v') != 1 or not all(exact_id(event.get(key)) for key in ('session', 'request', 'seq')):
        raise ValueError('Invalid payload event identity')
    event = dict(event)
    for key, limit in (('reason', 80), ('preflight_phase', 40), ('state', 40)):
        if key in event and len(str(event[key])) > limit:
            event[key] = str(event[key])[:limit - 14] + '...[truncated]'
    # Only display text may be shortened. Never truncate identities or outcomes.
    for key in ('reason', 'preflight_phase', 'state'):
        if len('USVE1:' + json.dumps(event, ensure_ascii=True, separators=(',', ':'))) <= MAX_EVENT_BYTES:
            return event
        original = str(event.get(key, ''))
        for size in range(len(original) - 1, -1, -1):
            event[key] = original[:size] + '...[truncated]'
            if len('USVE1:' + json.dumps(event, ensure_ascii=True, separators=(',', ':'))) <= MAX_EVENT_BYTES:
                return event
    if len('USVE1:' + json.dumps(event, ensure_ascii=True, separators=(',', ':'))) > MAX_EVENT_BYTES:
        raise ValueError('Payload event too long')
    return event


def encode_event(event):
    return 'USVE1:' + json.dumps(bounded_event(event), ensure_ascii=True, separators=(',', ':'))


class _Journal(object):
    """Append-only diagnostic trace. Dropping a line is better than stalling."""

    def __init__(self, path):
        self.path = path
        self._queue = queue.Queue(maxsize=JOURNAL_QUEUE)
        self._thread = None
        self._lock = threading.Lock()
        self._handle = None
        self._failed = False
        self._closed = False

    def write(self, record):
        if self._closed:
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            return
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name='PayloadJournal')
                self._thread.start()

    def _loop(self):
        while True:
            if self._closed and self._queue.empty():
                self._close_handle()
                return
            try:
                record = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._handle is None and not self._failed:
                self._open()
            if self._handle is None:
                continue
            try:
                self._handle.write(json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n')
                self._handle.flush()
            except OSError as exc:
                # A journal that stops accepting writes is a diagnostics fault,
                # not a reason to latch the control plane.
                self._failed = True
                _LOG.warning('Payload journal unavailable; continuing without it: %s', exc)
                self._close_handle()

    def _open(self):
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._handle = open(self.path, 'a', encoding='utf-8')
        except OSError as exc:
            self._failed = True
            _LOG.warning('Payload journal unavailable; continuing without it: %s', exc)

    def _close_handle(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def close(self):
        self._closed = True
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._close_handle()


class RequestLedger(object):
    """Correlate one request identity with at most one recent result.

    Rules, all local to this process:
      * the same identity and parameters replay the stored event, never a
        second action;
      * the same identity with different parameters is refused and the
        original record is untouched;
      * a rejected record keeps replaying the rejection even after the lane is
        free, so a retry cannot silently become an execution;
      * in-flight and unfinished-routine records are never evicted, so a task
        that is still running always has its own correlation;
      * a restart forgets everything: reconnects re-sync state instead of
        promising exactly-once execution across processes.
    """

    def __init__(self, journal_path=None):
        self.lock = threading.RLock()
        self.records = OrderedDict()
        self.watermarks = {}
        self.journal = None
        if journal_path not in (None, '', ':memory:'):
            self.journal = _Journal(journal_path)

    @staticmethod
    def key(context):
        return '{}:{}:{}:{}'.format(context['sender_system'], context['sender_component'],
                                   context['session'], context['request'])

    @staticmethod
    def sender(context):
        return '{}:{}'.format(context['sender_system'], context['sender_component'])

    @staticmethod
    def signature(context, param1, param2):
        return json.dumps([context['command'], param1, param2])

    def reserve(self, context, param1, param2):
        """Volatile admission. Returns ``(admitted, previous_event)``."""
        if not all(math.isfinite(value) for value in (param1, param2)):
            raise ValueError('Non-finite command parameters')
        key = self.key(context)
        sender = self.sender(context)
        signature = self.signature(context, param1, param2)
        with self.lock:
            record = self.records.get(key)
            if record is not None:
                if record['signature'] is not None and record['signature'] != signature:
                    raise ValueError('Request identity reused with different parameters')
                # A record synthesised by an out-of-band event learns its
                # signature on first admission; it never changes afterwards.
                record['signature'] = signature
                event = record.get('event')
                return False, dict(event) if event else None
            watermark = self.watermarks.get(sender)
            if watermark and (context['session'], context['request']) <= tuple(watermark):
                raise ValueError('Retired payload request')
            if not watermark and len(self.watermarks) >= MAX_SENDERS:
                raise ValueError('Payload sender capacity reached')
            self.watermarks[sender] = (context['session'], context['request'])
            self.records[key] = {'signature': signature, 'event': None, 'closed': False,
                                 'command': context['command'], 'attempts': []}
            self._evict()
            return True, None

    def persist(self, context, param1, param2):
        """Compatibility shim: the journal needs no separate admission step.

        Admission already happened in :meth:`reserve`; the journal is written
        from :meth:`event` once a result exists.
        """
        return None

    def admit(self, context, param1, param2):
        return self.reserve(context, param1, param2)

    def _evict(self):
        if len(self.records) <= MAX_REPLIES:
            return
        for key in list(self.records):
            if len(self.records) <= MAX_REPLIES:
                break
            record = self.records[key]
            if record.get('event') is None or not record.get('closed'):
                continue  # in flight or still owns a running task
            del self.records[key]

    @staticmethod
    def _is_closed(phase, outcome, command):
        if phase == 'terminal':
            return True
        if phase != 'request':
            return False
        if outcome not in ('accepted', 'rejected', 'failed', 'cancelled'):
            return False
        # An accepted sampling/calibration/survey request starts work; only its
        # terminal closes it.
        return not (outcome == 'accepted' and command in TASK_COMMANDS)

    def event(self, context, phase, outcome='', reason='', attempt_id='', preflight_phase='', **details):
        """Build the next event for this identity, creating the record if needed."""
        key = self.key(context)
        with self.lock:
            record = self.records.get(key)
            if record is None:
                record = {'signature': None, 'event': None,
                          'command': context.get('command'), 'attempts': [], 'closed': False}
                self.records[key] = record
                self._evict()
            previous = dict(record.get('event') or {})
            # Once a terminal result exists, late accepted/progress cannot erase
            # it. A survey run legitimately continues into its next attempt.
            if (previous.get('phase') == 'terminal'
                    and (context['command'] != 31015 or previous.get('outcome') != 'succeeded'
                         or not attempt_id or previous.get('attempt_id') == attempt_id
                         or phase not in ('progress', 'terminal'))):
                return dict(previous)
            event = bounded_event(dict(context, seq=previous.get('seq', 0) + 1, phase=phase, outcome=outcome,
                                       reason=str(reason), attempt_id=str(attempt_id or ''),
                                       preflight_phase=str(preflight_phase or ''), **details))
            if dict(event, seq=previous.get('seq')) == previous:
                return dict(previous)  # unchanged status keeps its seq: no fake renewal
            encode_event(event)
            record['event'] = event
            if attempt_id:
                attempts = record.setdefault('attempts', [])
                if attempt_id not in attempts:
                    attempts.append(attempt_id)
                    del attempts[:-8]
            record['closed'] = self._is_closed(phase, outcome, context['command'])
        if self.journal is not None:
            self.journal.write({'key': key, 'phase': phase, 'outcome': outcome, 'reason': str(reason),
                                'seq': event['seq'], 'attempt_id': str(attempt_id or '')})
        return dict(event)

    def close(self):
        if self.journal is not None:
            self.journal.close()
