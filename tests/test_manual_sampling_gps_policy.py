"""Legacy GPS policies cannot block sampling; terminal MAVLink ACKs still apply."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gps_fixtures import gps_position
from command_fixtures import remote_command, wait_commands
from test_mavlink_command_compat import _load_script
from scripts.lib.sample_recording.models import make_window


class ManualSamplingGPSPolicyTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('manual_sampling_gps_policy', 'scripts/mavlink_trigger_node.py')

    def make_node(self, policy=None, position=None):
        node = self.module.MAVLinkTriggerNode()
        config = {'sampling_sequence': {'steps': [], 'loop_count': 1}}
        if policy is not None:
            config['automation_policy'] = policy
        node._load_config = lambda: config
        node._latest_global_position = position
        node._call_control_transaction = Mock(return_value=(True, {}))
        node._cleanup_sampling_attempt = Mock(return_value=(True, False))
        node._request_fcu_hold = Mock(return_value=True)
        return node

    @staticmethod
    def send_start(node):
        node._mavlink_cmd_rx_cb(remote_command(node, 31010))
        wait_commands(node)
        ack = node.cmd_ack_pub.messages[-1].data
        assert ack[4] == node._mavlink_tcp_session
        return ack[:4]

    def test_opt_out_starts_missing_lost_and_stale_gps(self):
        for position in (None, dict(gps_position(), fix_status=-1), gps_position(age=3)):
            with self.subTest(position=position):
                node = self.make_node({'require_gps': False}, position)
                node._latest_lab_position = dict(gps_position(25, 110), position_source='lab_sim')
                self.assertEqual(self.send_start(node), [31010.0, 0.0, 255.0, 190.0])
                node._call_control_transaction.assert_called_once()
                action, payload = node._call_control_transaction.call_args.args
                self.assertEqual(action, 'automation_start')
                self.assertEqual(payload['source'], 'manual')
                self.assertFalse(payload['gps_required'])
                self.assertFalse(payload['simulated'])
                self.assertEqual(payload['gps_snapshot'], {'position_source': 'manual_no_gps'})
                self.assertTrue(payload['record_id'])
                self.assertTrue(node.is_sampling)
                self.assertIn('sampling_started', [m.data for m in node.status_pub.messages])

    def test_opt_out_still_freezes_valid_gps(self):
        node = self.make_node({'require_gps': False}, gps_position())
        self.assertEqual(self.send_start(node)[1], 0.0)
        payload = node._call_control_transaction.call_args.args[1]
        self.assertEqual(payload['gps_snapshot']['position_source'], 'gps')
        self.assertEqual(payload['gps_snapshot']['lat'], 30)
        self.assertFalse(payload['simulated'])

    def test_default_and_legacy_policy_values_accept_no_fix(self):
        for policy in (None, {}, {'require_gps': True}, {'require_gps': 'false'},
                       {'require_gps': 0}, {'require_gps': None}, False):
            with self.subTest(policy=policy):
                node = self.make_node(policy, dict(gps_position(), fix_status=-1))
                # Assert the protocol value, not the implementation's constant.
                self.assertEqual(self.send_start(node), [31010.0, 0.0, 255.0, 190.0])
                node._call_control_transaction.assert_called_once()
                self.assertTrue(node.is_sampling)
                self.assertEqual(node.current_sampling_context['gps_snapshot'],
                                 {'position_source': 'manual_no_gps'})

    def test_saved_required_policy_cannot_restore_the_gps_limit(self):
        node = self.make_node(position=dict(gps_position(), fix_status=-1))
        del node._load_config
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / 'sampling_config.json'
            with patch.object(self.module, 'CONFIG_FILE', str(config_file)):
                config_file.write_text(json.dumps({'automation_policy': {'require_gps': False}}), encoding='utf-8')
                self.assertEqual(self.send_start(node)[1], 0.0)
                node._handle_completion(True, 'finished')
                config_file.write_text(json.dumps({'automation_policy': {'require_gps': True}}), encoding='utf-8')
                self.assertEqual(self.send_start(node)[1], 0.0)
                self.assertEqual(node._call_control_transaction.call_count, 2)

    def test_unlocated_record_is_not_filled_from_later_gps_or_map_position(self):
        node = self.make_node({'require_gps': False})
        self.assertEqual(self.send_start(node)[1], 0.0)
        context = dict(node.current_sampling_context)
        node._latest_global_position = gps_position()
        node._handle_completion(True, 'finished')
        record = json.loads(node.sample_record_pub.messages[-1].data)
        window = make_window('indoor', context, gps_position(25, 110))
        for saved in (record, window):
            self.assertIsNone(saved['latitude'])
            self.assertIsNone(saved['longitude'])
            self.assertIsNone(saved['gps_timestamp'])
            self.assertEqual(saved['position_source'], 'manual_no_gps')
            self.assertFalse(saved['simulated'])
        self.assertIsNone(window['gps_start'])
        self.assertEqual(record['sample_id'], window['sample_id'])

    def test_legacy_policy_cannot_block_fcu_or_survey_sampling(self):
        for entry in ('fcu', 'survey'):
            with self.subTest(entry=entry):
                node = self.make_node({'require_gps': True}, dict(gps_position(), fix_status=-1))
                if entry == 'fcu':
                    self.assertTrue(node._do_fcu_sample(42))
                    node._request_fcu_hold.assert_not_called()
                else:
                    node._survey_active = True
                    self.assertEqual(node._start_survey_sample_once(node._load_config()), 'started')
                node._call_control_transaction.assert_called_once()
                self.assertNotIn('lat', node.current_sampling_context['gps_snapshot'])

    def test_automation_rejection_returns_terminal_failed_ack(self):
        node = self.make_node({'require_gps': False}, gps_position())
        node._call_control_transaction.return_value = (False, {})
        self.assertEqual(self.send_start(node), [31010.0, 4.0, 255.0, 190.0])
        node._cleanup_sampling_attempt.assert_called_once()
        self.assertFalse(node.is_sampling)
        self.assertIn('manual_start_rejected', [m.data for m in node.status_pub.messages])

    def test_busy_sampling_returns_temporary_rejection(self):
        node = self.make_node({'require_gps': False})
        node.is_sampling = True
        self.assertEqual(self.send_start(node), [31010.0, 1.0, 255.0, 190.0])
        node._call_control_transaction.assert_not_called()
        self.assertTrue(node.is_sampling)


if __name__ == '__main__':
    unittest.main()
