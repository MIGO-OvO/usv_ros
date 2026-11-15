"""Legacy require_gps flags are removed on load, update and server restart."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

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

    def test_default_policy_has_no_gps_requirement(self):
        manager = self.make_manager()
        self.assertNotIn('require_gps', manager.get()['automation_policy'])

    def test_legacy_config_without_policy_does_not_add_a_gps_requirement(self):
        legacy = {'version': '1.0', 'sampling_sequence': {'steps': [], 'loop_count': 1}}
        with open(self.config_file, 'w', encoding='utf-8') as handle:
            json.dump(legacy, handle)
        manager = self.make_manager()
        self.assertNotIn('require_gps', manager.get()['automation_policy'])
        with open(self.config_file, 'w', encoding='utf-8') as handle:
            json.dump({'automation_policy': {}}, handle)
        manager = self.make_manager()
        self.assertNotIn('require_gps', manager.get()['automation_policy'])

    def test_old_flags_are_discarded_on_load_save_and_reload(self):
        for value in (True, False, 'not-a-bool', None):
            with self.subTest(value=value):
                Path(self.config_file).write_text(json.dumps({
                    'automation_policy': {'require_gps': value, 'legacy_extension': 'retained'},
                }), encoding='utf-8')
                manager = self.make_manager()
                self.assertEqual(manager.get()['automation_policy'], {'legacy_extension': 'retained'})
                self.assertTrue(manager.update({'automation_policy': {'require_gps': value}}))
                saved = json.loads(Path(self.config_file).read_text(encoding='utf-8'))
                self.assertNotIn('require_gps', saved['automation_policy'])
                self.assertEqual(self.make_manager().get()['automation_policy'], {'legacy_extension': 'retained'})

    def test_legacy_flag_values_are_ignored_but_policy_must_be_an_object(self):
        manager = self.make_manager()
        for value in ('not-a-bool', '', 0, 1, None, [], {}):
            self.assertTrue(manager.update({'automation_policy': {'require_gps': value}}))
            self.assertNotIn('require_gps', manager.get()['automation_policy'])
        for value in (None, False, [], 'invalid'):
            with self.assertRaises(ValueError):
                manager.update({'automation_policy': value})

    def test_disk_failure_rolls_back_in_memory_policy(self):
        manager = self.make_manager()
        manager.save = Mock(return_value=False)
        self.assertFalse(manager.update({'automation_policy': {'legacy_extension': 'changed'}}))
        self.assertEqual(manager.get()['automation_policy'], {})

    def test_failed_file_replace_preserves_saved_config_and_rolls_back_memory(self):
        manager = self.make_manager()
        self.assertTrue(manager.save())
        saved = Path(self.config_file).read_bytes()
        with patch.object(self.module.os, 'replace', side_effect=OSError('disk error')):
            self.assertFalse(manager.update({'sampling_sequence': {'loop_count': 8}}))
        self.assertEqual(Path(self.config_file).read_bytes(), saved)
        self.assertEqual(manager.get()['sampling_sequence']['loop_count'], 1)

    def test_overlapping_autosaves_keep_both_sections_and_rollback_only_failed_write(self):
        manager = self.make_manager()
        entered, release = threading.Event(), threading.Event()
        save = manager.save

        def delayed_save():
            if threading.current_thread().name == 'failed-config-write':
                entered.set()
                release.wait(2)
                return False
            return save()

        manager.save = delayed_save
        results = []
        second_entered, second_finished = threading.Event(), threading.Event()

        def save_waypoints():
            second_entered.set()
            results.append(manager.update({'waypoint_sampling': {'3': {'enabled': True, 'loop_count': 4}}}))
            second_finished.set()

        failed = threading.Thread(name='failed-config-write', target=lambda: results.append(
            manager.update({'sampling_sequence': {'loop_count': 7}})))
        succeeded = threading.Thread(target=save_waypoints)
        failed.start()
        self.assertTrue(entered.wait(1))
        succeeded.start()
        try:
            self.assertTrue(second_entered.wait(1))
            self.assertFalse(second_finished.wait(0.1), 'second write bypassed the in-flight transaction')
        finally:
            release.set()
        failed.join(3)
        succeeded.join(3)
        self.assertFalse(failed.is_alive())
        self.assertFalse(succeeded.is_alive())
        self.assertCountEqual(results, [False, True])
        self.assertEqual(manager.get()['sampling_sequence']['loop_count'], 1)
        self.assertEqual(manager.get()['waypoint_sampling']['3']['loop_count'], 4)
        self.assertEqual(self.make_manager().get()['waypoint_sampling']['3']['loop_count'], 4)

    def test_waypoint_map_replacement_persists_deletion_including_last_item(self):
        manager = self.make_manager()
        self.assertTrue(manager.update({'waypoint_sampling': {'1': {}, '2': {}}}))
        self.assertTrue(manager.update({'waypoint_sampling': {'2': {}}}))
        self.assertEqual(set(self.make_manager().get()['waypoint_sampling']), {'2'})
        self.assertTrue(manager.update({'waypoint_sampling': {}}))
        self.assertEqual(self.make_manager().get()['waypoint_sampling'], {})

    def test_api_config_roundtrip(self):
        class TempConfigManager(self.module.ConfigManager):
            def __init__(self, _config_file=self.config_file):
                super().__init__(_config_file)

        self.module.ConfigManager = TempConfigManager
        server = self.module.WebConfigServer(standalone=False)
        client = server.app.test_client()
        for value in ('invalid', [], False, None):
            response = client.post('/api/config', json={'automation_policy': value})
            self.assertEqual(response.status_code, 400)

        get_resp = client.get('/api/config')
        self.assertEqual(get_resp.status_code, 200)
        self.assertNotIn('require_gps', get_resp.get_json()['automation_policy'])
        for value in (True, False, 'false', '', 0, None):
            post_resp = client.post('/api/config', json={
                'automation_policy': {'require_gps': value, 'legacy_extension': 'retained'},
            })
            self.assertEqual(post_resp.status_code, 200)
            reloaded = client.get('/api/config')
            self.assertEqual(reloaded.get_json()['automation_policy'], {'legacy_extension': 'retained'})
        self.assertEqual(self.make_manager().get()['automation_policy'], {'legacy_extension': 'retained'})


if __name__ == '__main__':
    unittest.main()
