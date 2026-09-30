import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from scripts.web_config_server import FLASK_AVAILABLE, WebConfigServer, String
from gps_fixtures import gps_position
from scripts.lib.sample_recording.record import bind_context


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
        self.server._latest_real_gps = gps_position()

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

    def test_frozen_context_survives_moving_web_position_and_real_archive(self):
        context = {'source': 'fcu', 'sample_id': 42, 'waypoint_seq': 3, 'attempt_id': 'bound-A'}
        bind_context(context, gps_position())
        self.server.current_position = {'lat': 31.0, 'lng': 121.0}
        self.server._trigger_status_cb(msg('sampling_context:' + json.dumps(context)))
        self.server._trigger_status_cb(msg('sampling_started'))
        window = self.server.current_sample_window
        self.server._spectrometer_raw_cb(msg(json.dumps({'voltage': 1.2, 'valid': True})))
        self.server._voltage_cb(msg(json.dumps({'voltage': 1.2, 'valid': True})))
        self.assertTrue(self.server._record_latest_mission_point())
        point = self.server.data_manager.current_mission_data['data_points'][-1]
        self.assertEqual(point['wgs84']['lat'], 30)
        self.assertEqual(point['sample_id'], context['record_id'])
        self.assertEqual(point['position_age_s'], context['gps_snapshot']['position_age_s'])
        self.server._automation_status_cb(msg(json.dumps({
            'status': 'finished', 'running': False, 'sampling_context': context})))
        self.assertEqual(window['latitude'], 30)
        self.assertEqual(window['gps_start']['lng'], 120)
        self.assertEqual(window['gps_end']['lng'], 121)
        self.assertEqual(window['sample_id'], context['record_id'])
        self.assertEqual(window['spectrometer']['frame_count'], 1)
        self.assertIsNotNone(window['timestamp_end'])

    def test_web_direct_start_rejects_stale_or_missing_hardware_gps(self):
        self.server.standalone = False
        for position in (None, gps_position(age=3)):
            self.server._latest_real_gps = position
            self.server.current_position = {'lat': 25, 'lng': 110, 'position_source': 'lab_sim'}
            with patch.object(self.server, '_call_control_command') as control:
                result = self.server.app.test_client().post('/api/mission/start')
            self.assertEqual(result.status_code, 409)
            control.assert_not_called()
            self.assertIsNone(self.server.current_sample_window)

    def test_web_opt_out_records_no_coordinates_even_with_display_position(self):
        self.server.standalone = False
        self.server._latest_real_gps = None
        self.server.current_position = {'lat': 25, 'lng': 110, 'position_source': 'lab_sim'}
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'indoor'}), \
                patch.object(self.server, '_call_control_command', return_value=(True, 'accepted', {})) as control:
            response = self.server.app.test_client().post('/api/mission/start', json={'require_gps': False})
        self.assertTrue(response.get_json()['success'])
        control.assert_called_once()
        window = self.server.current_sample_window
        self.assertIsNone(window['latitude'])
        self.assertIsNone(window['longitude'])
        self.assertIsNone(window['gps_start'])
        self.assertEqual(window['position_source'], 'web_no_gps')
        self.assertFalse(window['simulated'])
        self.server._latest_real_gps = gps_position()
        self.server._close_sample_window_if_open()
        self.assertIsNone(window['latitude'])

    def test_web_opt_out_rejects_non_boolean(self):
        self.server.standalone = False
        with patch.object(self.server, '_call_control_command') as control:
            for value in ('false', 0, None, {}):
                response = self.server.app.test_client().post('/api/mission/start', json={'require_gps': value})
                self.assertEqual(response.status_code, 409)
        control.assert_not_called()

    def test_gps_status_uses_hardware_and_expires_without_new_messages(self):
        client = self.server.app.test_client()
        self.assertTrue(client.get('/api/gps').get_json()['valid'])
        self.server._latest_real_gps = gps_position(age=3)
        stale = client.get('/api/gps').get_json()
        self.assertFalse(stale['valid'])
        self.assertEqual(stale['reason'], 'gps_stale')
        self.assertEqual(stale['latitude'], 30)
        self.server._latest_real_gps = dict(gps_position(), fix_status=-1)
        self.assertEqual(client.get('/api/gps').get_json()['reason'], 'gps_no_fix')
        self.server._latest_real_gps = None
        self.assertEqual(client.get('/api/gps').get_json()['reason'], 'gps_missing')

    def test_simulated_measurement_persisted_on_ordered_lifecycle_stream(self):
        context = {'source': 'lab_sim', 'waypoint_seq': 3}
        bind_context(context, {'lat': 25, 'lng': 110}, simulated=True)
        self.server._trigger_status_cb(msg('sampling_context:' + json.dumps(context)))
        self.server._trigger_status_cb(msg('sampling_started'))
        window = self.server.current_sample_window
        self.server._trigger_status_cb(msg('sampling_measurement:' + json.dumps({
            'sample_id': context['record_id'], 'spectrometer': {'voltage': 2.0},
            'water_quality': {'concentration': 1.5, 'unit': 'mg/L'}})))
        self.server._trigger_status_cb(msg('sampling_stopped'))
        self.assertEqual(window['spectrometer']['measurement']['voltage'], 2.0)
        self.assertEqual(window['water_quality']['concentration'], 1.5)
        self.assertEqual(window['latitude'], 25)
        self.assertTrue(window['simulated'])

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
                self.assertEqual(calls.count('manual_stop_all'), 1)
                self.assertEqual(calls.count('automation_cleanup'), 1)

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

    def test_immediate_owned_failure_is_not_reported_as_success(self):
        self.server.standalone = False

        def control(*args, **kwargs):
            self.server._automation_status_cb(msg(json.dumps({
                'status': 'failed', 'running': False,
                'sampling_context': {'attempt_id': 'fast-failure'},
                'last_error': 'first step failed',
            })))
            return True, 'Automation started', {}

        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'fast-failure'}), \
                patch.object(self.server, '_call_control_command', side_effect=control):
            response = self.server.app.test_client().post('/api/mission/start').get_json()
        self.assertFalse(response['success'])
        self.assertIn('first step failed', response['message'])
        self.assertIsNone(self.server._web_attempt_id)
        self.assertIsNone(self.server.current_sample_window)

    def test_old_failure_cannot_reject_new_start(self):
        self.server.standalone = False
        self.server.latest_automation_status = {
            'status': 'failed', 'running': False, 'last_error': 'old failure',
            'sampling_context': {'attempt_id': 'old'},
        }
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'new'}), \
                patch.object(self.server, '_call_control_command', return_value=(True, 'started', {})):
            response = self.server.app.test_client().post('/api/mission/start').get_json()
        self.assertTrue(response['success'])

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

    def test_cancelled_start_rolls_back_window_and_leaves_next_attempt_clean(self):
        """Stop during a pending start: the attempt must fail, roll back its
        window and never pollute the following attempt."""
        self.server.standalone = False
        started = threading.Event()
        release = threading.Event()
        calls = []

        def control(action, payload=None, source='web'):
            calls.append(action)
            if action == 'automation_start':
                started.set()
                release.wait(5)
                # Hardware accepted the start; Web must still cancel it.
                return True, 'accepted', {}
            return True, 'ok', {}

        result = {}
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'cancelled-A'}), \
                patch.object(self.server, '_call_control_command', side_effect=control):
            start = threading.Thread(
                target=lambda: result.update(
                    response=self.server.app.test_client().post('/api/mission/start').get_json()))
            try:
                start.start()
                self.assertTrue(started.wait(3))
                self.assertIsNotNone(self.server.current_sample_window)
                self.server.owner_pub = Mock()
                self.server._publish_web_owner_heartbeat()
                beat = self.server.owner_pub.publish.call_args[0][0]
                self.assertEqual(json.loads(beat.data)['attempt_id'], 'cancelled-A')
                # Operator presses Stop while the start request is still in flight.
                self.server._cancel_pending_web_start()
            finally:
                release.set()
                start.join(5)

        self.assertFalse(result['response']['success'])
        self.assertIn('cancel', result['response']['message'].lower())
        self.assertIsNone(self.server.current_sample_window)
        self.assertIsNone(self.server._web_attempt_id)
        self.assertNotIn('cancelled-A', (self.server._sampling_context or {}).get('attempt_id', ''))
        self.assertEqual(calls.count('automation_cleanup'), 1)
        self.assertNotIn('manual_stop_all', calls)

        # A fresh start after the cancelled one is not polluted by the old attempt.
        with patch.object(self.server, '_publish_steps', return_value={'attempt_id': 'fresh-B'}), \
                patch.object(self.server, '_call_control_command', return_value=(True, 'accepted', {})):
            retry = self.server.app.test_client().post('/api/mission/start', json={'require_gps': False})
        self.assertTrue(retry.get_json()['success'])
        self.assertEqual(self.server._web_attempt_id, 'fresh-B')
        self.assertEqual(self.server.current_sample_window['attempt_id'], 'fresh-B')

    def test_old_rollback_does_not_clear_new_heartbeat_owner(self):
        self.server.standalone = False
        self.server._web_attempt_id = 'new-B'
        self.server._sampling_context = {'attempt_id': 'new-B'}
        self.server._rollback_web_start({'attempt_id': 'old-A'}, None, None, {})
        self.server.owner_pub = Mock()
        self.server._publish_web_owner_heartbeat()
        self.assertEqual(json.loads(self.server.owner_pub.publish.call_args[0][0].data),
                         {'attempt_id': 'new-B'})
        self.server._cancel_pending_web_start()
        self.server.owner_pub.reset_mock()
        self.server._publish_web_owner_heartbeat()
        self.server.owner_pub.publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
