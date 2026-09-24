"""Deterministic terminal-order and compatibility-entry safety regressions."""
import json
import types
import unittest
from unittest.mock import patch

import test_fcu_sampling_result as fcu_results
import test_system_safety_contract as system_safety
from test_hardware_runtime_sync import _load_script as load_runtime


class SamplingTerminalContractTests(unittest.TestCase):
    def setUp(self):
        self.case = fcu_results.FCUSamplingResultTests()
        self.case.setUp()
        self.node = self.case.node
        self.queued = []

        def capture_thread(target, kwargs=None, daemon=True):
            return types.SimpleNamespace(start=lambda: self.queued.append((target, kwargs or {})))

        patcher = patch.object(self.case.trigger_module.threading, 'Thread', side_effect=capture_thread)
        patcher.start()
        self.addCleanup(patcher.stop)

    def drain(self):
        while self.queued:
            target, kwargs = self.queued.pop(0)
            target(**kwargs)

    def pump_terminal(self, context, status='finished'):
        # Pump publishes both streams; only an installed structured subscriber
        # receives the correlated stream, just as in the real ROS wiring.
        module, _, _ = load_runtime('terminal_contract_pump', 'scripts/pump_control_node.py')
        with patch.object(module, 'InjectionPumpWorker', return_value=None):
            pump = module.PumpControlNode()
        pump.sampling_context = dict(context)
        pump.status_pub = fcu_results.ForwardPublisher(self.node._pump_status_cb)
        callback = getattr(self.node, '_automation_status_cb', None)
        pump.automation_status_pub = fcu_results.ForwardPublisher(callback or (lambda msg: None))
        pump._on_automation_status(status)

    def test_old_terminal_delivered_after_new_start_never_completes_new_attempt(self):
        self.case.trigger(42)
        old_context = dict(self.node.current_sampling_context)
        self.node._stop_sampling_sequence()
        self.case.trigger(43)
        self.pump_terminal(old_context)
        self.drain()
        self.assertTrue(self.node.is_sampling)
        self.case.assert_no_done()

    def test_terminal_before_successful_start_response_is_buffered(self):
        def start(action):
            self.pump_terminal(self.node.current_sampling_context)
            return True
        self.node._call_automation_service = start
        self.case.trigger(42)
        self.drain()
        self.assertFalse(self.node.is_sampling)
        self.assertEqual(list(self.case.bridge._pending_usv_done), [42])
        lifecycle = [msg.data for msg in self.node.status_pub.messages]
        self.assertLess(lifecycle.index('sampling_started'), lifecycle.index('sampling_stopped'))

    def test_terminal_before_rejected_start_response_cannot_release_fcu(self):
        def start(action):
            if action != 'start':
                return True
            self.pump_terminal(self.node.current_sampling_context)
            return False
        self.node._call_automation_service = start
        self.case.trigger(42)
        self.drain()
        self.case.assert_no_done()
        self.assertEqual(self.case.results()[0]['outcome'], 'failed')

    def test_operator_mode_change_cancels_without_overriding_new_mode(self):
        for mode in ('MANUAL', 'RTL'):
            with self.subTest(mode=mode):
                self.case.setUp()
                self.node = self.case.node
                self.case.trigger(42)
                self.node._state_cb(types.SimpleNamespace(connected=True, mode=mode, armed=True))
                self.drain()
                self.case.assert_no_done()
                self.assertEqual(self.case.modes, [])
                self.assertEqual(self.case.results()[0]['outcome'], 'cancelled')

    def test_mode_change_wins_over_queued_failure_even_with_skip_policy(self):
        self.node.default_on_fail = 'SKIP'
        self.case.trigger(42)
        context = self.node.current_sampling_context
        self.node._state_cb(types.SimpleNamespace(connected=True, mode='RTL', armed=True))
        self.node._handle_completion(False, 'late_failure', expected_context=context)
        self.drain()
        self.case.assert_no_done()
        self.assertEqual(self.case.modes, [])

    def test_mode_change_during_start_cannot_publish_started(self):
        def start(action):
            if action == 'start':
                self.node._state_cb(types.SimpleNamespace(connected=True, mode='MANUAL', armed=True))
            return True
        self.node._call_automation_service = start
        self.case.trigger(42)
        self.drain()
        self.case.assert_no_done()
        self.assertNotIn('sampling_started', [msg.data for msg in self.node.status_pub.messages])
        self.assertEqual(self.case.modes, [])

    def test_queued_state_cancellation_does_not_cancel_new_attempt(self):
        self.case.trigger(42)
        self.node._state_cb(types.SimpleNamespace(connected=True, mode='MANUAL', armed=True))
        self.node._stop_sampling_sequence()
        self.node._state_cb(types.SimpleNamespace(connected=True, mode='AUTO', armed=True))
        self.case.trigger(43)
        self.drain()
        self.assertTrue(self.node.is_sampling)
        self.assertEqual(self.node.current_sampling_context['sample_id'], 43)
        self.case.assert_no_done()

    def test_step_failure_with_failed_automation_stop_cannot_skip(self):
        self.node.default_on_fail = 'SKIP'
        self.case.trigger(42)
        self.node._call_control_transaction = lambda action, payload=None: (False, {
            'cleanup': 'failed', 'attempt_id': payload['attempt_id'],
        })
        self.node._handle_completion(False, 'step_failed')
        self.case.assert_no_done()
        self.assertEqual(self.case.results()[0]['outcome'], 'failed')
        self.assertIn('HOLD', self.case.modes)

    def test_successful_sequence_with_failed_injection_stop_is_failure_even_with_skip(self):
        self.node.default_on_fail = 'SKIP'
        self.case.trigger(42)
        self.node._call_control_transaction = lambda action, payload=None: (False, {
            'cleanup': 'failed', 'attempt_id': payload['attempt_id'],
        })
        self.node._handle_completion(True)
        self.case.assert_no_done()
        self.assertEqual(self.case.results()[0]['outcome'], 'failed')
        self.assertIn('HOLD', self.case.modes)

    def test_matching_survey_cancellation_stops_entire_scheduler(self):
        for status in ('stopped', 'cancelled', 'owner_lost'):
            with self.subTest(status=status):
                self.node._survey_active = True
                self.node._set_survey_sampling_flags(True)
                self.node._prepare_automation_steps({'steps': [{'name': 'sample'}]}, source='survey')
                self.node.current_sampling_context['started'] = True
                self.pump_terminal(self.node.current_sampling_context, status)
                self.drain()
                with patch.object(self.case.trigger_module.rospy, 'is_shutdown', return_value=False), \
                        patch.object(self.node, '_start_survey_sample_once', return_value='failed') as restart:
                    if not self.node.is_sampling:
                        self.node._survey_loop()
                restart.assert_not_called()
                self.assertFalse(self.node._survey_active)
                self.assertFalse(self.node.is_sampling)
                self.assertIn('survey_stopped', [msg.data for msg in self.node.status_pub.messages])

    def test_direct_sampling_stop_also_cancels_survey_scheduler(self):
        self.node._survey_active = True
        self.node._set_survey_sampling_flags(True)
        self.node._prepare_automation_steps({'steps': [{'name': 'sample'}]}, source='survey')
        self.node._stop_sampling_sequence()
        self.assertFalse(self.node._survey_active)
        self.assertFalse(self.node._survey_sample_active)

    def test_survey_stop_between_samples_cancels_pending_next_iteration(self):
        self.node._survey_active = True
        self.node._set_survey_sampling_flags(True)
        self.node._prepare_automation_steps({'steps': [{'name': 'sample'}]}, source='survey')
        self.node.current_sampling_context['started'] = True
        context = dict(self.node.current_sampling_context)
        self.pump_terminal(context, 'finished')
        self.drain()
        self.assertFalse(self.node.is_sampling)
        self.assertTrue(self.node._survey_active)
        self.pump_terminal(context, 'stopped')
        self.drain()
        self.assertFalse(self.node._survey_active)

    def test_survey_stop_during_gate_check_cannot_start_next_sample(self):
        self.node._survey_active = True
        self.node.is_sampling = False
        self.node.current_sampling_context = {'source': 'survey', 'attempt_id': 'previous', 'started': True}
        def gate(config):
            self.pump_terminal(self.node.current_sampling_context, 'stopped')
            self.drain()
            return True, ''
        self.node._survey_gate_status = gate
        calls = []
        self.node._call_automation_service = lambda action: calls.append(action) or True
        self.node._start_survey_sample_once({'steps': [{'name': 'next'}]})
        self.assertNotIn('start', calls)
        self.assertFalse(self.node._survey_active)
        self.assertFalse(self.node.is_sampling)


