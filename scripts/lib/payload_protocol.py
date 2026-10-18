"""Versioned payload requests; durable admission prevents replay after restart."""
import json
import math
import os
import sqlite3
import threading
from collections import OrderedDict

MAX_ID = (1 << 24) - 1
MAX_EVENT_BYTES = 750
SAFETY_COMMANDS = (31011, 31016, 31019)


def next_transport_session(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, timeout=0.05)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS transport (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM transport WHERE id=1').fetchone()
            value = row[0] + 1 if row else 1
            if value > MAX_ID:
                raise ValueError('Transport session identity exhausted')
            db.execute('INSERT OR REPLACE INTO transport VALUES (1,?)', (value,))
        return value
    finally:
        db.close()


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


class RequestLedger:
    """One local sqlite transaction before dispatch, 256 replies + 64 watermarks.

    Watermarks are never evicted. Older/forgotten requests fail closed, including
    STOP retries, so an old STOP cannot stop a newer accepted task. A lost disk
    or exhausted identity space needs operator maintenance, never a silent reset.
    """
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, timeout=0.05, check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS watermark (sender TEXT PRIMARY KEY, session INTEGER, request INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS reply (key TEXT PRIMARY KEY, signature TEXT, event TEXT)')
        self.db.commit()
        # This lock never covers disk I/O. STOP can reserve while fsync stalls.
        self.reservation_lock = threading.Lock()
        self.watermarks = {sender: (session, request) for sender, session, request in
                           self.db.execute('SELECT sender,session,request FROM watermark')}
        self.replies = OrderedDict((key, (signature, json.loads(event) if event else None))
                                   for key, signature, event in self.db.execute(
                                       'SELECT key,signature,event FROM reply ORDER BY rowid'))

    @staticmethod
    def key(context):
        return '{}:{}:{}:{}'.format(context['sender_system'], context['sender_component'],
                                   context['session'], context['request'])

    def reserve(self, context, param1, param2):
        """Volatile replay gate; call persist before any non-safety action."""
        if not all(math.isfinite(value) for value in (param1, param2)):
            raise ValueError('Non-finite command parameters')
        key = self.key(context)
        sender = '{}:{}'.format(context['sender_system'], context['sender_component'])
        signature = json.dumps([context['command'], param1, param2])
        with self.reservation_lock:
            prior = self.replies.get(key)
            if prior:
                if prior[0] != signature:
                    raise ValueError('Request identity reused with different parameters')
                return False, prior[1]
            watermark = self.watermarks.get(sender)
            if watermark and (context['session'], context['request']) <= tuple(watermark):
                raise ValueError('Retired payload request')
            if not watermark and len(self.watermarks) >= 64:
                raise ValueError('Payload sender capacity reached')
            self.watermarks[sender] = (context['session'], context['request'])
            self.replies[key] = (signature, None)
            while len(self.replies) > 256:
                self.replies.popitem(last=False)
        return True, None

    def persist(self, context, param1, param2):
        key = self.key(context)
        sender = '{}:{}'.format(context['sender_system'], context['sender_component'])
        signature = json.dumps([context['command'], param1, param2])
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior = self.db.execute('SELECT session,request FROM watermark WHERE sender=?', (sender,)).fetchone()
            if not prior or (context['session'], context['request']) > tuple(prior):
                self.db.execute('INSERT OR REPLACE INTO watermark VALUES (?,?,?)',
                                (sender, context['session'], context['request']))
            self.db.execute('INSERT OR IGNORE INTO reply VALUES (?,?,NULL)', (key, signature))
            self.db.execute('DELETE FROM reply WHERE rowid NOT IN (SELECT rowid FROM reply ORDER BY rowid DESC LIMIT 256)')

    def admit(self, context, param1, param2):
        admitted, previous = self.reserve(context, param1, param2)
        if admitted:
            self.persist(context, param1, param2)
        return admitted, previous

    def event(self, context, phase, outcome='', reason='', attempt_id='', preflight_phase='', **details):
        with self.lock, self.db:
            row = self.db.execute('SELECT event FROM reply WHERE key=?', (self.key(context),)).fetchone()
            if not row:
                return None
            previous = json.loads(row[0]) if row[0] else {}
            # Once a terminal result exists, late accepted/progress cannot erase it.
            if (previous.get('phase') == 'terminal'
                    and (context['command'] != 31015 or previous.get('outcome') != 'succeeded'
                         or not attempt_id or previous.get('attempt_id') == attempt_id
                         or phase not in ('progress', 'terminal'))):
                return previous
            event = bounded_event(dict(context, seq=previous.get('seq', 0) + 1, phase=phase, outcome=outcome,
                         reason=str(reason), attempt_id=str(attempt_id or ''),
                         preflight_phase=str(preflight_phase or ''), **details))
            if dict(event, seq=previous.get('seq')) == previous:
                return previous  # Unchanged 1 Hz status must not rewrite flash.
            encode_event(event)
            self.db.execute('UPDATE reply SET event=? WHERE key=?', (json.dumps(event), self.key(context)))
            with self.reservation_lock:
                key = self.key(context)
                if key in self.replies:
                    self.replies[key] = (self.replies[key][0], event)
            return event

    def close(self):
        self.db.close()
