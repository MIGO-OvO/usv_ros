"""RPC loss/replay checks use fake API actions; no hardware is opened."""
import json
import threading
import time
import unittest
import zlib
import os
from types import SimpleNamespace
from unittest.mock import patch
from scripts.lib import ros_radio as radio


class RosRadioTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.server = radio.RosRadioServer(lambda op: self.calls.append(op) or (200, {'success': True, 'data': op.get('body', {})}))
        self.addCleanup(self.server.close)
        self.sender, self.session = (254, 190), 123

    def send(self, kind, request=1, index=0, count=0, data=b'', boot=None):
        self.server.receive(self.sender, radio.pack(kind, self.session, self.server.boot if boot is None else boot, request, index, count, data))
        item = self.server.peek()
        self.assertIsNotNone(item)
        self.server.sent(item)
        return radio.unpack(item[1])

    def submit(self, operation, request=1):
        data = zlib.compress(json.dumps(operation).encode())
        parts = [data[n:n + radio.CHUNK] for n in range(0, len(data), radio.CHUNK)]
        for i, part in enumerate(parts):
            self.assertEqual(self.send(radio.REQUEST, request, i, len(parts), part)[0], radio.ACK)
        return parts

    def result(self, request=1):
        end, parts = time.monotonic() + 2, []
        while time.monotonic() < end:
            reply = self.send(radio.FETCH, request, len(parts))
            if reply[0] == radio.WAIT:
                time.sleep(.005)
                continue
            self.assertEqual(reply[0], radio.RESPONSE)
            parts.append(reply[-1])
            if len(parts) == reply[5]:
                return radio.decode_json(b''.join(parts))
        self.fail('RPC result deadline')

    def test_fragment_duplicates_lost_result_and_restart_do_not_repeat_a_move(self):
        operation = {'method': 'POST', 'path': '/api/manual/pump-step', 'body': {'axis': 'X', 'angle_deg': 1800}}
        parts = self.submit(operation)
        self.assertEqual(self.result()['status'], 200)
        for _ in range(3):
            self.send(radio.REQUEST, 1, 0, len(parts), parts[0])
            self.assertEqual(self.result()['body']['success'], True)
        self.assertEqual(len(self.calls), 1)
        old_boot = self.server.boot
        self.server.reset()
        self.assertEqual(self.send(radio.REQUEST, 1, 0, len(parts), parts[0], old_boot)[0], radio.ERROR)
        self.assertEqual(len(self.calls), 1)

    def test_evicted_results_cannot_re_admit_old_requests(self):
        for n in range(1, 36):
            self.submit({'method': 'POST', 'path': '/api/config', 'body': {'value': n}}, n)
            self.result(n)
        data = zlib.compress(b'{"method":"POST","path":"/api/config","body":{}}')
        self.assertEqual(self.send(radio.REQUEST, 1, 0, 1, data)[0], radio.ERROR)
        self.assertEqual(len(self.calls), 35)

    def test_data_files_raw_commands_and_unlisted_methods_are_rejected(self):
        for method, path in [('GET', '/api/data/missions'), ('GET', '/api/map/live'), ('POST', '/api/motor/command'), ('DELETE', '/api/config'), ('GET', '/api/diagnostics/export'), ('POST', '/api/pid/test')]:
            self.assertFalse(radio.allowed(method, path))
        self.submit({'method': 'POST', 'path': '/api/motor/command', 'body': {'command': 'START'}})
        self.assertEqual(self.result()['status'], 400)
        self.assertEqual(self.calls, [])
        self.assertTrue(radio.allowed('POST', '/api/preset/auto/五圈'))
        self.assertFalse(radio.allowed('POST', '/api/preset/auto/../other'))

    def test_stop_has_independent_worker_and_cancels_queued_actions(self):
        entered, release = threading.Event(), threading.Event()
        def api(op):
            self.calls.append(op['path'])
            if op['path'] == '/api/config':
                entered.set()
                release.wait(2)
            return 200, {'success': True}
        self.server.api = api
        self.addCleanup(release.set)
        self.submit({'method': 'GET', 'path': '/api/config'}, 1)
        self.assertTrue(entered.wait(1))
        self.submit({'method': 'POST', 'path': '/api/manual/pump-step', 'body': {}}, 2)
        self.submit({'method': 'POST', 'path': '/api/manual/stop-all', 'body': {}}, 3)
        self.assertEqual(self.result(3)['status'], 200)
        self.assertEqual(self.result(2)['status'], 409)
        release.set()
        self.assertNotIn('/api/manual/pump-step', self.calls)

    def test_local_http_timeout_is_cached_as_unknown(self):
        def failed(op):
            self.calls.append(op)
            raise TimeoutError('fake reply loss')
        self.server.api = failed
        parts = self.submit({'method': 'POST', 'path': '/api/injection-pump/on', 'body': {'speed': 20}})
        self.assertEqual(self.result()['status'], 502)
        self.send(radio.REQUEST, 1, 0, len(parts), parts[0])
        self.assertEqual(self.result()['status'], 502)
        self.assertEqual(len(self.calls), 1)

    def test_codec_limits_and_explicit_length_survive_mavlink_zero_padding(self):
        frame = radio.pack(radio.REQUEST, 1, 2, 3, 0, 1, b'a\0b')
        self.assertEqual(radio.unpack(frame.ljust(249, b'\0'))[-1], b'a\0b')
        with self.assertRaises(ValueError):
            radio.decode_json(zlib.compress(b'x' * (radio.MAX_JSON + 1)))
        with self.assertRaises(ValueError):
            radio.decode_json(zlib.compress(b'{}') + b'garbage')


