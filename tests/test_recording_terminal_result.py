import csv
import io
import json
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace

from scripts.lib.sample_recording.storage import SampleRecordingStorage
from scripts.web_config_server import FLASK_AVAILABLE, MissionDataManager, WebConfigServer


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask is not installed')
class RecordingTerminalTests(unittest.TestCase):
    def test_start_rejection_is_saved_without_sampling_started_in_either_order(self):
        for result_first in (False, True):
            with self.subTest(result_first=result_first), tempfile.TemporaryDirectory() as directory:
                server = WebConfigServer(standalone=True)
                server.data_manager = MissionDataManager(directory)
                server.sample_storage = SampleRecordingStorage(directory)
                attempt = 'c' * 32
                context = dict(attempt_id=attempt, source='manual', record_id='d' * 32,
                               timestamp_start=time.time(), gps_snapshot={'position_source': 'manual_no_gps'})
                cleanup = dict(cleanup='not_admitted', attempt_id=attempt, owner_attempt_id=None)
                result = dict(source='manual', attempt_id=attempt, outcome='failed',
                              reason='Controller fault watchdog_tripped; reconnect controller',
                              start_admission='rejected', cleanup_result=cleanup)
                context_msg = SimpleNamespace(data='sampling_context:' + json.dumps(context))
                result_msg = SimpleNamespace(data=json.dumps(result))
                if result_first:
                    server._sampling_result_cb(result_msg)
                    server._trigger_status_cb(context_msg)
                else:
                    server._trigger_status_cb(context_msg)
                    server._sampling_result_cb(result_msg)
                missions = server.data_manager.list_missions()
                self.assertEqual(len(missions), 1)
                data = server.data_manager.get_mission(missions[0]['id'])
                window = data['sample_windows'][0]
                self.assertEqual(window['state'], 'closed')
                self.assertEqual(window['outcome'], 'failed')
                self.assertEqual(window['terminal_reason'], result['reason'])
                self.assertEqual(window['start_admission'], 'rejected')
                self.assertEqual(window['cleanup_result'], cleanup)
                self.assertFalse(data['data_points'])
                with server._build_mission_archive(data) as archive, zipfile.ZipFile(archive) as zipped:
                    rows = list(csv.DictReader(io.StringIO(zipped.read(
                        'mission_' + data['mission_id'] + '/sample_results.csv').decode('utf-8'))))
                    self.assertEqual(rows[0]['terminal_reason'], result['reason'])
                    self.assertEqual(json.loads(rows[0]['cleanup_result']), cleanup)
                server._trigger_status_cb(context_msg)
                server._sampling_result_cb(result_msg)
                self.assertEqual(len(server.data_manager.list_missions()), 1)
                server._sampling_context = {'attempt_id': 'new-owner', 'source': 'manual'}
                server._sampling_result_cb(result_msg)
                self.assertEqual(server._sampling_context['attempt_id'], 'new-owner')

    def test_trigger_cleanup_result_wins_over_early_pump_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            server = WebConfigServer(standalone=True)
            server.data_manager = MissionDataManager(directory)
            server.sample_storage = SampleRecordingStorage(directory)
            server._sampling_context = {'attempt_id': 'b' * 32, 'source': 'manual'}
            server._start_data_recording_if_needed(source='trigger')
            server._start_sample_window_if_needed()
            window = server.current_sample_window
            self.assertFalse(server._finish_owned_recording(dict(
                status='finished', terminal_reason='completed', sampling_context=server._sampling_context)))
            self.assertEqual(window['state'], 'open')
            server._sampling_result_cb(SimpleNamespace(data=json.dumps(dict(
                source='manual', attempt_id='b' * 32, outcome='failed',
                reason='cleanup_failed', cleanup_failed=True))))
            self.assertEqual(window['state'], 'closed')
            self.assertEqual(window['outcome'], 'failed')
            self.assertTrue(window['cleanup_failed'])

    def test_failed_attempt_survives_close_reload_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            server = WebConfigServer(standalone=True)
            server.data_manager = MissionDataManager(directory)
            server.sample_storage = SampleRecordingStorage(directory)
            server._sampling_context = {'attempt_id': 'a' * 32, 'source': 'web'}
            server._start_data_recording_if_needed(source='web')
            server._start_sample_window_if_needed()
            window = server.current_sample_window
            mission_id = server.data_manager.current_mission_data['mission_id']
            terminal = dict(status='finished', terminal_reason='spectrometer_stale',
                            preflight_phase='settling', cleanup_failed=True,
                            spectrometer_age_s=4.5, owner_age_s=0.3,
                            sampling_context={'attempt_id': 'old'})
            self.assertFalse(server._finish_owned_recording(terminal))
            terminal['sampling_context'] = {'attempt_id': 'a' * 32}
            self.assertTrue(server._finish_owned_recording(terminal))
            self.assertEqual(window['state'], 'closed')
            self.assertEqual(window['outcome'], 'failed')
            self.assertEqual(window['terminal_reason'], 'spectrometer_stale')
            data = server.data_manager.get_mission(mission_id)
            self.assertEqual(data['sample_windows'][0]['outcome'], 'failed')
            archive = server._build_mission_archive(data)
            try:
                with zipfile.ZipFile(archive) as zipped:
                    rows = list(csv.DictReader(io.StringIO(zipped.read(
                        'mission_' + mission_id + '/sample_results.csv').decode('utf-8'))))
                    self.assertEqual(rows[0]['outcome'], 'failed')
                    self.assertEqual(rows[0]['attempt_id'], 'a' * 32)
                    self.assertEqual(rows[0]['preflight_phase'], 'settling')
            finally:
                archive.close()


if __name__ == '__main__':
    unittest.main()
