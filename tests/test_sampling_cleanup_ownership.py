"""Old cleanup RPCs cannot act on a newer owner (real engine, fake serial)."""
import json
import threading
import unittest
from unittest.mock import patch

import test_fcu_sampling_result as fcu_results
from test_hardware_runtime_sync import _load_script


class SamplingCleanupOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.case = fcu_results.FCUSamplingResultTests()
        self.case.setUp()
        self.node = self.case.node
        self.module, _, _ = _load_script('cleanup_ownership_pump', 'scripts/pump_control_node.py')
        with patch.object(self.module, 'InjectionPumpWorker', return_value=None):
            self.pump = self.module.PumpControlNode()
        self.sent = []
        self.pump.send_command = lambda command: self.sent.append(command) or True
        self.engine = self.pump.automation_engine
        self.engine.send_command = self.pump.send_command
        self.engine.on_step_command = lambda step: True
        self.waiting = threading.Event()
        self.pause = threading.Event()

        def wait_step(step):
            self.waiting.set()
            while self.engine._running.is_set():
                self.pause.wait(0.005)
            return False

        self.engine.on_step_wait = wait_step
        self.addCleanup(self.pump._auto_stop_callback, None)
        self.node.current_sampling_context = {
            'source': 'fcu', 'sample_id': 42, 'attempt_id': 'A', 'started': True,
        }
        self.node.is_sampling = True
        self.pump.sampling_context = dict(self.node.current_sampling_context)
        self.assertFalse(self.pump._automation_is_active())
        self.node._stop_injection_session = lambda source, reason='': self.pump._execute_control_action(
            'injection_off', {'source': source, 'reason': reason})[0]

    def start_web_b(self):
        self.assertTrue(self.pump._execute_control_action('injection_on', {'speed': 30})[0])
        self.assertTrue(self.pump._execute_control_action('automation_start', {
            'source': 'web', 'attempt_id': 'B', 'pid_mode': False,
            'steps': [{'name': 'B'}],
        })[0])
        self.assertTrue(self.waiting.wait(1.0))
        self.assertTrue(self.engine.is_running())
        self.assertTrue(self.pump.inject_pump_enabled)

    def exercise_delay(self, order):
        def delayed_request(action, payload=None):
            if order == 'request':
                self.start_web_b()
            if action == 'automation_cleanup':
                ok, _, result = self.pump._execute_control_action(action, payload)
            else:
                response = self.pump._auto_stop_callback(None)
                ok, result = response.success, {}
            if order == 'response':
                self.start_web_b()
            return ok, result

        # Legacy and scoped transports use the same deterministic RPC ordering.
        self.node._call_automation_service = lambda action: delayed_request(action)[0]
        self.node._call_control_transaction = delayed_request
        self.node._handle_completion(False, 'A_step_failed')
        self.assertTrue(self.engine.is_running(), 'old A cleanup cancelled B automation')
        self.assertTrue(self.pump.inject_pump_enabled, 'old A cleanup disabled B injection')
        self.assertEqual(self.pump.sampling_context['attempt_id'], 'B')

    def test_delayed_old_cleanup_request_cannot_stop_web_b(self):
        self.exercise_delay('request')
        self.assertEqual(self.case.modes, [], 'superseded A must not force HOLD')
        self.assertIsNone(self.pump._controller_fault)

    def test_delayed_cleanup_response_cannot_send_a_second_off_to_web_b(self):
        self.exercise_delay('response')

    def test_explicit_qgc_stop_without_trigger_context_still_stops_web_b(self):
        self.start_web_b()
        self.node.is_sampling = False
        self.node.current_sampling_context = None
        self.node._call_automation_service = lambda action: self.pump._auto_stop_callback(None).success
        self.assertTrue(self.node.handle_mavlink_command(31011))
        self.assertFalse(self.engine.is_running())
        self.assertFalse(self.pump.inject_pump_enabled)

    def test_same_trigger_start_is_busy_until_old_cleanup_returns(self):
        accepted = []
        def cleanup(action, payload=None):
            accepted.append(self.node._do_manual_sample())
            return True, {'cleanup': 'stopped', 'attempt_id': 'A'}
        self.node._call_control_transaction = cleanup
        self.node._call_automation_service = lambda action: cleanup(action)[0]
        self.node._handle_completion(False, 'A_step_failed')
        self.assertEqual(accepted, [False])

    def test_prestart_injection_claim_is_cleaned_when_start_is_rejected(self):
        node = self.case.trigger_module.MAVLinkTriggerNode()
        node.set_mode = lambda mode: True
        node._load_config = lambda: {'steps': []}
        node._build_steps_payload = lambda *args: {'steps': []}
        requests = []
        def transaction(action, payload=None):
            requests.append((action, dict(payload or {})))
            ok, _, result = self.pump._execute_control_action(action, payload or {})
            return ok, result
        node._call_control_transaction = transaction
        node._call_control_command = lambda action, payload=None: transaction(action, payload)[0]
        self.assertFalse(node._do_fcu_sample(42))
        self.assertFalse(self.pump.inject_pump_enabled)
        self.assertFalse(self.pump._automation_is_active())
        self.assertTrue(self.pump.sampling_context.get('attempt_id'))
        injection_owner = next(payload['attempt_id'] for action, payload in requests if action == 'injection_on')
        self.assertNotEqual(injection_owner, 'A')
        self.assertEqual(self.pump.sampling_context['attempt_id'], injection_owner)
        self.assertEqual([action for action, _ in requests], ['injection_on', 'automation_start', 'automation_cleanup'])

    def test_matching_cleanup_failure_latches_fault_and_rejects_new_owner(self):
        self.pump.send_command = lambda command: False
        self.engine.send_command = self.pump.send_command
        ok, _, result = self.pump._execute_control_action('automation_cleanup', {'attempt_id': 'A'})
        self.assertFalse(ok)
        self.assertEqual(result['cleanup'], 'failed')
        self.assertEqual(self.pump._controller_fault, 'cleanup_failed')
        self.assertFalse(self.pump._execute_control_action('automation_start', {
            'source': 'web', 'attempt_id': 'B', 'steps': [{'name': 'B'}],
        })[0])
        keepalive_calls = []
        with patch.object(self.pump, 'connect', return_value=True), \
                patch.object(self.pump, 'disconnect'), \
                patch.object(self.module.rospy, 'is_shutdown', side_effect=[False, True]), \
                patch.object(self.pump, 'send_command', side_effect=lambda command: keepalive_calls.append(command) or False):
            self.pump.run()
        self.assertFalse(any('WATCHDOG:KEEPALIVE' in command for command in keepalive_calls))

    def test_superseded_cleanup_never_sends_stop_or_latches_fault(self):
        self.pump.sampling_context = {'attempt_id': 'B', 'source': 'web'}
        self.pump.send_command = lambda command: self.fail('superseded cleanup sent a serial command')
        ok, _, result = self.pump._execute_control_action('automation_cleanup', {'attempt_id': 'A'})
        self.assertFalse(ok)
        self.assertEqual(result['cleanup'], 'superseded')
        self.assertIsNone(self.pump._controller_fault)
        self.pump.send_command = lambda command: True

    def test_existing_owner_fault_cannot_be_reported_as_successful_cleanup(self):
        self.pump._controller_fault = 'owner_lost'
        ok, _, result = self.pump._execute_control_action('automation_cleanup', {'attempt_id': 'A'})
        self.assertFalse(ok)
        self.assertEqual(result['cleanup'], 'failed')
        self.assertEqual(self.pump._controller_fault, 'owner_lost')

    def test_owner_loss_stops_injection_even_without_running_engine(self):
        self.assertTrue(self.pump._execute_control_action('injection_on', {
            'source': 'survey', 'attempt_id': 'A', 'speed': 30,
        })[0])
        self.pump._owner_last_seen = 10.0
        self.assertFalse(self.engine.is_running())
        with patch.object(self.module.time, 'monotonic', return_value=20.0):
            self.pump._check_sampling_owner()
        self.assertFalse(self.pump.inject_pump_enabled)
        self.assertEqual(self.pump._controller_fault, 'owner_lost')

    def test_survey_gap_continues_owner_heartbeats_without_running_sample(self):
        self.node.current_sampling_context = {'source': 'survey', 'attempt_id': 'A'}
        self.node._survey_active = True
        self.node.is_sampling = False
        self.node._init_services = lambda: None
        self.node.wait_for_mavros = lambda: True
        with patch.object(self.case.trigger_module.rospy, 'is_shutdown', side_effect=[False, True]):
            self.node.run()
        self.assertEqual([json.loads(message.data) for message in self.node.owner_pub.messages], [{'attempt_id': 'A'}])

    def test_simulated_survey_gap_keeps_prestart_injection_owner(self):
        self.node.current_sampling_context = None
        self.node._survey_owner_attempt_id = 'survey-lease'
        self.node._survey_active = True
        self.node.is_sampling = False
        self.node._init_services = lambda: None
        self.node.wait_for_mavros = lambda: True
        with patch.object(self.case.trigger_module.rospy, 'is_shutdown', side_effect=[False, True]):
            self.node.run()
        self.assertEqual([json.loads(message.data) for message in self.node.owner_pub.messages], [{'attempt_id': 'survey-lease'}])


if __name__ == '__main__':
    unittest.main()
