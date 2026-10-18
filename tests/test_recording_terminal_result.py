import csv
import io
import json
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

from scripts.lib.sample_recording.storage import SampleRecordingStorage
from scripts.web_config_server import FLASK_AVAILABLE, MissionDataManager, WebConfigServer


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask is not installed')
class RecordingTerminalTests(unittest.TestCase):
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
