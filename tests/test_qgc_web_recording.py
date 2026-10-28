"""QGC sampling records through pump/trigger callbacks and the Web download API."""
import csv
import io
import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from command_fixtures import remote_command, wait_commands
from test_fcu_sampling_result import ForwardPublisher
import test_preflight_review_regressions as review
from scripts.web_config_server import FLASK_AVAILABLE, MissionDataManager, WebConfigServer


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask is not installed')
class QGCWebRecordingTests(unittest.TestCase):
    stop = review.SurveyAndQGCChainTests.stop
    send = review.SurveyAndQGCChainTests.send
    join = review.SurveyAndQGCChainTests.join

    def setUp(self):
        review.SurveyAndQGCChainTests.setUp(self)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        with patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.directory}):
            self.server = WebConfigServer(standalone=True)
        self.addCleanup(self.server.sample_storage.close)
        self.client = self.server.app.test_client()
        self.forward_lifecycle = False

        def automation_status(message):
            self.server._automation_status_cb(message)
            self.trigger._automation_status_cb(message)

        self.node.automation_status_pub = ForwardPublisher(automation_status)
        self.trigger.sampling_result_pub = ForwardPublisher(self.server._sampling_result_cb)
        self.trigger.status_pub = ForwardPublisher(
            lambda message: self.server._trigger_status_cb(message) if self.forward_lifecycle else None)

    def start_sampling(self, source='manual'):
        if source == 'manual':
            self.trigger._mavlink_cmd_rx_cb(remote_command(self.trigger, 31010))
            wait_commands(self.trigger)
        else:
            self.assertTrue(self.trigger._do_fcu_sample(42))
        self.join()
        self.assertTrue(self.trigger.is_sampling)
        return dict(self.trigger.current_sampling_context)

    def finish_sampling(self):
        self.trigger._handle_completion(True, 'finished')
        self.assertFalse(self.trigger.is_sampling)

    def test_qgc_status_opens_recording_and_raw_download_without_start_notice(self):
        self.trigger._latest_global_position = None
        context = self.start_sampling()
        self.assertIsNotNone(self.server.current_sample_window)
        self.assertEqual(self.server.current_sample_window['sample_id'], context['record_id'])
        self.assertEqual(self.server.current_sample_window['position_source'], 'manual_no_gps')
        for voltage in (1.2, 1.4):
            self.server._spectrometer_raw_cb(SimpleNamespace(data=json.dumps(
                {'voltage': voltage, 'valid': True})))
        self.finish_sampling()

        missions = self.client.get('/api/data/missions').get_json()['data']
        self.assertEqual(len(missions), 1)
        mission_id = missions[0]['id']
        mission = MissionDataManager(self.directory).get_mission(mission_id)
        window = mission['sample_windows'][0]
        self.assertEqual(window['state'], 'closed')
        self.assertEqual(window['outcome'], 'succeeded')
        self.assertEqual(window['attempt_id'], context['attempt_id'])
        self.assertEqual(window['spectrometer']['frame_count'], 2)
        with self.client.get('/api/data/mission/%s/archive' % mission_id) as response:
            self.assertEqual(response.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                rows = list(csv.DictReader(io.StringIO(archive.read(
                    'mission_%s/raw.csv' % mission_id).decode('utf-8'))))
        self.assertEqual([float(row['voltage']) for row in rows], [1.2, 1.4])

    def test_fcu_status_preserves_start_position_and_trigger_reuses_window(self):
        self.forward_lifecycle = True
        context = self.start_sampling('fcu')
        window = self.server.current_sample_window
        self.assertIsNotNone(window)
        self.assertEqual(window['sample_id'], context['record_id'])
        self.assertEqual(window['mavlink_sample_id'], 42)
        self.assertEqual(window['latitude'], context['gps_snapshot']['lat'])
        self.assertEqual(len(self.server.data_manager.current_mission_data['sample_windows']), 1)
        self.finish_sampling()
        self.assertEqual(len(self.client.get('/api/data/missions').get_json()['data']), 1)

    def test_late_status_and_start_cannot_reopen_completed_recording(self):
        self.start_sampling()
        running = self.node.automation_status_pub.messages[-1]
        lifecycle = list(self.trigger.status_pub.messages)
        self.finish_sampling()
        self.assertEqual(len(self.client.get('/api/data/missions').get_json()['data']), 1)
        self.server._automation_status_cb(running)
        for message in lifecycle:
            self.server._trigger_status_cb(message)
        self.assertIsNone(self.server.current_sample_window)
        self.assertFalse(self.server.automation_running)
        self.assertEqual(len(self.client.get('/api/data/missions').get_json()['data']), 1)

    def test_status_opens_window_when_context_arrived_without_start(self):
        self.node.automation_status_pub.callback = self.trigger._automation_status_cb
        self.trigger.status_pub.callback = lambda message: (
            self.server._trigger_status_cb(message) if message.data.startswith('sampling_context:') else None)
        context = self.start_sampling()
        self.assertIsNone(self.server.current_sample_window)
        self.server._automation_status_cb(self.node.automation_status_pub.messages[-1])
        self.assertEqual(self.server.current_sample_window['sample_id'], context['record_id'])
        self.finish_sampling()
        self.assertEqual(len(self.client.get('/api/data/missions').get_json()['data']), 1)

    def test_result_before_running_status_is_saved_once(self):
        self.node.automation_status_pub.callback = self.trigger._automation_status_cb
        context = self.start_sampling()
        running = self.node.automation_status_pub.messages[-1]
        self.finish_sampling()
        self.server._automation_status_cb(running)
        self.assertIsNone(self.server.current_sample_window)
        missions = self.client.get('/api/data/missions').get_json()['data']
        self.assertEqual(len(missions), 1)
        mission = self.server.data_manager.get_mission(missions[0]['id'])
        self.assertEqual(mission['sample_windows'][0]['attempt_id'], context['attempt_id'])
        self.assertEqual(mission['sample_windows'][0]['outcome'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
