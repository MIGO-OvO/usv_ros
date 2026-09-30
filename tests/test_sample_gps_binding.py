"""Acquisition-coordinate contract, offline ROS and storage integration."""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from gps_fixtures import gps_message, gps_position
from test_mavlink_command_compat import _load_script
from scripts.lib.sample_recording.record import bind_context, freeze_position, sample_record
from scripts.lib.sample_recording.storage import SampleRecordingStorage


class SampleGPSBindingTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('gps_binding_trigger', 'scripts/mavlink_trigger_node.py')
        self.node = self.module.MAVLinkTriggerNode()
        self.node._latest_global_position = gps_position()
        self.node._load_config = lambda: {'sampling_sequence': {'steps': [], 'loop_count': 1}}
        self.node._start_injection_session = Mock(return_value=True)
        self.node._call_automation_service = Mock(return_value=True)
        self.node._cleanup_sampling_attempt = Mock(return_value=(True, False))
        self.node._request_fcu_hold = Mock(return_value=True)
        self.node.set_mode = Mock(return_value=True)

    def test_manual_freezes_gps_before_first_hardware_call(self):
        def move(*args):
            context = self.node.current_sampling_context
            self.assertEqual(context['gps_snapshot']['lat'], 30.0)
            self.node._latest_global_position = gps_position(31.0, 121.0)
            return True
        self.node._start_injection_session.side_effect = move
        self.assertTrue(self.node._do_manual_sample())
        context = self.node.current_sampling_context
        self.node._spectrometer_voltage_cb(self.message({'voltage': 1.23, 'valid': True}))
        self.node._handle_completion(True, 'finished')
        record = json.loads(self.node.sample_record_pub.messages[-1].data)
        self.assertEqual(record['sample_id'], context['record_id'])
        self.assertEqual((record['latitude'], record['longitude']), (30.0, 120.0))
        self.assertEqual(record['spectrometer']['voltage'], 1.23)
        self.assertGreaterEqual(record['timestamp_end'], record['timestamp_start'])
        self.assertLess(record['position_age_s'], 2.0)

    @staticmethod
    def message(payload):
        return type('Message', (), {'data': json.dumps(payload)})()

    def test_stale_gps_rejects_every_real_entry_without_hardware(self):
        for entry in ('manual', 'fcu', 'lab_real', 'survey'):
            with self.subTest(entry=entry):
                self.node._latest_global_position = gps_position(age=2.1)
                if entry == 'manual':
                    self.assertFalse(self.node._do_manual_sample())
                elif entry == 'fcu':
                    self.node.default_on_fail = 'SKIP'
                    self.assertFalse(self.node._do_fcu_sample(42))
                    result = json.loads(self.node.sampling_result_pub.messages[-1].data)
                    self.assertEqual(result['outcome'], 'failed')
                elif entry == 'lab_real':
                    self.node._run_lab_sampling(1, 20, 100, {'data_source': 'real'})
                else:
                    self.assertFalse(self.node._start_survey())
                self.node._start_injection_session.assert_not_called()
                self.node._call_automation_service.assert_not_called()
                self.assertFalse(self.node.is_sampling)

    def test_fix_loss_immediately_invalidates_cached_good_fix(self):
        self.node._global_position_cb(gps_message(status=-1))
        self.assertFalse(self.node._do_manual_sample())
        self.node._start_injection_session.assert_not_called()

    def test_invalid_positions_and_missing_receive_time_are_rejected(self):
        invalid = [dict(lat=float('nan')), dict(lon=181), dict(lat=-91),
                   dict(fix_status=-1), dict(received_monotonic=None),
                   dict(received_monotonic=time.monotonic() - 3),
                   dict(received_monotonic=time.monotonic() + 5)]
        for changes in invalid:
            with self.subTest(changes=changes):
                p = gps_position()
                p.update(changes)
                with self.assertRaises(ValueError):
                    freeze_position(p)

    def test_no_fix_zero_age_parameter_cannot_disable_real_gate(self):
        self.node.sampling_max_position_age_s = 0
        self.node._latest_global_position = gps_position(age=3)
        self.assertFalse(self.node._do_manual_sample())

    def test_survey_next_sample_has_new_id_and_new_start_position(self):
        self.node._survey_active = True
        config = self.node._load_config()
        self.assertEqual(self.node._start_survey_sample_once(config), 'started')
        first = dict(self.node.current_sampling_context)
        self.node._latest_global_position = gps_position(31.0, 121.0)
        self.node._handle_completion(True, 'finished')
        self.assertEqual(self.node._last_survey_sample_position['lat'], 30.0)
        self.assertEqual(self.node._start_survey_sample_once(config), 'started')
        second = self.node.current_sampling_context
        self.assertNotEqual(first['record_id'], second['record_id'])
        self.assertEqual(second['gps_snapshot']['lat'], 31.0)

    def test_real_and_lab_emit_same_record_keys_and_preserve_legacy_id(self):
        self.assertTrue(self.node._do_fcu_sample(42))
        self.node._handle_completion(True, 'finished')
        real = json.loads(self.node.sample_record_pub.messages[-1].data)
        result = json.loads(self.node.sampling_result_pub.messages[-1].data)
        self.assertEqual(result['sample_id'], 42)
        self.assertEqual(result['sample_record']['sample_id'], real['sample_id'])
        # This fake ROS String supports no positional argument; use the lab stub.
        lab_module = __import__('test_lab_sim_sampling_state')._load_script('gps_lab', 'scripts/mavlink_trigger_node.py')
        lab = lab_module.MAVLinkTriggerNode()
        lab._run_lab_sampling(3, 25.0, 110.0, {'data_source': 'simulated', 'sim': {'sample_dwell_s': 0}})
        simulated = json.loads(lab.sample_record_pub.messages[-1].data)
        self.assertEqual(set(real), set(simulated))
        self.assertTrue(simulated['simulated'])
        self.assertEqual(simulated['position_source'], 'lab_sim')
        self.assertEqual(simulated['latitude'], 25.0)
        self.assertIsNone(simulated['gps_timestamp'])
        self.assertFalse(real['simulated'])

    def test_storage_closing_fix_does_not_replace_start_and_ids_do_not_collide(self):
        context = {'source': 'fcu', 'sample_id': 42, 'mavlink_sample_id': 42, 'waypoint_seq': 3}
        bind_context(context, gps_position())
        with tempfile.TemporaryDirectory() as directory:
            store = SampleRecordingStorage(directory)
            mission = {'mission_id': 'mission-A'}
            first = store.start_window(mission, context, {'lat': 99, 'lng': 99})
            store.close_window(mission, first, {'lat': 31, 'lng': 121})
            self.assertEqual(first['latitude'], 30)
            self.assertEqual(first['gps_start']['lat'], 30)
            self.assertEqual(first['gps_end']['lat'], 31)
            self.assertEqual(first['mission_id'], 'mission-A')
            self.assertEqual(first['mavlink_sample_id'], 42)
            self.assertEqual(first['waypoint_id'], 3)
            with patch('time.time', return_value=1000):
                a = store.start_window(mission)
                b = store.start_window(mission)
            self.assertNotEqual(a['sample_id'], b['sample_id'])
            store.close()

    def test_freeze_is_deep_copy_and_idempotent(self):
        position = gps_position()
        context = {}
        bind_context(context, position)
        record_id = context['record_id']
        position['lat'] = 50
        bind_context(context, gps_position(40))
        self.assertEqual(context['gps_snapshot']['lat'], 30)
        self.assertEqual(context['record_id'], record_id)

    def test_age_boundary_and_wall_clock_change(self):
        p = gps_position()
        p['received_monotonic'] = 100
        with patch('scripts.lib.sample_recording.record.time.monotonic', return_value=102.0), \
                patch('scripts.lib.sample_recording.record.time.time', return_value=999999.0):
            self.assertEqual(freeze_position(p)['position_age_s'], 2.0)
        with patch('scripts.lib.sample_recording.record.time.monotonic', return_value=102.001):
            with self.assertRaises(ValueError):
                freeze_position(p)

    def test_fresh_local_receive_survives_bad_wall_clock_offset(self):
        # FCU/MAVROS header.stamp 5s behind the Jetson wall clock must not
        # reject a locally fresh fix (field observation: QGC OK, ROS stale).
        p = gps_position()
        p['gps_timestamp'] = time.time() - 5.0
        p['header_clock_offset_s'] = 5.0
        frozen = freeze_position(p)
        self.assertGreaterEqual(frozen['position_age_s'], 0.0)
        self.assertLess(frozen['position_age_s'], 2.0)
        self.assertAlmostEqual(frozen['gps_timestamp'], p['gps_timestamp'])

    def test_future_header_stamp_is_diagnostic_not_a_gate(self):
        p = gps_position()
        p['gps_timestamp'] = time.time() + 30.0
        p['header_clock_offset_s'] = -30.0
        self.assertTrue(freeze_position(p)['position_age_s'] >= 0.0)

    def test_lab_real_uses_hardware_fix_not_virtual_waypoint(self):
        self.node._run_lab_sampling(7, 20, 100, {'data_source': 'real'})
        self.assertEqual(self.node.current_sampling_context['gps_snapshot']['lat'], 30)
        self.assertFalse(self.node.current_sampling_context['simulated'])

    def test_waypoint_rejects_after_hold_if_fix_has_expired(self):
        self.node._get_waypoint_sampling_config = lambda *args: {
            'enabled': True, 'loop_count': 1, 'retry_count': 0, 'on_fail': 'SKIP', 'hold_before_sampling_s': 0}
        self.node._wait_until_stable = lambda *args: (True, '')
        self.node._latest_global_position = gps_position(age=3)
        self.assertFalse(self.node._start_sampling_sequence(3))
        self.node._start_injection_session.assert_not_called()
        self.node._call_automation_service.assert_not_called()

    def test_survey_false_setting_does_not_bypass_real_gps_gate(self):
        self.node._survey_active = True
        self.node._latest_global_position = None
        self.assertEqual(self.node._start_survey_sample_once({'survey_sampling': {'survey_require_gps': False}}), 'skipped')
        self.node._call_automation_service.assert_not_called()

    def test_duplicate_emission_and_window_open_are_idempotent(self):
        context = {'source': 'manual'}
        bind_context(context, gps_position())
        self.node._emit_sample_record(dict(context), 'succeeded', '')
        self.node._emit_sample_record(dict(context), 'succeeded', '')
        self.assertEqual(len(self.node.sample_record_pub.messages), 1)
        with tempfile.TemporaryDirectory() as directory:
            store = SampleRecordingStorage(directory)
            mission = {'mission_id': 'M'}
            first = store.start_window(mission, context)
            self.assertIs(store.start_window(mission, context), first)
            self.assertEqual(len(mission['sample_windows']), 1)
            store.close_window(mission, first)
            with self.assertRaises(ValueError):
                store.start_window(mission, context)
            store.close()


if __name__ == '__main__':
    unittest.main()
