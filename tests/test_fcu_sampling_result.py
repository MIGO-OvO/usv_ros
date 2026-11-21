"""Safety regression: only an explicit, matching FCU result may release a script."""
import json
import threading
import time
import types
import unittest
from unittest.mock import patch

from test_mavlink_command_compat import _load_script, FakeConnection, RecordingMav
from gps_fixtures import gps_position
from command_fixtures import wait_commands
from scripts.lib import fcu_sampling


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
        self.node._latest_global_position = gps_position()
        self.modes = []
        self.node.set_mode = lambda mode: self.modes.append(mode) or True
        self.node._load_config = lambda: {'steps': []}
        self.node._build_steps_payload = lambda *args: {'steps': []}
        self.node._start_injection_session = lambda *args: True
        self.node._stop_injection_session = lambda *args: True
        self.node._call_automation_service = lambda action: True
        self.node._call_control_transaction = lambda action, payload=None: (
            True, {'cleanup': 'stopped', 'attempt_id': (payload or {}).get('attempt_id')})
        self.node.status_pub = ForwardPublisher(self.bridge._trigger_status_cb)
        self.node.sampling_result_pub = ForwardPublisher(
            lambda msg: self.bridge._sampling_result_cb(msg))
        self.bridge._fcu_sampling_pub = ForwardPublisher(self.node._fcu_sampling_cb)
        self.frames = []

    def trigger(self, sample_id=42):
        msg = types.SimpleNamespace(name='USV_SMPL', value=sample_id,
                                    get_srcSystem=lambda: 1, get_srcComponent=lambda: 1,
                                    get_type=lambda: 'NAMED_VALUE_FLOAT')
        self.bridge._conn = FakeConnection([msg])
        self.bridge._cmd_rx_pub = ForwardPublisher(self.node._mavlink_cmd_rx_cb)
        self.bridge._receive_mavlink_messages()
        wait_commands(self.node)

    def results(self):
        return [json.loads(msg.data) for msg in self.node.sampling_result_pub.messages]

    def frame(self, kind, identity, system=1, component=1, target=191):
        msg = types.SimpleNamespace(
            message_type=fcu_sampling.MESSAGE_TYPE,
            payload=fcu_sampling.encode(kind, identity),
            get_srcSystem=lambda: system, get_srcComponent=lambda: component,
            target_system=1, target_component=target, get_type=lambda: 'V2_EXTENSION')
        self.bridge._conn = FakeConnection([msg])
        self.bridge._conn.mav = types.SimpleNamespace(v2_extension_send=self.record_frame)
        self.bridge._receive_mavlink_messages()

    def record_frame(self, network, system, component, message_type, payload):
        self.assertEqual((network, system, component, message_type), (0, 1, 1, 31021))
        self.frames.append(fcu_sampling.decode(payload))

    def test_native_offer_then_single_start_releases_only_matching_attempt(self):
        m = fcu_sampling.Message
        identity = (2**63 + 5, 42, 3, 1)
        self.node._build_steps_payload = self.trigger_module.MAVLinkTriggerNode._build_steps_payload.__get__(self.node)
        self.node._load_config = lambda: {
            'sampling_sequence': {'steps': [{'name': 'test'}]},
            'waypoint_sampling': {'1': {'loop_count': 4, 'on_fail': 'LOITER'}, '3': {'loop_count': 2}},
        }
        starts = []
        self.node._call_automation_service = lambda action: starts.append(action) or True
        self.frame(m.START, identity)
        self.frame(m.OFFER, identity, target=190)
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames, [])
        self.frame(m.OFFER, identity)
        self.assertEqual(self.frames, [])
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames, [(m.READY, identity)])
        self.assertEqual(starts, [])
        self.frame(m.START, identity)
        self.frame(m.START, identity)
        wait_commands(self.node)
        self.assertEqual(starts, ['start'])
        self.assertEqual(self.node.current_sampling_context['waypoint_seq'], 3)
        prepared = json.loads(self.node.steps_pub.messages[-1].data)
        self.assertEqual(prepared['loop_count'], 4)
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames[-1], (m.ACCEPTED, identity))
        self.assertEqual([r['outcome'] for r in self.results()], ['accepted'])
        self.frame(m.CANCEL, (identity[0] - 1, *identity[1:]))
        self.assertTrue(self.node.is_sampling)
        self.node._handle_completion(True)
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames[-1], (m.DONE, identity))
        self.assert_no_legacy_result()

        newer = (identity[0] + 1, *identity[1:])
        self.frame(m.OFFER, newer)
        self.bridge._flush_fcu_reply()
        self.frame(m.START, newer)
        wait_commands(self.node)
        self.assertEqual(starts, ['start', 'start'])
        self.bridge._record_fcu_result(dict(fcu_sampling.context(identity), outcome='succeeded'))
        self.assertEqual(self.bridge._fcu_reply[:2], (m.ACCEPTED, newer))
        self.node._handle_completion(False, 'preflight_failed')
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames[-1], (m.FAILED_LOITER, newer))
        self.assertEqual(self.modes, [])

    def test_native_busy_reports_hold_without_stopping_other_owner(self):
        m = fcu_sampling.Message
        identity = (11, 42, 3, 1)
        owner = {'source': 'manual', 'attempt_id': 'existing'}
        self.node.current_sampling_context = owner
        self.node.is_sampling = True
        with patch.object(self.node, '_call_control_transaction') as control:
            self.frame(m.OFFER, identity)
            self.bridge._flush_fcu_reply()
            self.frame(m.START, identity)
            wait_commands(self.node)
            self.bridge._flush_fcu_reply()
            self.frame(m.CANCEL, identity)
            control.assert_not_called()
        self.assertIs(self.node.current_sampling_context, owner)
        self.assertEqual(self.frames[-1], (m.FAILED_HOLD, identity))
        self.assertEqual(self.results()[-1]['reason'], 'sampling_busy')

    def test_retired_transport_cleans_only_blocked_native_start(self):
        m = fcu_sampling.Message
        identity = (31, 42, 3, 1)
        entered = threading.Event()
        release = threading.Event()
        cleanup = threading.Event()
        calls = []

        def start(action):
            calls.append(action)
            entered.set()
            return release.wait(3)

        def control(action, payload):
            calls.append((action, payload.get('attempt_id')))
            cleanup.set()
            return True, {'cleanup': 'stopped', 'attempt_id': payload['attempt_id']}

        self.node._call_automation_service = start
        self.node._call_control_transaction = control
        self.node._mavlink_tcp_session = 7
        self.bridge._session_pub = ForwardPublisher(self.node._mavlink_session_cb)
        self.frame(m.OFFER, identity)
        self.bridge._flush_fcu_reply()
        self.frame(m.START, identity)
        try:
            self.assertTrue(entered.wait(2))
            attempt = self.node.current_sampling_context['attempt_id']
            self.bridge._retire_router('test transport retirement')
            self.assertTrue(cleanup.wait(2))
            self.assertEqual(calls[:2], ['start', ('automation_cleanup', attempt)])
        finally:
            release.set()
        wait_commands(self.node)
        deadline = time.monotonic() + 2
        while self.node.is_sampling and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.node.is_sampling)
        self.assertIsNone(self.bridge._fcu_reply)
        self.frame(m.START, identity)
        wait_commands(self.node)
        self.assertEqual(calls.count('start'), 1)
        self.assertEqual(self.modes, [])
        self.assert_no_legacy_result()

    def test_native_owner_fault_and_activity_follow_the_token(self):
        m = fcu_sampling.Message
        identity = (41, 42, 3, 1)
        self.frame(m.OFFER, identity)
        self.bridge._flush_fcu_reply()
        self.frame(m.START, identity)
        wait_commands(self.node)
        status = {
            'source': 'fcu', 'sample_id': identity[1], 'running': True,
            'source_monotonic': time.monotonic(), 'sampling_context': {'fcu_token': '40'},
        }
        reply = self.bridge._fcu_reply
        self.bridge._automation_status_cb(types.SimpleNamespace(data=json.dumps(status)))
        self.assertIs(self.bridge._fcu_reply, reply)
        status['sampling_context']['fcu_token'] = str(identity[0])
        self.bridge._automation_status_cb(types.SimpleNamespace(data=json.dumps(status)))
        self.assertEqual(self.bridge._fcu_reply[3], status['source_monotonic'] + 3)
        status['controller_fault'] = 'owner_lost'
        self.bridge._automation_status_cb(types.SimpleNamespace(data=json.dumps(status)))
        self.bridge._flush_fcu_reply()
        self.assertEqual(self.frames[-1], (m.FAILED_HOLD, identity))

    def assert_no_legacy_result(self):
        self.assertEqual(list(self.bridge._pending_usv_done), [])
        self.assertEqual(list(self.bridge._pending_usv_fail), [])

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
        for failure in ('preflight', 'automation'):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == 'preflight':
                    self.trigger()
                    self.node._handle_completion(False, 'controller_fault')
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
                self.node._automation_status_cb(types.SimpleNamespace(data=json.dumps({
                    'status': status, 'sampling_context': dict(self.node.current_sampling_context),
                })))
        for target, kwargs in queued:
            target(**kwargs)
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'cancelled')

    def test_cancellation_during_preparation_does_not_start_automation(self):
        calls = []
        self.node._call_automation_service = lambda action: calls.append(action) or True
        original_prepare = self.node._prepare_automation_steps
        def prepare(*args):
            original_prepare(*args)
            self.node._stop_sampling_sequence()
        self.node._prepare_automation_steps = prepare
        self.trigger()
        self.assertEqual(calls, ['stop'])
        self.assert_no_done()
        self.assertNotIn('sampling_started', [m.data for m in self.node.status_pub.messages])

    def test_finished_before_start_acceptance_cannot_mask_start_failure(self):
        queued = []
        real_thread = self.trigger_module.threading.Thread
        def capture_thread(target, kwargs=None, daemon=True, **options):
            if target.__name__ == '_command_loop':
                return real_thread(target=target, kwargs=kwargs or {}, daemon=daemon, **options)
            return types.SimpleNamespace(start=lambda: queued.append((target, kwargs)))
        def reject_start(action):
            if action != 'start':
                return True
            self.node._automation_status_cb(types.SimpleNamespace(data=json.dumps({
                'status': 'finished', 'sampling_context': dict(self.node.current_sampling_context),
            })))
            return False
        self.node._call_automation_service = reject_start
        with patch.object(self.trigger_module.threading, 'Thread', side_effect=capture_thread):
            self.trigger()
        self.assertEqual(queued, [])
        self.assert_no_done()
        self.assertEqual(self.results()[0]['outcome'], 'failed')


if __name__ == '__main__':
    unittest.main()
