"""Server-persisted automation_policy.require_gps contract.

The GPS requirement switch on the Web Automation page must survive page
switches, browser reloads and Jetson web server restarts. The per-run start
payload still overrides the persisted default explicitly.
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_hardware_runtime_sync import _load_script


class AutomationPolicyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.module, _, _ = _load_script(
            'web_config_automation_policy_test',
            'scripts/web_config_server.py',
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_file = str(Path(self._tmp.name) / 'sampling_config.json')

    def make_manager(self):
        """Create a manager exactly like production: construct then load()."""
        class TempConfigManager(self.module.ConfigManager):
            def __init__(self, _config_file=self.config_file):
                super().__init__(_config_file)

        manager = TempConfigManager(self.config_file)
        manager.load()
        return manager

    def test_default_require_gps_is_true(self):
        manager = self.make_manager()
        self.assertTrue(manager.get()['automation_policy']['require_gps'])

    def test_legacy_config_without_policy_gets_safe_default(self):
        legacy = {'version': '1.0', 'sampling_sequence': {'steps': [], 'loop_count': 1}}
        with open(self.config_file, 'w', encoding='utf-8') as handle:
            json.dump(legacy, handle)
        manager = self.make_manager()
        self.assertTrue(manager.get()['automation_policy']['require_gps'])
        # A partial policy object also falls back to the safe default.
        with open(self.config_file, 'w', encoding='utf-8') as handle:
            json.dump({'automation_policy': {}}, handle)
        manager = self.make_manager()
        self.assertTrue(manager.get()['automation_policy']['require_gps'])

    def test_update_persists_across_manager_reload(self):
        manager = self.make_manager()
        self.assertTrue(manager.update({'automation_policy': {'require_gps': False}}))
        self.assertFalse(manager.get()['automation_policy']['require_gps'])
        # Restart: a fresh manager reading the same file keeps the setting.
        manager2 = self.make_manager()
        self.assertFalse(manager2.get()['automation_policy']['require_gps'])

    def test_invalid_policy_values_fall_back_to_safe_default(self):
        manager = self.make_manager()
        manager.update({'automation_policy': {'require_gps': 'not-a-bool'}})
        # bool('not-a-bool') is True: non-empty junk cannot silently disable GPS.
        self.assertTrue(manager.get()['automation_policy']['require_gps'])
        manager.update({'automation_policy': {'require_gps': False}})
        self.assertFalse(manager.get()['automation_policy']['require_gps'])

    def test_api_config_roundtrip(self):
        class TempConfigManager(self.module.ConfigManager):
            def __init__(self, _config_file=self.config_file):
                super().__init__(_config_file)

        self.module.ConfigManager = TempConfigManager
        server = self.module.WebConfigServer(standalone=False)
        client = server.app.test_client()

        get_resp = client.get('/api/config')
        self.assertEqual(get_resp.status_code, 200)
        self.assertTrue(get_resp.get_json()['automation_policy']['require_gps'])

        post_resp = client.post('/api/config', json={'automation_policy': {'require_gps': False}})
        self.assertEqual(post_resp.status_code, 200)

        get_resp = client.get('/api/config')
        self.assertFalse(get_resp.get_json()['automation_policy']['require_gps'])

        # Reload Automation: config endpoint still reports the persisted value.
        reloaded = client.get('/api/config')
        self.assertFalse(reloaded.get_json()['automation_policy']['require_gps'])


if __name__ == '__main__':
    unittest.main()
