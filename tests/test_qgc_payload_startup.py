"""QGC startup and stop regressions through the trigger/pump/bridge seams."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_preflight_review_regressions as preparation
from test_fcu_sampling_result import ForwardPublisher
from test_mavlink_command_compat import _load_script


class PayloadStartupTests(unittest.TestCase):
    def test_bridge_starts_without_a_blocking_fcu_heartbeat_wait(self):
        module = _load_script('qgc_startup_bridge', 'scripts/usv_mavlink_router_bridge.py')
        connection = Mock()
        connection.wait_heartbeat.return_value = None
        with patch.object(module, 'connect_router', return_value=connection), patch.object(module, 'next_transport_session', return_value=1):
            bridge = module.USVMavlinkRouterBridge()
        connection.wait_heartbeat.assert_not_called()
        bridge._send_heartbeat()
        connection.mav.heartbeat_send.assert_called_once()

    def test_mavros_initialization_does_not_delay_owner_or_reset_live_sampling(self):
        module = _load_script('qgc_startup_trigger', 'scripts/mavlink_trigger_node.py')
        node = module.MAVLinkTriggerNode()
        node.is_sampling = True
        node.current_sampling_context = {'source': 'manual', 'attempt_id': 'early-qgc-start'}
        node.current_mission_state = module.MissionState.SAMPLING
        released = threading.Event()
        initialized = threading.Event()
        renewed = threading.Event()
        shutdown = threading.Event()

        def init_services():
            released.wait(2)
            initialized.set()

        node._init_services = init_services
        node.wait_for_mavros = Mock(return_value=False)
        node.owner_pub = ForwardPublisher(lambda msg: renewed.set())
        module.rospy.is_shutdown = shutdown.is_set
        module.rospy.Rate = lambda hz: SimpleNamespace(sleep=shutdown.set)
        runner = threading.Thread(target=node.run)
        runner.start()
        try:
            self.assertTrue(renewed.wait(0.5), 'MAVROS startup blocked the sampling owner lease')
            self.assertFalse(initialized.is_set())
            self.assertEqual(node.current_mission_state, module.MissionState.SAMPLING)
            self.assertEqual(json.loads(node.owner_pub.messages[0].data)['attempt_id'], 'early-qgc-start')
            node.wait_for_mavros.assert_not_called()
        finally:
            shutdown.set()
            released.set()
            runner.join(2)
            self.assertTrue(initialized.wait(2))
        self.assertFalse(runner.is_alive())


class QGCPreparationTests(unittest.TestCase):
    stop = preparation.SurveyAndQGCChainTests.stop
    send = preparation.SurveyAndQGCChainTests.send
    join = preparation.SurveyAndQGCChainTests.join
    status = preparation.SurveyAndQGCChainTests.status
    wait_phase = preparation.SurveyAndQGCChainTests.wait_phase

    def setUp(self):
        preparation.SurveyAndQGCChainTests.setUp(self)
        self.config['pump_settings']['default_speed'] = 0
        self.config['pump_settings']['injection_pump_policy'] = {
            'mode': 'automation', 'speed': 38, 'lead_time_s': 0, 'stop_on_finish': True,
        }
        self.trigger._send_command_ack = Mock()

    def attach_bridge(self):
        module = _load_script('qgc_preparation_bridge', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        self.trigger.mission_status_pub = ForwardPublisher(bridge._mission_status_cb)
        self.node.automation_status_pub = ForwardPublisher(bridge._automation_status_cb)
        self.pubs['/usv/automation_status'] = self.node.automation_status_pub
        return bridge

    def test_qgc_manual_sample_runs_the_configured_automatic_injection_pump(self):
        self.assertTrue(self.trigger.handle_mavlink_command(31010))
        self.join()
        self.assertIn('PUMP:SET:38', self.commands)
        self.assertTrue(self.node.inject_pump_enabled)
        self.node.automation_engine.start.assert_called_once()
        self.assertEqual(self.requests[0][1]['injection_pump_policy']['speed'], 38)

    def test_preparation_is_distinct_from_sampling_and_normal_stop_confirms_halt(self):
        self.config['pump_settings']['default_speed'] = 60
        bridge = self.attach_bridge()
        self.block_phase = 'compensating'
        self.assertTrue(self.trigger.handle_mavlink_command(31010))
        self.wait_phase('compensating')
        self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['WAITING_STABLE'])
        # A delayed mission update must not turn preparation into a pausable step.
        bridge._mission_status_cb(SimpleNamespace(data='SAMPLING:0'))
        self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['WAITING_STABLE'])
        self.trigger._dispatch_command_long(31011, 0, 0, 255, 190, 'test')
        self.join()
        self.trigger._send_command_ack.assert_called_with(
            31011, self.trigger_module.MAV_RESULT_ACCEPTED, 255, 190)
        self.assertEqual(self.commands[-2:], ['PIDQUERY', 'STOPALL'])
        self.assertFalse(self.node.inject_pump_enabled)
        self.assertFalse(self.trigger.is_sampling)
        self.assertEqual(bridge._status_code, bridge._MISSION_STATE_CODES['IDLE'])
        self.node.automation_engine.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