class LocalWebTests(unittest.TestCase):
    def test_loopback_preserves_existing_auth_and_scope(self):
        from flask import Flask, jsonify, request
        from werkzeug.serving import make_server
        from scripts.lib.web_access import install_control_access
        app = Flask(__name__)
        token = 'test-only-control-token'
        with patch.dict(os.environ, {'USV_WEB_REQUIRE_AUTH': '1', 'USV_WEB_CONTROL_TOKEN': token}):
            install_control_access(app)
        @app.route('/api/manual/stop-all', methods=['POST'])
        @app.route('/api/config', methods=['POST'])
        def control():
            return jsonify(success=True, issued=request.headers.get('X-USV-Radio-Issued'))
        http = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        api = radio.LocalWebAPI(http.server_port)
        operation = dict(method='POST', path='/api/config', body={}, _issued_monotonic=42)
        self.assertEqual(api(operation)[0], 401)
        operation['authorization'] = 'Bearer ' + token
        self.assertEqual(api(operation)[1]['issued'], '42')
        operation['path'] = '/api/manual/stop-all'
        self.assertIsNone(api(operation)[1]['issued'])  # STOP never expires behind an earlier action.
        operation['path'] = '/api/motor/command'
        self.assertEqual(api(operation)[0], 403)

    def test_radio_admission_time_reaches_the_existing_stop_cutoff(self):
        from flask import Flask
        from scripts import web_config_server as web
        app = Flask(__name__)
        instance = object.__new__(web.WebConfigServer)
        calls = []
        def service(*args):
            calls.append(json.loads(args[-1]))
            return SimpleNamespace(success=True, message='ok', result_json='{}')
        ros = SimpleNamespace(wait_for_service=lambda *a, **k: None, ServiceProxy=lambda *a: service)
        with patch.object(web, 'rospy', ros, create=True), patch.object(web, 'ControlCommand', object, create=True), patch.object(web.time, 'monotonic', return_value=100):
            for remote, headers, expected in [
                ('127.0.0.1', {'X-USV-Radio-Issued': '95'}, 95),
                ('10.42.0.2', {'X-USV-Radio-Issued': '95'}, 100),
                ('127.0.0.1', {}, 100),
            ]:
                with app.test_request_context(headers=headers, environ_base={'REMOTE_ADDR': remote}):
                    self.assertTrue(instance._call_control_command('manual_step')[0])
                    self.assertEqual(calls[-1]['_issued_monotonic'], expected)
            count = len(calls)
            for value in ('NaN', '101', '89', 'invalid'):
                with app.test_request_context(headers={'X-USV-Radio-Issued': value}, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
                    self.assertFalse(instance._call_control_command('manual_step')[0])
            self.assertEqual(len(calls), count)


if __name__ == '__main__':
    unittest.main()
