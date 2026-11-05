"""MAVROS watchdog deadlines, manual recovery and Web access contracts."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_mavlink_command_compat import _load_script


class LinkRecoveryTests(unittest.TestCase):
    def test_launch_enables_mavros_respawn_and_installs_the_watchdog(self):
        import xml.etree.ElementTree as ET
        root = Path(__file__).resolve().parents[1]
        launch = ET.parse(root / 'launch/usv_bringup.launch').getroot()
        self.assertEqual(launch.find("arg[@name='mavros_respawn']").get('default'), 'true')
        self.assertEqual(launch.find("node[@name='link_recovery_node']").get('if'), '$(arg enable_mavros)')
        self.assertIn('scripts/link_recovery_node.py', (root / 'CMakeLists.txt').read_text())

    def setUp(self):
        self.module = _load_script('link_recovery_test', 'scripts/link_recovery_node.py')
        self.clock = patch.object(self.module.time, 'monotonic', return_value=100.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.node = self.module.LinkRecoveryNode()
        self.node._restart_mavros = Mock()

    def step(self, now):
        self.now.return_value = now
        self.node.step()
        return json.loads(self.node._pub.messages[-1].data)

    def test_persistent_disconnect_recovers_with_backoff_until_a_fresh_state_arrives(self):
        self.assertEqual(self.step(129)['attempts'], 0)
        status = self.step(130)
        self.assertEqual(status['state'], 'waiting_heartbeat')
        self.assertFalse(status['connected'])
        self.assertEqual(self.step(159)['attempts'], 1)
        self.assertEqual(self.step(160)['attempts'], 2)
        self.assertEqual(self.step(219)['attempts'], 2)
        self.assertEqual(self.step(220)['attempts'], 3)
        self.node._state_cb(SimpleNamespace(connected=True))
        self.assertEqual(self.step(221)['state'], 'connected')
        self.assertEqual(self.node._restart_mavros.call_count, 3)

    def test_state_silence_after_connected_is_not_treated_as_permanently_online(self):
        self.node._state_cb(SimpleNamespace(connected=True))
        self.assertTrue(self.step(104)['connected'])
        self.assertFalse(self.step(106)['connected'])
        self.step(135)
        self.node._restart_mavros.assert_called_once()

    def test_manual_requests_coalesce_and_healthy_link_is_not_restarted(self):
        for _ in range(5):
            self.assertTrue(self.node._reconnect_cb(None).success)
        self.step(101)
        self.node._restart_mavros.assert_called_once()
        self.node._request_cb(SimpleNamespace(data='ground_station'))
        self.step(102)
        self.node._restart_mavros.assert_called_once()
        self.node._state_cb(SimpleNamespace(connected=True))
        self.assertIn('已连接', self.node._reconnect_cb(None).message)
        self.step(103)
        self.node._restart_mavros.assert_called_once()

    def test_failed_restart_remains_observable_and_rate_limited(self):
        self.node._restart_mavros.side_effect = RuntimeError('XML-RPC timed out')
        status = self.step(130)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('XML-RPC', status['last_error'])
        self.step(131)
        self.node._restart_mavros.assert_called_once()

    def test_opt_out_does_not_kill_a_node_without_respawn(self):
        self.node.enabled = False
        self.assertFalse(self.node._reconnect_cb(None).success)
        self.assertEqual(self.step(1000)['state'], 'disabled')
        self.node._restart_mavros.assert_not_called()

    def test_restart_uses_only_bounded_mavros_shutdown(self):
        with patch.object(self.module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.module.LinkRecoveryNode._restart_mavros(self.node)
        self.assertEqual(run.call_args.args[0], ['rosnode', 'kill', '/mavros'])
        self.assertEqual(run.call_args.kwargs['timeout'], 4.0)

    def test_a_fresh_connection_arriving_before_shutdown_cancels_restart(self):
        self.now.return_value = 130
        self.node._state_cb(SimpleNamespace(connected=True))
        with patch.object(self.module.subprocess, 'run') as run:
            self.assertFalse(self.module.LinkRecoveryNode._restart_mavros(self.node))
        run.assert_not_called()


class LinkRecoveryWebTests(unittest.TestCase):
    def setUp(self):
        from scripts import web_config_server as module
        self.module = module
        if not module.FLASK_AVAILABLE:
            self.skipTest('Flask unavailable')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'USV_MISSION_DATA_DIR': tmp.name,
                                     'USV_WEB_REQUIRE_AUTH': '', 'USV_WEB_CONTROL_TOKEN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.server = module.WebConfigServer(standalone=True)
        self.client = self.server.app.test_client()

    def test_standalone_and_cross_origin_cannot_report_a_successful_reconnect(self):
        self.assertEqual(self.client.post('/api/diagnostics/link/reconnect').status_code, 503)
        self.assertEqual(self.client.post('/api/diagnostics/link/reconnect',
                                         headers={'Origin': 'https://untrusted.invalid'}).status_code, 403)

    def test_cached_mavros_true_expires_and_recovery_status_has_its_own_freshness(self):
        self.server._mavros_state['connected'] = True
        self.server._mavros_state_received_at = 100.0
        self.server._link_recovery_received_at = 100.0
        with patch.object(self.module.time, 'monotonic', return_value=107.0):
            data = self.client.get('/api/diagnostics/link').get_json()['data']
        self.assertFalse(data['mavros']['connected'])
        self.assertTrue(data['mavros']['stale'])
        self.assertFalse(data['recovery']['available'])

    def test_post_queues_recovery_and_reports_service_failure(self):
        self.server.standalone = False
        for success, status in ((True, 200), (False, 409)):
            with patch.object(self.module, 'ROS_AVAILABLE', True), \
                    patch.object(self.module.rospy, 'wait_for_service'), \
                    patch.object(self.module.rospy, 'ServiceProxy', return_value=lambda: SimpleNamespace(
                        success=success, message='等待心跳')) as service:
                response = self.client.post('/api/diagnostics/link/reconnect')
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.get_json()['message'], '等待心跳')
            self.assertEqual(service.call_args.args[0], '/usv/link_reconnect')


if __name__ == '__main__':
    unittest.main()
