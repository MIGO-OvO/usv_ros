"""STOP ACK and Survey launch races through real trigger/pump RPC seams."""
import re
import threading
import unittest
from unittest.mock import Mock, patch

import test_preflight_review_regressions as review_tests
from test_fcu_sampling_result import ForwardPublisher
from test_mavlink_command_compat import _load_script as load_trigger


class StopAndSurveyRaceTests(unittest.TestCase):
    stop = review_tests.SurveyAndQGCChainTests.stop
    send = review_tests.SurveyAndQGCChainTests.send
    join = review_tests.SurveyAndQGCChainTests.join
    status = review_tests.SurveyAndQGCChainTests.status
    wait_phase = review_tests.SurveyAndQGCChainTests.wait_phase

    def setUp(self):
        review_tests.SurveyAndQGCChainTests.setUp(self)
        proxy = self.trigger_module.rospy.ServiceProxy
        self.trigger_module.rospy.ServiceProxy = lambda name, *args: (
            (lambda: self.node._auto_stop_callback(None)) if name == '/usv/automation_stop'
            else proxy(name, *args))
        self.trigger._send_command_ack = Mock()

    def attach_bridge(self):
        module = load_trigger('stop_race_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        self.trigger.mission_status_pub = ForwardPublisher(bridge._mission_status_cb)
        self.node.automation_status_pub = ForwardPublisher(bridge._automation_status_cb)
        self.pubs['/usv/automation_status'] = self.node.automation_status_pub
        return bridge

    def test_qgc_global_stop_failure_reaches_ack_and_failed_mission(self):
        bridge = self.attach_bridge()
        self.trigger._survey_active = True
        self.block_phase = 'compensating'
        self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'started')
        self.wait_phase('compensating')
        self.node.stop_all_pumps = Mock(return_value=False)
        self.trigger._dispatch_command_long(31011, 0, 0, 255, 190, 'test')
        self.join()
        self.trigger._send_command_ack.assert_called_with(31011, self.trigger_module.MAV_RESULT_FAILED, 255, 190)
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.FAILED)
        self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['FAILED'])
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertEqual(self.status()['status'], 'cleanup_failed')
        self.assertFalse(self.trigger._survey_active)
        self.node.automation_engine.start.assert_not_called()

    def test_qgc_stop_requires_firmware_ack_even_when_serial_writes_succeed(self):
        sender = self.node.send_command
        def delayed_old_ack(command):
            if command.strip() == 'STOPALL':
                return True
            if command.strip() == 'PIDQUERY':
                self.node._on_text_received('STOPALL_OK')  # must not satisfy the later STOP
            return sender(command)
        self.node.send_command = delayed_old_ack
        verified_wait = self.node._send_and_wait_text
        with patch.object(self.node, '_send_and_wait_text', side_effect=lambda command, success, errors:
                          verified_wait(command, success, errors, timeout=0.06)) as wait:
            self.trigger._dispatch_command_long(31011, 0, 0, 255, 190, 'test')
        self.trigger._send_command_ack.assert_called_with(31011, self.trigger_module.MAV_RESULT_FAILED, 255, 190)
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertTrue(any(call.args[0] == 'STOPALL' for call in wait.call_args_list))
        self.assertFalse(self.node._spectro_reply_sync_required)

    def test_qgc_success_ack_follows_verified_firmware_stop_and_bridge_idle(self):
        bridge = self.attach_bridge()
        self.trigger._survey_active = True
        self.block_phase = 'compensating'
        self.assertEqual(self.trigger._start_survey_sample_once(self.config), 'started')
        self.wait_phase('compensating')
        self.trigger._dispatch_command_long(31011, 0, 0, 255, 190, 'test')
        self.join()
        self.trigger._send_command_ack.assert_called_with(31011, self.trigger_module.MAV_RESULT_ACCEPTED, 255, 190)
        self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['IDLE'])
        self.assertEqual(self.commands[-2:], ['PIDQUERY', 'STOPALL'])
        self.assertIsNone(self.node._controller_fault)
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertFalse(self.trigger._survey_active)

    def test_confirmation_exception_latches_failure_and_rejects_new_motion(self):
        with patch.object(self.node, '_confirm_all_outputs_stopped', side_effect=RuntimeError('reader failed')):
            self.assertFalse(self.trigger.handle_mavlink_command(31011))
        self.assertTrue(self.node._cleanup_failed)
        self.assertEqual(self.node._controller_fault, 'cleanup_failed')
        self.assertFalse(self.node._execute_control_action('injection_on', {'speed': 60})[0])
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.FAILED)

    def test_existing_fault_cannot_be_cleared_by_successful_stop_ack(self):
        self.node._controller_fault = 'owner_lost'
        self.node._set_terminal_reason('owner_lost')
        self.assertFalse(self.trigger.handle_mavlink_command(31011))
        self.assertEqual(self.node._controller_fault, 'owner_lost')
        self.assertEqual(self.status()['terminal_reason'], 'owner_lost')

    def test_stop_before_first_survey_acquisition_finishes_without_touching_other_owner(self):
        self.assertTrue(self.node._execute_control_action('injection_on', {
            'source': 'web', 'attempt_id': 'other', 'speed': 60})[0])
        with patch.object(self.trigger_module.threading.Thread, 'start'):
            self.assertTrue(self.trigger._start_survey())
        before = list(self.commands)
        self.assertTrue(self.trigger._stop_survey())
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.IDLE)
        self.assertEqual(self.commands, before)
        self.assertTrue(self.node.inject_pump_enabled)
        self.assertEqual(self.node.sampling_context['attempt_id'], 'other')
        self.assertIsNone(self.trigger.current_sampling_context)
        self.assertIsNone(self.trigger._survey_owner_attempt_id)
        self.assertEqual(self.requests, [])

    def test_delayed_survey_launch_cannot_drive_after_stop_success(self):
        self.exercise_delayed_launch_stop(31016)

    def test_delayed_survey_launch_cannot_drive_after_global_stop_success(self):
        self.exercise_delayed_launch_stop(31011)

    def exercise_delayed_launch_stop(self, stop_command):
        self.trigger._survey_active = True
        self.block_phase = 'compensating'
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        transaction = self.trigger._call_control_transaction
        results = {}
        def delayed(action, payload=None):
            if action == 'automation_start':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('launch barrier not released')
                response = transaction(action, payload)
                self.wait_phase('compensating')  # force real motion before the RPC response
                return response
            return transaction(action, payload)
        self.trigger._call_control_transaction = delayed
        def start():
            results['start'] = self.trigger._start_survey_sample_once(self.config)
        def stop():
            results['stop'] = self.trigger.handle_mavlink_command(stop_command)
            results['commands_at_stop'] = len(self.commands)
            stopped.set()
        starter, stopper = threading.Thread(target=start), threading.Thread(target=stop)
        starter.start()
        self.assertTrue(entered.wait(1))
        stopper.start()
        try:
            stopped.wait(0.05)
        finally:
            release.set()
            starter.join(3)
            stopper.join(3)
        self.assertFalse(starter.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(results['stop'])
        self.join()
        late = self.commands[results['commands_at_stop']:]
        self.assertFalse(any(re.match(r'[XYZA]E', command) or command.startswith('PUMP:SET:') for command in late), late)
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.IDLE)
        self.assertFalse(self.node.inject_pump_enabled)
        self.node.automation_engine.start.assert_not_called()

    def test_old_scheduler_generation_cannot_dispatch_after_restart(self):
        with patch.object(self.trigger_module.threading.Thread, 'start'):
            self.assertTrue(self.trigger._start_survey())
            old_args = self.trigger._survey_thread._args
            self.assertTrue(self.trigger._stop_survey())
            self.assertTrue(self.trigger._start_survey())
        self.assertEqual(self.trigger._start_survey_sample_once(self.config, generation=old_args[0]), 'skipped')
        self.assertEqual(self.requests, [])
        self.assertTrue(self.trigger._survey_active)

    def test_old_survey_loop_exit_cannot_clear_restarted_survey(self):
        with patch.object(self.trigger_module.threading.Thread, 'start'):
            self.assertTrue(self.trigger._start_survey())
        old_args = self.trigger._survey_thread._args
        self.trigger._start_survey_sample_once = Mock(return_value='skipped')
        restarted = []
        def sleep(_seconds):
            if not restarted:
                self.assertTrue(self.trigger._stop_survey())
                with patch.object(self.trigger_module.threading.Thread, 'start'):
                    self.assertTrue(self.trigger._start_survey())
                restarted.append(True)
        with patch.object(self.trigger_module.rospy, 'sleep', side_effect=sleep), \
                patch.object(self.trigger_module.rospy, 'is_shutdown', side_effect=lambda: bool(restarted)):
            self.trigger._survey_loop(*old_args)
        self.assertTrue(self.trigger._survey_active)
        self.assertEqual(self.trigger.current_mission_state, self.trigger_module.MissionState.SURVEYING)

    def test_sub_encoder_precision_does_not_timeout_any_preflight_pid_phase(self):
        quantum = 360 / 16384
        for phase in ('homing', 'compensating', 'separating'):
            with self.subTest(phase=phase):
                sender = self.send
                def quantized_feedback(command):
                    preparation = self.node._preflight
                    if (preparation and preparation.snapshot()['phase'] == phase and
                            re.match(r'[XYZA]E[FB]R', command)):
                        moves = re.findall(r'([XYZA])E([FB])R([\d.]+)P([\d.]+)', command)
                        if any(float(precision) < quantum / 2 for _, _, _, precision in moves):
                            self.commands.append(command.strip())
                            for axis, direction, delta, precision in moves:
                                self.node._on_text_received('PID_START:%s,delta=%.1f,dir=%s,prec=%s' % (
                                    axis, float(delta), direction, precision))
                            self.node._on_text_received('CMD_OK')
                            return True  # nearest representable encoder value cannot meet this precision
                    return sender(command)
                self.node.send_command = quantized_feedback
                payload = {'attempt_id': phase, 'source': 'web', 'pid_precision': 0.001,
                           'steps': [{'X': {'enable': 'E'}}],
                           'preflight': {'oil_axis': 'A', 'separation_turns': 2, 'separation_rpm': 20}}
                with patch.object(self.module.AutomationPreflight, 'PID_TIMEOUT', 0.06):
                    self.assertTrue(self.node._execute_control_action('automation_start', payload)[0])
                    self.join()
                self.node.automation_engine.start.assert_called()
                self.assertEqual(self.node.pid_precision, 0.001)
                self.assertEqual(self.node.command_generator.pid_precision, 0.001)
                self.node.automation_engine.start.reset_mock()
                self.stop()


if __name__ == '__main__':
    unittest.main()
