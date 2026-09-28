import os
import base64
import tempfile
import unittest
from unittest.mock import patch

from scripts.web_config_server import FLASK_AVAILABLE, WebConfigServer


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask unavailable')
class WebControlAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.tmp.name,
                                          'USV_WEB_CONTROL_TOKEN': '', 'USV_WEB_REQUIRE_AUTH': ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_lan_controls_work_without_login_by_default(self):
        server = WebConfigServer(standalone=True)
        client = server.app.test_client()
        for path, payload in (('/api/mission/start', {}),
                              ('/api/spectrometer/start', {}),
                              ('/api/motor/command', {'command': 'XFWV100J10'}),
                              ('/api/motor/stop', {})):
            with self.subTest(path=path):
                response = client.post(path, json=payload, base_url='http://10.42.0.1:5000',
                                       environ_base={'REMOTE_ADDR': '10.42.0.2'},
                                       headers={'Origin': 'http://10.42.0.1:5000',
                                                'Sec-Fetch-Site': 'same-origin'})
                self.assertEqual(response.status_code, 200)
                if path == '/api/mission/start':
                    # The access guard passes; standalone still requires ROS for a mission.
                    self.assertFalse(response.get_json()['success'])
                    self.assertIn('ROS', response.get_json()['message'])
                else:
                    self.assertTrue(response.get_json()['success'])  # standalone, no hardware
        self.assertEqual(client.get('/api/diagnostics/system', environ_base={'REMOTE_ADDR': '10.42.0.2'}).status_code, 200)

    def test_cross_origin_write_is_rejected_even_on_loopback(self):
        server = WebConfigServer(standalone=True)
        response = server.app.test_client().post('/api/mission/start', headers={'Origin': 'https://untrusted.invalid'})
        self.assertEqual(response.status_code, 403)

    def test_remote_write_requires_matching_token(self):
        with patch.dict(os.environ, {'USV_WEB_REQUIRE_AUTH': '1',
                                    'USV_WEB_CONTROL_TOKEN': 'test-only-token-not-a-real-secret'}):
            server = WebConfigServer(standalone=True)
        client = server.app.test_client()
        remote = {'REMOTE_ADDR': '10.42.0.2'}
        self.assertEqual(client.post('/api/mission/start', environ_base=remote).status_code, 401)
        response = client.post('/api/mission/start', environ_base=remote,
                               headers={'Authorization': 'Bearer test-only-token-not-a-real-secret'})
        self.assertEqual(response.status_code, 200)  # standalone response, never actuates hardware

    def test_token_alone_does_not_enable_auth(self):
        for token in ('short', 'test-only-token-not-a-real-secret'):
            with self.subTest(token_length=len(token)), patch.dict(os.environ, {'USV_WEB_CONTROL_TOKEN': token}):
                client = WebConfigServer(standalone=True).app.test_client()
                self.assertEqual(client.post('/api/mission/start', base_url='http://10.42.0.1:5000',
                                             environ_base={'REMOTE_ADDR': '10.42.0.2'}).status_code, 200)
                self.assertEqual(client.get('/api/control/auth').status_code, 200)

    def test_auth_requires_valid_configuration(self):
        from flask import Flask
        from scripts.lib.web_access import install_control_access
        for token in ('', 'short'):
            with self.subTest(token_length=len(token)), patch.dict(os.environ, {
                    'USV_WEB_REQUIRE_AUTH': '1', 'USV_WEB_CONTROL_TOKEN': token}):
                with self.assertRaisesRegex(ValueError, 'at least 16 characters'):
                    install_control_access(Flask(__name__))

    def test_basic_auth_and_local_requests_in_auth_mode(self):
        token = 'test-only-token-not-a-real-secret'
        with patch.dict(os.environ, {'USV_WEB_REQUIRE_AUTH': '1', 'USV_WEB_CONTROL_TOKEN': token}):
            client = WebConfigServer(standalone=True).app.test_client()
        self.assertEqual(client.post('/api/mission/start').status_code, 401)
        self.assertEqual(client.get('/api/diagnostics/system').status_code, 200)
        response = client.get('/api/control/auth')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Basic realm=', response.headers['WWW-Authenticate'])
        for username, password, expected in (('operator', token, 200),
                                              ('operator', 'wrong', 401), ('other', token, 401)):
            with self.subTest(username=username, expected=expected):
                encoded = base64.b64encode(f'{username}:{password}'.encode()).decode()
                headers = {'Authorization': 'Basic ' + encoded}
                self.assertEqual(client.get('/api/control/auth', headers=headers).status_code, expected)
                self.assertEqual(client.post('/api/mission/start', headers=headers).status_code, expected)
        self.assertEqual(client.post('/api/mission/start', headers={
            'Authorization': 'Bearer wrong'}).status_code, 401)

    def test_cross_origin_and_cross_site_blocked_in_both_modes(self):
        token = 'test-only-token-not-a-real-secret'
        for mode in ('0', '1'):
            with patch.dict(os.environ, {'USV_WEB_REQUIRE_AUTH': mode, 'USV_WEB_CONTROL_TOKEN': token}):
                client = WebConfigServer(standalone=True).app.test_client()
            for protection in ({'Origin': 'https://untrusted.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(mode=mode, protection=protection):
                    headers = dict(protection, Authorization='Bearer ' + token)
                    self.assertEqual(client.post('/api/mission/start', headers=headers).status_code, 403)
                    self.assertEqual(client.get('/api/control/auth', headers=headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
