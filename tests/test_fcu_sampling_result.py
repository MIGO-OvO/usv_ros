"""Safety regression: only an explicit, matching FCU result may release a script."""
import json
import types
import unittest
from unittest.mock import patch

from test_mavlink_command_compat import _load_script, FakeConnection, RecordingMav


class ForwardPublisher:
    def __init__(self, callback):
        self.callback = callback
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)
        self.callback(msg)


class FCUSamplingResultTests(unittest.TestCase):
    def setUp(self):
        self.trigger_module = _load_script('trigger_result_test', 'scripts/mavlink_trigger_node.py')
        self.bridge_module = _load_script('bridge_result_test', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(self.bridge_module.USVMavlinkRouterBridge, '_connect_router'):
            self.bridge = self.bridge_module.USVMavlinkRouterBridge()
        self.node = self.trigger_module.MAVLinkTriggerNode()
        self.modes = []
        self.node.set_mode = lambda mode: self.modes.append(mode) or True
        self.node._load_config = lambda: {'steps': []}
        self.node._build_steps_payload = lambda *args: {'steps': []}
        self.node._start_injection_session = lambda *args: True
        self.node._stop_injection_session = lambda *args: True
        self.node._call_automation_service = lambda action: True
        self.node.status_pub = ForwardPublisher(self.bridge._trigger_status_cb)
        self.node.sampling_result_pub = ForwardPublisher(
            lambda msg: self.bridge._sampling_result_cb(msg))

    def trigger(self, sample_id=42):
        msg = types.SimpleNamespace(name='USV_SMPL', value=sample_id,
                                    get_type=lambda: 'NAMED_VALUE_FLOAT')
        self.bridge._conn = FakeConnection([msg])
        self.bridge._cmd_rx_pub = ForwardPublisher(self.node._mavlink_cmd_rx_cb)
        self.bridge._receive_mavlink_messages()

    def results(self):
        return [json.loads(msg.data) for msg in self.node.sampling_result_pub.messages]

    def assert_no_done(self):
        self.assertEqual(list(self.bridge._pending_usv_done), [])
        self.assertFalse(any('Completed' in text for text, _ in self.bridge._pending_statustexts))

    def test_legacy_stop_is_not_completion(self):
        self.bridge._fcu_sample_id = 42
        self.bridge._trigger_status_cb(types.SimpleNamespace(data='sampling_stopped'))
        self.assert_no_done()

    def test_success_releases_matching_id_once(self):
        self.trigger()
        self.node._handle_completion(True)
        self.node._handle_completion(True)
        self.assertEqual(list(self.bridge._pending_usv_done), [42])
        self.assertEqual(self.results()[0]['outcome'], 'succeeded')
        self.bridge._sampling_result_cb(self.node.sampling_result_pub.messages[0])
        self.assertEqual(list(self.bridge._pending_usv_done), [42])
        mav = RecordingMav()
        self.bridge._conn = types.SimpleNamespace(mav=mav)
        self.bridge._send_usv_done(self.bridge._pending_usv_done.popleft())
        self.assertEqual(mav.named_values, [('USV_DONE', 42.0)])

    def test_failure_stops_recording_but_does_not_complete(self):
        self.trigger()
        self.node._handle_completion(False, 'automation_timeout')
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'failed')
        self.assertIn('HOLD', self.modes)
        self.assertIn('sampling_stopped', [m.data for m in self.node.status_pub.messages])

    def test_start_failures_are_correlated_before_dispatch(self):
        for failure in ('injection', 'automation'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'injection':
                    self.node._start_injection_session = lambda *args: False
                else:
                    self.node._call_automation_service = lambda action: False
                self.trigger()
                self.assert_no_done()
                self.assertEqual(self.results()[0]['sample_id'], 42)
                self.assertEqual(self.results()[0]['outcome'], 'failed')
                self.assertEqual(self.bridge._fcu_sample_id, 0)
                self.assertIn('HOLD', self.modes)

    def test_cancel_wins_over_finished_during_stop_service(self):
        self.trigger()
        self.node._call_automation_service = lambda action: self.node._handle_completion(True) or True
        self.node._stop_sampling_sequence()
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'cancelled')
        self.assertIn('HOLD', self.modes)

    def test_explicit_skip_releases_without_claiming_success(self):
        self.node.default_on_fail = 'SKIP'
        self.trigger()
        self.node._handle_completion(False, 'automation_timeout')
        self.assertEqual(list(self.bridge._pending_usv_done), [42])
        self.assertEqual(self.results()[0]['outcome'], 'skipped')
        self.assertFalse(any('Completed' in text for text, _ in self.bridge._pending_statustexts))

    def test_manual_or_late_result_cannot_complete_new_fcu_sample(self):
        self.trigger()
        self.node._handle_completion(False, 'timeout')
        self.trigger(43)
        for source, sample_id in [('fcu', 42), ('manual', 43)]:
            self.bridge._sampling_result_cb(types.SimpleNamespace(data=json.dumps({
                'source': source, 'sample_id': sample_id, 'outcome': 'succeeded'})))
        self.assert_no_done()
        self.node._handle_completion(True)
        self.assertEqual(list(self.bridge._pending_usv_done), [43])

    def test_duplicate_trigger_does_not_restart_finished_sample(self):
        self.trigger()
        self.node._handle_completion(True)
        self.trigger()
        self.assertFalse(self.node.is_sampling)
        self.assertEqual(len(self.results()), 1)

    def test_abort_and_hold_request_failure_never_release(self):
        self.node.default_on_fail = 'ABORT'
        self.node.set_mode = lambda mode: False
        self.trigger()
        self.node._handle_completion(False, 'timeout')
        self.assert_no_done()
        self.assertEqual(self.node.current_mission_state, self.trigger_module.MissionState.ABORTED)
        self.assertEqual(self.results()[0]['outcome'], 'failed')

    def test_busy_manual_sampling_cannot_release_fcu(self):
        self.node.is_sampling = True
        self.node.current_sampling_context = {'source': 'manual'}
        self.trigger()
        self.node._handle_completion(True)
        self.assert_no_done()
        self.assertEqual(self.results()[0]['reason'], 'sampling_busy')

    def test_malformed_results_fail_closed(self):
        self.trigger()
        payloads = ['invalid', 'null', '[]', '{}']
        for sample_id in (True, 0, -1, 65536, 42.5, '42'):
            payloads.append(json.dumps({'source': 'fcu', 'sample_id': sample_id, 'outcome': 'succeeded'}))
        for outcome in (None, [], 'finished', 'FAILED'):
            payloads.append(json.dumps({'source': 'fcu', 'sample_id': 42, 'outcome': outcome}))
        for payload in payloads:
            self.bridge._sampling_result_cb(types.SimpleNamespace(data=payload))
        self.assert_no_done()
        self.assertEqual(self.bridge._fcu_sample_id, 42)

    def test_queued_old_completion_cannot_finish_new_attempt(self):
        self.trigger()
        old_context = self.node.current_sampling_context
        self.node._stop_sampling_sequence()
        self.trigger(43)
        self.node._handle_completion(True, expected_context=old_context)
        self.assertTrue(self.node.is_sampling)
        self.assert_no_done()
        self.node._handle_completion(True)
        self.assertEqual(list(self.bridge._pending_usv_done), [43])

    def test_new_trigger_discards_unsent_old_completion(self):
        self.trigger()
        self.node._handle_completion(True)
        self.trigger(43)
        self.assertEqual(list(self.bridge._pending_usv_done), [])
        self.node._handle_completion(True)
        self.assertEqual(list(self.bridge._pending_usv_done), [43])

    def test_direct_automation_stop_is_not_success(self):
        engine_module = _load_script('engine_result_test', 'scripts/lib/automation_engine.py')
        generator = types.SimpleNamespace(generate_pid_stop_command=lambda: 'PIDSTOP',
                                          generate_stop_command=lambda: 'STOP')
        engine = engine_module.AutomationEngine(generator, lambda cmd: True, log_func=lambda msg: None)
        engine._running.set()
        engine._current_loop = 1
        engine.loop_count = 1
        statuses = []
        engine.on_status_update = statuses.append
        engine._execute_loop = lambda: engine.stop() or False
        self.trigger()
        engine._run_loop()
        self.assertNotIn('finished', statuses)
        self.assertIn('stopped', statuses)
        queued = []
        def capture_thread(target, kwargs, daemon):
            return types.SimpleNamespace(start=lambda: queued.append((target, kwargs)))
        with patch.object(self.trigger_module.threading, 'Thread', side_effect=capture_thread):
            for status in statuses:
                self.node._pump_status_cb(types.SimpleNamespace(data='automation: ' + status))
        for target, kwargs in queued:
            target(**kwargs)
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'cancelled')

    def test_cancellation_during_injection_start_does_not_start_automation(self):
        calls = []
        self.node._call_automation_service = lambda action: calls.append(action) or True
        def start_injection(*args):
            self.node._stop_sampling_sequence()
            return True
        self.node._start_injection_session = start_injection
        self.trigger()
        self.assertEqual(calls, ['stop'])
        self.assert_no_done()
        self.assertNotIn('sampling_started', [m.data for m in self.node.status_pub.messages])

    def test_finished_before_start_acceptance_cannot_mask_start_failure(self):
        queued = []
        def capture_thread(target, kwargs, daemon):
            return types.SimpleNamespace(start=lambda: queued.append((target, kwargs)))
        def reject_start(action):
            self.node._pump_status_cb(types.SimpleNamespace(data='automation: finished'))
            return False
        self.node._call_automation_service = reject_start
        with patch.object(self.trigger_module.threading, 'Thread', side_effect=capture_thread):
            self.trigger()
        self.assertEqual(queued, [])
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'failed')


if __name__ == '__main__':
    unittest.main()
