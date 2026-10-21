"""Manual sampling uses real trigger RPCs, pump admission and scoped cleanup."""
import json
import threading
import unittest
from unittest.mock import Mock

from command_fixtures import remote_command, wait_commands
import test_preflight_review_regressions as review


class ManualSamplingAdmissionTests(unittest.TestCase):
    stop, send, join = review.SurveyAndQGCChainTests.stop, review.SurveyAndQGCChainTests.send, review.SurveyAndQGCChainTests.join
    status, wait_phase = review.SurveyAndQGCChainTests.status, review.SurveyAndQGCChainTests.wait_phase

    def setUp(self):
        review.SurveyAndQGCChainTests.setUp(self)
        self.block_phase = 'homing'
        self.replies = []
        original = self.trigger_module.rospy.ServiceProxy('/usv/control_command')

        def control(**request):
            response = original(**request)
            self.replies.append((request['action'], response))
            return response

        self.control = control
        self.trigger_module.rospy.ServiceProxy = lambda *args: self.control

    def start_manual(self):
        self.trigger._mavlink_cmd_rx_cb(remote_command(self.trigger, 31010))
        wait_commands(self.trigger)

    def failed_result(self):
        events = [json.loads(msg.data) for msg in self.trigger._event_pub.messages]
        failure = next(event for event in reversed(events) if event['outcome'] == 'failed')
        records = [json.loads(msg.data) for msg in self.trigger.sample_record_pub.messages]
        results = [json.loads(msg.data) for msg in self.trigger.sampling_result_pub.messages]
        self.assertEqual(records[-1]['reason'], failure['reason'])
        self.assertEqual(results[-1]['reason'], failure['reason'])
        return failure, records[-1]

    def test_ready_first_request_enters_preparation_in_navigation_manual_mode(self):
        self.trigger.mavros_state.mode = 'MANUAL'
        self.assertFalse(self.node.manual_mode_enabled)
        self.start_manual()
        self.wait_phase('homing')
        self.assertTrue(self.trigger.is_sampling)
        self.assertTrue(self.node._automation_is_active())
        self.assertEqual(self.requests[0][0], 'automation_start')
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('PUMP:SET:60', self.commands)

    def test_pre_admission_rejections_keep_first_reason_with_empty_or_old_owner(self):
        for owner, fault, manual, invalid, expected in (
                (None, 'watchdog_tripped', False, False, 'watchdog_tripped'),
                ('old', None, True, False, 'pump manual mode'),
                ('old', None, False, True, 'configuration')):
            with self.subTest(owner=owner, expected=expected):
                self.node.sampling_context = {'attempt_id': owner}
                self.node._controller_fault = fault
                self.node.manual_mode_enabled = manual
                self.config['pump_settings']['pid_precision'] = 'bad' if invalid else 0.1
                self.start_manual()
                event, record = self.failed_result()
                self.assertIn(expected, event['reason'])
                self.assertNotIn('superseded', event['reason'])
                self.assertEqual(record['start_admission'], 'rejected')
                self.assertEqual(record['cleanup_result']['cleanup'], 'not_admitted')
                self.assertFalse(record.get('cleanup_failed', False))
                attempt = self.requests[-1][1]['attempt_id']
                self.assertIn(attempt, self.node._cancelled_attempts)
                self.assertEqual(self.node.sampling_context.get('attempt_id'), owner)
                self.assertFalse(self.commands)
                self.assertFalse(self.trigger.is_sampling)

    def test_claimed_start_rejection_is_stopped_without_overwriting_reason(self):
        self.config['sampling_sequence']['steps'] = []
        self.start_manual()
        event, record = self.failed_result()
        self.assertIn('No automation steps', event['reason'])
        self.assertEqual(record['start_admission'], 'owned')
        self.assertEqual(record['cleanup_result']['cleanup'], 'stopped')
        self.assertIn('STOPALL', self.commands)

    def test_busy_owner_is_preserved_and_rejection_does_not_become_cleanup_failure(self):
        self.node.sampling_context = {'attempt_id': 'running-owner'}
        self.node.automation_engine.is_running = Mock(return_value=True)
        self.start_manual()
        event, record = self.failed_result()
        self.assertIn('already running', event['reason'])
        self.assertEqual(record['cleanup_result']['cleanup'], 'not_admitted')
        self.assertTrue(self.node.automation_engine.is_running())
        self.assertFalse(self.commands)

    def test_survey_start_rejection_keeps_same_reason_in_record_and_result(self):
        self.trigger._survey_active = True
        self.node._controller_fault = 'watchdog_tripped'
        self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'failed')
        record = json.loads(self.trigger.sample_record_pub.messages[-1].data)
        result = json.loads(self.trigger.sampling_result_pub.messages[-1].data)
        self.assertIn('watchdog_tripped', record['reason'])
        self.assertEqual(record['reason'], result['reason'])
        self.assertEqual(record['cleanup_result']['cleanup'], 'not_admitted')
        self.assertFalse(self.trigger._survey_active)
        self.assertFalse(self.commands)

    def test_unknown_start_result_is_verified_by_attempt_and_stopped(self):
        original = self.control

        def lose_response(**request):
            response = original(**request)
            if request['action'] == 'automation_start':
                raise RuntimeError('start RPC outcome unknown')
            return response

        self.control = lose_response
        self.start_manual()
        event, record = self.failed_result()
        self.assertEqual(event['reason'], 'start RPC outcome unknown')
        self.assertEqual(record['start_admission'], 'unknown')
        self.assertEqual(record['cleanup_result']['cleanup'], 'stopped')
        self.assertEqual([action for action, _ in self.requests], ['automation_start', 'automation_cleanup'])
        self.join()
        self.node.automation_engine.start.assert_not_called()

    def test_unknown_request_not_yet_delivered_is_cancelled_before_late_start(self):
        original = self.control
        delayed = []

        def delay_request(**request):
            if request['action'] == 'automation_start':
                delayed.append(json.loads(request['payload_json']))
                raise RuntimeError('start RPC outcome unknown')
            return original(**request)

        self.control = delay_request
        self.start_manual()
        event, record = self.failed_result()
        self.assertEqual(record['start_admission'], 'unknown')
        self.assertEqual(record['cleanup_result']['cleanup'], 'not_owned')
        self.assertEqual(event['reason'], 'start RPC outcome unknown')
        self.assertFalse(self.node._execute_control_action('automation_start', delayed[0])[0])
        self.assertFalse(self.commands)

    def test_unknown_response_then_new_owner_is_not_stopped_by_old_cleanup(self):
        original = self.control
        after_takeover = []

        def lose_response(**request):
            response = original(**request)
            if request['action'] == 'automation_start':
                self.assertTrue(self.node._auto_stop_callback(None, confirm_stop=True).success)
                self.join()
                self.assertTrue(self.node._execute_control_action('injection_on', {
                    'attempt_id': 'new-owner', 'source': 'web', 'speed': 30})[0])
                after_takeover.append(len(self.commands))
                raise RuntimeError('start RPC outcome unknown')
            return response

        self.control = lose_response
        self.start_manual()
        event, record = self.failed_result()
        self.assertEqual(event['reason'], 'start RPC outcome unknown')
        self.assertEqual(record['cleanup_result']['cleanup'], 'superseded')
        self.assertEqual(record['cleanup_result']['owner_attempt_id'], 'new-owner')
        self.assertEqual(len(self.commands), after_takeover[0])
        self.assertTrue(self.node.inject_pump_enabled)

    def test_manual_start_delayed_until_after_stop_cannot_move_outputs(self):
        original = self.control
        received, stopped = threading.Event(), threading.Event()

        def delayed(**request):
            if request['action'] == 'automation_start':
                received.set()
                self.assertTrue(stopped.wait(2))
            response = original(**request)
            if request['action'] == 'manual_stop_all':
                self.assertTrue(response.success)
                stopped.set()
            return response

        self.control = delayed
        self.trigger._mavlink_cmd_rx_cb(remote_command(self.trigger, 31010))
        self.assertTrue(received.wait(2))
        self.trigger._mavlink_cmd_rx_cb(remote_command(self.trigger, 31011))
        wait_commands(self.trigger)
        self.assertFalse(self.trigger.is_sampling)
        self.assertFalse(self.node._automation_is_active())
        self.assertFalse(any('R' in cmd and cmd[0] in 'XYZA' for cmd in self.commands))
        self.assertNotIn('PUMP:SET:60', self.commands)


if __name__ == '__main__':
    unittest.main()
