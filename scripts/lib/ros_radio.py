"""Bounded ROS control RPC over addressed MAVLink V2_EXTENSION messages."""
import json
import math
import os
import re
import secrets
import struct
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import zlib
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

MESSAGE_TYPE = 31020
HEADER = struct.Struct('<4sBQQIHHH')
MAGIC = b'USR1'
HELLO, READY, REQUEST, ACK, FETCH, RESPONSE, WAIT, ERROR = range(8)
CHUNK = 249 - HEADER.size
MAX_JSON = 131072
MAX_WIRE = 65536
MAX_PARTS = math.ceil(MAX_WIRE / CHUNK)
TIMEOUT = 120.0

# The existing data, map, archive, mission-control and arbitrary serial APIs are
# deliberately outside this transport. Their HTTP routes are unchanged.
GET_PATHS = frozenset((
    '/api/config', '/api/pid/config', '/api/calibration/offsets',
    '/api/presets/auto', '/api/waypoint-sampling', '/api/manual/status',
    '/api/hardware/config', '/api/hardware/serial-ports',
    '/api/diagnostics/link', '/api/diagnostics/system',
    '/api/diagnostics/events', '/api/diagnostics/history',
))
POST_PATHS = frozenset((
    '/api/config', '/api/pid/config',
    '/api/calibration/offsets', '/api/calibration/zero',
    '/api/calibration/reset', '/api/calibration/start',
    '/api/waypoint-sampling', '/api/waypoint-sampling/sync',
    '/api/manual/mode', '/api/manual/pump-step', '/api/manual/stop-all',
    '/api/injection-pump/status', '/api/injection-pump/on',
    '/api/injection-pump/off', '/api/injection-pump/set',
    '/api/hardware/config', '/api/hardware/test-pump-port',
    '/api/hardware/apply', '/api/diagnostics/link/reconnect',
))


def allowed(method, path):
    if method == 'GET' and path in GET_PATHS:
        return True
    if method == 'POST' and path in POST_PATHS:
        return True
    if re.fullmatch(r'/api/preset/auto/[\w-]{1,64}', path):
        return method in ('GET', 'POST', 'DELETE')
    if re.fullmatch(r'/api/waypoint-sampling/[0-9]{1,5}', path):
        return method in ('GET', 'POST', 'DELETE')
    return False


def is_stop(path):
    return path in ('/api/manual/stop-all', '/api/injection-pump/off')


def pack(kind, session, boot, request, index=0, count=0, data=b''):
    if len(data) > CHUNK:
        raise ValueError('radio fragment too large')
    return HEADER.pack(MAGIC, kind, session, boot, request, index, count, len(data)) + data


def unpack(payload):
    payload = bytes(payload)
    if len(payload) < HEADER.size:
        raise ValueError('short radio header')
    magic, kind, session, boot, request, index, count, size = HEADER.unpack_from(payload)
    if magic != MAGIC or kind > ERROR or size > CHUNK or len(payload) < HEADER.size + size:
        raise ValueError('invalid radio header')
    return kind, session, boot, request, index, count, payload[HEADER.size:HEADER.size + size]


def decode_json(data):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(data, MAX_JSON + 1)
    if len(raw) > MAX_JSON or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
        raise ValueError('invalid or oversized compressed JSON')
    return json.loads(raw.decode('utf-8'))