class PumpTerminalContractTests(unittest.TestCase):
    def setUp(self):
        self.case = system_safety.PumpSafetyTests()
        self.case.setUp()
        self.node = self.case.node

    def test_legacy_spectrometer_configuration_cannot_override_active_automation(self):
        self.node.automation_engine._running.set()
        for command in ({'cmd': 'configure', 'gain': 4},
                        {'cmd': 'set_i2c_map', 'mapping': {'spectro_channel': 1}},
                        {'cmd': 'start'}):
            self.node._spectro_cmd_callback(types.SimpleNamespace(data=json.dumps(command)))
        self.assertEqual(self.case.sent, [])

    def test_legacy_spectrometer_services_respect_active_interlock(self):
        self.node.automation_engine._running.set()
        with patch.object(self.node, '_spectro_start', return_value=(True, 'started')) as start, \
                patch.object(self.node, '_apply_i2c_mapping', return_value=True) as mapping:
            self.assertFalse(self.node._spectro_start_callback(None).success)
            self.assertFalse(self.node._i2c_map_apply_callback(None).success)
        start.assert_not_called()
        mapping.assert_not_called()

    def test_old_worker_cleanup_cannot_be_relabelled_with_new_context(self):
        old_context = {'attempt_id': 'old', 'source': 'fcu', 'sample_id': 42}
        self.node.sampling_context = dict(old_context)
        self.node.automation_engine._running.clear()
        # The worker cleared running but has not emitted its terminal event yet.
        self.node.automation_engine._thread = types.SimpleNamespace(is_alive=lambda: True)
        ok, _, _ = self.node._execute_control_action('automation_start', {
            'attempt_id': 'new', 'source': 'fcu', 'sample_id': 43, 'steps': [{'name': 'new'}],
        })
        self.assertFalse(ok)
        self.assertEqual(self.node.sampling_context, old_context)
        self.node._on_automation_status('finished')
        payload = json.loads(self.case.publishers['/usv/automation_status'].messages[-1].data)
        self.assertEqual(payload['sampling_context'], old_context)


if __name__ == '__main__':
    unittest.main()
