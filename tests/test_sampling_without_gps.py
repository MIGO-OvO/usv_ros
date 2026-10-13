"""Sampling starts without GPS across the real command and transaction paths."""
import json
import types
import unittest
from unittest.mock import Mock

from gps_fixtures import gps_position
from test_mavlink_command_compat import _load_script
from scripts.lib.sample_recording.record import bind_context, bind_web_context, sample_record


class SamplingWithoutGPSTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('sampling_without_gps', 'scripts/mavlink_trigger_node.py')

    def make_node(self, position=None):
        node = self.module.MAVLinkTriggerNode()
        node._latest_global_position = position
        node._load_config = lambda: {
            'sampling_sequence': {'steps': [], 'loop_count': 1},
            'automation_policy': {'require_gps': True},
            'survey_sampling': {'survey_require_gps': True, 'survey_min_distance_m': 5},
        }
        node._call_control_transaction = Mock(return_value=(True, {}))
        node._cleanup_sampling_attempt = Mock(return_value=(True, False))
        node._request_fcu_hold = Mock(return_value=True)
        node.set_mode = Mock(return_value=True)
        node._wait_until_stable = Mock(return_value=(True, 'stable'))
        return node

    def test_all_real_entries_start_with_missing_lost_and_stale_gps(self):
        positions = (None, dict(gps_position(), fix_status=-1), gps_position(age=3))
        for position in positions:
            for entry in ('manual', 'fcu', 'waypoint', 'survey', 'lab_real'):
                with self.subTest(position=position, entry=entry):
                    node = self.make_node(position)
                    if entry == 'manual':
                        node._mavlink_cmd_rx_cb(types.SimpleNamespace(
                            data=[31010.0, 0.0, 0.0, 1.0, 191.0, 255.0, 190.0]))
                        self.assertEqual(node.cmd_ack_pub.messages[-1].data[1], 0.0)
                    elif entry == 'fcu':
                        self.assertTrue(node._do_fcu_sample(42))
                    elif entry == 'waypoint':
                        self.assertTrue(node._start_sampling_sequence(7))
                    elif entry == 'survey':
                        node._survey_active = True
                        self.assertEqual(node._start_survey_sample_once(node._load_config()), 'started')
                    else:
                        node._run_lab_sampling(7, 25, 110, {'data_source': 'real', 'allow_no_gps': False})
                    node._call_control_transaction.assert_called_once()
                    record = sample_record(node.current_sampling_context)
                    self.assertIsNone(record['latitude'])
                    self.assertIsNone(record['longitude'])
                    self.assertFalse(record['simulated'])

    def test_shared_binding_records_missing_gps_for_each_real_source(self):
        for source in ('manual', 'web', 'fcu', 'waypoint', 'survey', 'lab_real'):
            with self.subTest(source=source):
                context = bind_context({'source': source}, None)
                record = sample_record(context)
                self.assertEqual(record['position_source'], source + '_no_gps')
                self.assertIsNone(record['gps_timestamp'])
                self.assertFalse(record['simulated'])

    def test_web_legacy_required_flag_cannot_reject_missing_gps(self):
        context = bind_web_context({'source': 'web'}, None, require_gps=True)
        self.assertIsNone(sample_record(context)['latitude'])

    def test_survey_missing_gps_ignores_distance_and_clears_old_anchor(self):
        node = self.make_node()
        node._survey_active = True
        node._last_survey_sample_position = gps_position()
        self.assertEqual(node._start_survey_sample_once(node._load_config()), 'started')
        self.assertIsNone(node._last_survey_sample_position)

    def test_unlocated_fcu_completion_keeps_matching_success_and_record(self):
        node = self.make_node()
        self.assertTrue(node._do_fcu_sample(42))
        node._handle_completion(True, 'finished')
        result = json.loads(node.sampling_result_pub.messages[-1].data)
        self.assertEqual((result['sample_id'], result['outcome']), (42, 'succeeded'))
        self.assertIsNone(result['sample_record']['latitude'])


if __name__ == '__main__':
    unittest.main()
