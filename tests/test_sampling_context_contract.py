import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.web_config_server import FLASK_AVAILABLE, WebConfigServer, String


def msg(value):
    result = String()
    result.data = value
    return result


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask unavailable')
class SamplingContextContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.tmp.name}):
            self.server = WebConfigServer(standalone=True)
        self.addCleanup(self.server.sample_storage.close)
        self.server.current_waypoint_seq = 9

    def test_fcu_context_reaches_window_without_automation_topic_ordering(self):
        context = {'source': 'fcu', 'sample_id': 42, 'waypoint_seq': 3, 'attempt_id': 'attempt-42'}
        self.server._trigger_status_cb(msg('sampling_context:' + json.dumps(context)))
        self.server._trigger_status_cb(msg('sampling_started'))
        window = self.server.current_sample_window
        self.server._status_cb(msg('automation: finished'))
        self.assertTrue(self.server.automation_running)
        self.assertEqual(window['mavlink_sample_id'], 42)
        self.assertEqual(window['waypoint_seq'], 3)
        self.assertEqual(window['attempt_id'], 'attempt-42')
        self.assertEqual(window['source'], 'fcu')

    def test_manual_sample_with_existing_waypoint_is_not_fcu(self):
        self.server._trigger_status_cb(msg('sampling_context:' + json.dumps({'source': 'manual', 'waypoint_seq': 9})))
        self.server._trigger_status_cb(msg('sampling_started'))
        window = self.server.current_sample_window
        self.assertEqual(window['source'], 'manual')
        self.assertEqual(window['mode'], 'manual')
        self.assertIsNone(window['mavlink_sample_id'])
        self.assertIsNone(window['waypoint_seq'])

    def test_detector_full_stress_frame_is_excluded_from_real_archive(self):
        self.server._trigger_status_cb(msg('sampling_started'))
        self.server._spectrometer_raw_cb(msg(json.dumps({'voltage': 1.2, 'status': 0x11, 'valid': True})))
        self.assertEqual(self.server.current_sample_window['spectrometer']['frame_count'], 0)

    def test_legacy_web_emergency_stop_uses_cancelling_transaction(self):
        self.server.standalone = False
        with patch.object(self.server, '_call_control_command', return_value=(False, 'halt failed', {})) as control:
            response = self.server.app.test_client().post('/api/motor/stop').get_json()
        control.assert_called_once_with('manual_stop_all', {})
        self.assertFalse(response['success'])

    def test_rejected_duplicate_start_preserves_existing_owner(self):
        self.server.standalone = False
        self.server.automation_running = True
        self.server._web_attempt_id = 'existing-A'
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'rejected-B'}), \
                patch.object(self.server, '_call_control_command', return_value=(False, 'busy', {})):
            self.server.app.test_client().post('/api/mission/start')
        self.assertEqual(self.server._web_attempt_id, 'existing-A')

    def test_every_emergency_stop_cancels_a_pending_web_start(self):
        for route in ('/api/motor/stop', '/api/manual/stop-all', '/api/motor/command'):
            with self.subTest(route=route):
                self.server._stop_data_recording_if_active()
                self.server.automation_running = False
                self.server.automation_paused = False
                self.server.standalone = False
                self.server._web_attempt_id = None
                calls = []

                def control(action, payload=None, source='web'):
                    calls.append(action)
                    if action == 'automation_start':
                        response = self.server.app.test_client().post(route, json={'command': 'STOPALL'})
                        self.assertTrue(response.get_json()['success'])
                    return True, 'accepted', {}

                with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'late-start'}), \
                        patch.object(self.server, '_call_control_command', side_effect=control):
                    result = self.server.app.test_client().post('/api/mission/start').get_json()
                self.assertFalse(result['success'])
                self.assertIsNone(self.server._web_attempt_id)
                self.assertEqual(calls.count('manual_stop_all'), 2)

    def test_old_or_unscoped_terminal_cannot_close_new_owned_window(self):
        self.server._sampling_context = {'source': 'web', 'attempt_id': 'new-B'}
        self.server._start_data_recording_if_needed(source='web')
        self.server._start_sample_window_if_needed()
        self.server.automation_running = True
        window = self.server.current_sample_window
        for context in ({'attempt_id': 'old-A'}, {}):
            self.server._automation_status_cb(msg(json.dumps({
                'status': 'finished', 'running': False, 'sampling_context': context,
            })))
            self.assertIs(self.server.current_sample_window, window)
            self.assertTrue(self.server.automation_running)
        self.server._trigger_status_cb(msg('sampling_stopped'))
        self.assertIs(self.server.current_sample_window, window)
        self.server._automation_status_cb(msg(json.dumps({
            'status': 'finished', 'running': False, 'sampling_context': {'attempt_id': 'new-B'},
        })))
        self.assertIsNone(self.server.current_sample_window)

    def test_early_matching_terminal_is_consumed_after_window_opens(self):
        context = {'source': 'fcu', 'sample_id': 42, 'attempt_id': 'fast-A'}
        self.server._automation_status_cb(msg(json.dumps({
            'status': 'finished', 'running': False, 'sampling_context': context,
        })))
        self.server._trigger_status_cb(msg('sampling_context:' + json.dumps(context)))
        self.server._trigger_status_cb(msg('sampling_started'))
        self.assertIsNone(self.server.current_sample_window)
        self.assertFalse(self.server.automation_running)

    def test_rejected_start_rolls_back_its_window_not_existing_survey_file(self):
        self.server.standalone = False
        self.server._start_data_recording_if_needed(source='survey')
        existing = self.server.data_manager.current_mission_file
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'rejected-B'}), \
                patch.object(self.server, '_call_control_command', return_value=(False, 'busy', {})):
            response = self.server.app.test_client().post('/api/mission/start').get_json()
        self.assertFalse(response['success'])
        self.assertIsNone(self.server.current_sample_window)
        self.assertEqual(self.server.data_manager.current_mission_file, existing)
        self.assertEqual(self.server.data_recording_source, 'survey')

    def test_terminal_cleanup_cannot_interleave_with_new_window_start(self):
        self.server.standalone = False
        self.server._sampling_context = {'source': 'web', 'attempt_id': 'old-A'}
        self.server._start_data_recording_if_needed(source='web')
        self.server._start_sample_window_if_needed()
        self.server.automation_running = True
        closed, continue_cleanup, new_control = threading.Event(), threading.Event(), threading.Event()
        original_close = self.server._close_sample_window_if_open
        results = []

        def close_with_barrier():
            original_close()
            if not closed.is_set():
                closed.set()
                continue_cleanup.wait(3)

        def control(*args, **kwargs):
            new_control.set()
            return True, 'accepted', {}

        with patch.object(self.server, '_close_sample_window_if_open', side_effect=close_with_barrier), \
                patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'new-B'}), \
                patch.object(self.server, '_call_control_command', side_effect=control):
            finish = threading.Thread(target=lambda: self.server._automation_status_cb(msg(json.dumps({
                'status': 'finished', 'running': False, 'sampling_context': {'attempt_id': 'old-A'},
            }))))
            start = threading.Thread(target=lambda: results.append(
                self.server.app.test_client().post('/api/mission/start').get_json()))
            try:
                finish.start()
                self.assertTrue(closed.wait(2))
                start.start()
                self.assertFalse(new_control.wait(0.5))
            finally:
                continue_cleanup.set()
                finish.join(3)
                if start.ident is not None:
                    start.join(3)
        self.assertTrue(results[0]['success'])
        self.assertEqual(self.server.current_sample_window['attempt_id'], 'new-B')
        self.assertEqual(self.server._sampling_context['attempt_id'], 'new-B')


if __name__ == '__main__':
    unittest.main()
