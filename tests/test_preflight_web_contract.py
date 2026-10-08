"""Preflight settings persist, are validated, and reach the atomic start RPC."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_hardware_runtime_sync import _load_script


class PreflightWebContractTests(unittest.TestCase):
    def setUp(self):
        self.module, _, _ = _load_script('preflight_web', 'scripts/web_config_server.py')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = str(Path(self.tmp.name) / 'config.json')
        original = self.module.ConfigManager
        class TempConfigManager(original):
            def __init__(self):
                super().__init__(path)
        self.module.ConfigManager = TempConfigManager
        self.server = self.module.WebConfigServer(standalone=False)
        self.server.calibration_manager = self.module.CalibrationManager(str(Path(self.tmp.name) / 'calibration.json'))
        self.client = self.server.app.test_client()

    def test_config_persistence_and_request_pump_settings_reach_start(self):
        policy = {'oil_axis': 'Y', 'separation_turns': 2.5, 'separation_rpm': 7}
        response = self.client.post('/api/config', json={'pump_settings': {'preflight': policy}})
        self.assertEqual(response.status_code, 200)
        self.server.config_manager.load()
        self.assertEqual(self.client.get('/api/config').get_json()['pump_settings']['preflight'], policy)
        payloads = []
        self.server._call_control_command = lambda action, payload: (payloads.append((action, payload)) or True, 'accepted', {})
        self.server._start_data_recording_if_needed = Mock()
        self.server._start_sample_window_if_needed = Mock()
        policy['oil_axis'] = 'A'
        self.assertTrue(self.client.post('/api/mission/start', json={
            'require_gps': False, 'pump_settings': {'preflight': policy}}).get_json()['success'])
        self.assertEqual(payloads[0][0], 'automation_start')
        self.assertEqual(payloads[0][1]['preflight'], policy)
        self.assertEqual(self.server.config_manager.get()['pump_settings']['preflight'], policy)

    def test_invalid_settings_reject_and_preserve_persisted_config(self):
        before = self.server.config_manager.get()['pump_settings']['preflight']
        for value in ({'oil_axis': 'B'}, {'separation_rpm': 0}, {'separation_rpm': 100},
                      {'oil_axis': 'A', 'separation_turns': -1}, {'separation_turns': 1}):
            with self.subTest(value=value):
                response = self.client.post('/api/config', json={'pump_settings': {'preflight': value}})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.server.config_manager.get()['pump_settings']['preflight'], before)

    def test_relative_zero_and_settings_share_one_saved_source(self):
        self.assertEqual(self.client.get('/api/calibration/offsets').get_json()['configured_axes'], [])
        response = self.client.post('/api/calibration/offsets', json={'offsets': {'X': 120, 'A': 0}})
        self.assertTrue(response.get_json()['success'])
        restored = self.module.CalibrationManager(self.server.calibration_manager.file_path)
        self.assertEqual(restored.configured_axes, {'X', 'A'})
        self.assertEqual(restored.offsets['X'], 120)
        self.server.raw_angles = {'X': 132}
        self.assertTrue(self.client.post('/api/calibration/zero', json={'axis': 'X'}).get_json()['success'])
        self.assertEqual(self.client.get('/api/calibration/offsets').get_json()['data']['X'], 132)

    def test_bad_zero_or_disk_failure_never_changes_live_zero(self):
        for offsets in ({'X': 360}, {'Y': -1}, {'Z': 'nan'}, {'B': 4}, {'A': True}):
            with self.subTest(offsets=offsets):
                self.assertEqual(self.client.post('/api/calibration/offsets', json={'offsets': offsets}).status_code, 400)
        self.server.calibration_manager.save = Mock(return_value=False)
        self.assertEqual(self.client.post('/api/calibration/offsets', json={'offsets': {'X': 120}}).status_code, 500)
        self.assertEqual(self.server.calibration_manager.offsets['X'], 0)
        self.assertFalse(self.server.calibration_manager.configured_axes)

    def test_active_task_cannot_mutate_or_reset_zero(self):
        self.server.automation_running = True
        for url, payload in (('/api/calibration/offsets', {'offsets': {'X': 10}}),
                             ('/api/calibration/zero', {'axis': 'X'}),
                             ('/api/calibration/reset', {})):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, json=payload).status_code, 409)

    def test_config_write_failure_prevents_start_dispatch(self):
        self.server.config_manager.save = Mock(return_value=False)
        self.server._call_control_command = Mock()
        response = self.client.post('/api/mission/start', json={
            'require_gps': False, 'pump_settings': {'preflight': {'oil_axis': 'X', 'separation_turns': 1}}})
        self.assertEqual(response.status_code, 500)
        self.server._call_control_command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
