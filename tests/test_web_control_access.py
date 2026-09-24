import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.web_config_server import FLASK_AVAILABLE, WebConfigServer


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask unavailable')
class WebControlAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.tmp.name, 'USV_WEB_CONTROL_TOKEN': ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_remote_write_is_denied_without_credentials_but_monitoring_remains(self):
        server = WebConfigServer(standalone=True)
        client = server.app.test_client()
        response = client.post('/api/mission/start', environ_base={'REMOTE_ADDR': '10.42.0.2'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.get('/api/diagnostics/system', environ_base={'REMOTE_ADDR': '10.42.0.2'}).status_code, 200)

    def test_cross_origin_write_is_rejected_even_on_loopback(self):
        server = WebConfigServer(standalone=True)
        response = server.app.test_client().post('/api/mission/start', headers={'Origin': 'https://untrusted.invalid'})
        self.assertEqual(response.status_code, 403)

    def test_remote_write_requires_matching_token(self):
        with patch.dict(os.environ, {'USV_WEB_CONTROL_TOKEN': 'test-only-token-not-a-real-secret'}):
            server = WebConfigServer(standalone=True)
        client = server.app.test_client()
        remote = {'REMOTE_ADDR': '10.42.0.2'}
        self.assertEqual(client.post('/api/mission/start', environ_base=remote).status_code, 401)
        response = client.post('/api/mission/start', environ_base=remote,
                               headers={'Authorization': 'Bearer test-only-token-not-a-real-secret'})
        self.assertEqual(response.status_code, 200)  # standalone response, never actuates hardware


if __name__ == '__main__':
    unittest.main()