class LocalWebAPI:
    def __init__(self, port=5000):
        if not 1 <= int(port) <= 65535:
            raise ValueError('invalid local Web port')
        self.base = 'http://127.0.0.1:%d' % int(port)
        # A host proxy must never carry local control traffic off the vessel.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, request):
        method, path = request['method'], request['path']
        if not allowed(method, path):
            return 403, {'success': False, 'message': '该接口未开放数传访问'}
        headers = {'Content-Type': 'application/json'}
        authorization = request.get('authorization', '')
        if authorization:
            if not isinstance(authorization, str) or len(authorization) > 512 or '\r' in authorization or '\n' in authorization:
                return 400, {'success': False, 'message': '控制凭证无效'}
            headers['Authorization'] = authorization
        body = None if method == 'GET' else json.dumps(request.get('body', {})).encode('utf-8')
        issued = request.get('_issued_monotonic')
        if issued is not None and not is_stop(path):
            headers['X-USV-Radio-Issued'] = str(issued)
        outgoing = urllib.request.Request(self.base + urllib.parse.quote(path, safe='/'), body, headers, method=method)
        try:
            response = self.opener.open(outgoing, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Web response too large; use HTTP for full history')
            result = json.loads(raw.decode('utf-8'))
            # Retain the complete latest reports; history remains available over
            # HTTP. Radio history/events return the newest twenty entries.
            if path == '/api/diagnostics/system' and isinstance(result.get('data'), dict):
                result['data']['history'] = result['data'].get('history', [])[-20:]
            if path in ('/api/diagnostics/events', '/api/diagnostics/history') and isinstance(result.get('data'), list):
                result['data'] = result['data'][-20:]
            return response.code, result


class RosRadioServer:
    def __init__(self, api, clock=time.monotonic):
        self.api, self.clock = api, clock
        self.boot = secrets.randbits(64) or 1
        self.lock = threading.Lock()
        self.records = OrderedDict()
        self.highwater = {}
        self.outgoing = deque(maxlen=64)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ros-radio')
        self.stop_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ros-radio-stop')

    def close(self):
        self.reset()
        self.worker.shutdown(wait=False)
        self.stop_worker.shutdown(wait=False)

    def reset(self):
        with self.lock:
            self.boot = secrets.randbits(64) or 1
            self.records.clear()
            self.highwater.clear()
            self.outgoing.clear()

    def _reply(self, sender, kind, session, request, index=0, count=0, data=b''):
        self.outgoing.append((sender, pack(kind, session, self.boot, request, index, count, data)))

    def receive(self, sender, payload):
        try:
            kind, session, boot, request, index, count, data = unpack(payload)
        except (ValueError, struct.error, TypeError):
            return
        if not session or not 1 <= sender[0] <= 255 or not 1 <= sender[1] <= 255:
            return
        with self.lock:
            if kind == HELLO:
                self._reply(sender, READY, session, request)
                return
            if boot != self.boot:
                self._reply(sender, ERROR, session, request, data=b'Jetson radio session changed; result unknown')
                return
            key = (*sender, session, request)
            record = self.records.get(key)
            if kind == FETCH:
                if record is None:
                    self._reply(sender, ERROR, session, request, data=b'Request expired; do not replay an action')
                elif record.get('response') is None:
                    self._reply(sender, WAIT, session, request)
                else:
                    chunks = record['response']
                    if index < len(chunks):
                        self._reply(sender, RESPONSE, session, request, index, len(chunks), chunks[index])
                return
            if kind != REQUEST or not request or not 1 <= count <= MAX_PARTS or index >= count:
                return
            if record is None:
                owner = (*sender, session)
                if request <= self.highwater.get(owner, 0):
                    self._reply(sender, ERROR, session, request, data=b'Request retired; do not replay an action')
                    return
                if len(self.highwater) >= 256 and owner not in self.highwater:
                    self._reply(sender, ERROR, session, request, data=b'Radio session limit reached')
                    return
                self.highwater[owner] = request
                # Eviction cannot re-admit an action: the high-water mark lives
                # for this boot; a restart changes the required boot nonce.
                for old_key, old in list(self.records.items()):
                    if self.clock() - old['started'] > TIMEOUT and not old.get('running'):
                        del self.records[old_key]
                if len(self.records) >= 32:
                    completed = next((k for k, v in self.records.items() if v.get('response') is not None), None)
                    if completed is None:
                        self._reply(sender, ERROR, session, request, data=b'Radio request queue full')
                        return
                    del self.records[completed]
                record = dict(parts=[], count=count, started=self.clock(), response=None, submitted=False)
                self.records[key] = record
            if self.clock() - record['started'] > TIMEOUT or count != record['count']:
                self._reply(sender, ERROR, session, request, data=b'Request expired or changed')
                return
            parts = record['parts']
            if index < len(parts):
                if parts[index] != data:
                    self._reply(sender, ERROR, session, request, data=b'Request fragment changed')
                    return
            elif index == len(parts) and not record['submitted']:
                parts.append(data)
            else:
                return
            self._reply(sender, ACK, session, request, index)
            if len(parts) == count and not record['submitted']:
                record['submitted'] = True
                try:
                    operation = decode_json(b''.join(parts))
                    if not isinstance(operation, dict) or not allowed(operation.get('method'), operation.get('path', '')):
                        raise ValueError('该接口未开放数传访问')
                    if operation['method'] != 'GET' and not isinstance(operation.get('body', {}), dict):
                        raise ValueError('请求体应为 JSON 对象')
                    record['operation'] = operation
                    operation['_issued_monotonic'] = self.clock()
                    if is_stop(operation['path']):
                        for k, old in self.records.items():
                            if k[:3] == key[:3] and k != key and not old.get('running') and old.get('response') is None:
                                self._complete(old, 409, {'success': False, 'message': '已由停止操作取消'})
                    executor = self.stop_worker if is_stop(operation['path']) else self.worker
                    executor.submit(self._execute, key, record, self.boot)
                except (ValueError, TypeError, zlib.error, UnicodeError) as exc:
                    self._complete(record, 400, {'success': False, 'message': str(exc)})

    @staticmethod
    def _complete(record, status, result):
        raw = json.dumps({'status': status, 'body': result}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_JSON:
            raw = b'{"status":413,"body":{"success":false,"message":"Radio response too large; use HTTP"}}'
        compressed = zlib.compress(raw)
        if len(compressed) > MAX_WIRE:
            compressed = zlib.compress(b'{"status":413,"body":{"success":false,"message":"Radio response too large; use HTTP"}}')
        record['response'] = [compressed[i:i + CHUNK] for i in range(0, len(compressed), CHUNK)]

    def _execute(self, key, record, boot):
        with self.lock:
            if self.boot != boot or self.records.get(key) is not record or record['response'] is not None:
                return
            if self.clock() - record['started'] > TIMEOUT:
                self._complete(record, 408, {'success': False, 'message': '请求已过期，未执行'})
                return
            record['running'] = True
        try:
            status, result = self.api(record['operation'])
        except Exception as exc:
            # A local HTTP timeout may follow actual execution. Cache this
            # uncertain result; never submit that request to the API again.
            status, result = 502, {'success': False, 'message': 'ROS 请求结果未知，请读取设备状态：%s' % exc}
        with self.lock:
            if self.boot == boot and self.records.get(key) is record:
                self._complete(record, status, result)

    def peek(self):
        with self.lock:
            return self.outgoing[0] if self.outgoing else None

    def cancel_pending(self):
        with self.lock:
            for record in self.records.values():
                if not record.get('running') and record.get('response') is None:
                    self._complete(record, 409, {'success': False, 'message': '已由停止操作取消'})

    def sent(self, item):
        with self.lock:
            if self.outgoing and self.outgoing[0] is item:
                self.outgoing.popleft()
