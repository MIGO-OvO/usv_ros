import json
import os
import tempfile
import time
import types
import unittest
from unittest.mock import patch, Mock

from gps_fixtures import gps_message, gps_position
from scripts.lib.gps_diagnostics import GPSDiagnosticsCache, diagnose
from scripts.lib.sample_recording.record import freeze_position
from scripts.web_config_server import FLASK_AVAILABLE, WebConfigServer


def packet(kind, system=1, component=1, **values):
    return types.SimpleNamespace(get_type=lambda: kind, get_srcSystem=lambda: system,
                                 get_srcComponent=lambda: component, **values)


def evidence(fix=3):
    cache = GPSDiagnosticsCache()
    cache.ingest(packet('HEARTBEAT', autopilot=3))
    cache.ingest(packet('GLOBAL_POSITION_INT', lat=300000000, lon=1200000000, alt=-900000))
    if fix is not None:
        cache.ingest(packet('GPS_RAW_INT', fix_type=fix, satellites_visible=14 if fix >= 3 else 0,
                            lat=300000000, lon=1200000000, alt=4500, eph=90, epv=130))
    return cache


class DiagnosticsTests(unittest.TestCase):
    def test_raw_missing_despite_fused_coordinates(self):
        result = diagnose(evidence(None).snapshot())
        self.assertEqual(result['overall']['state'], 'raw_missing')
        self.assertIn('未收到 GPS_RAW_INT', result['overall']['message'])
        self.assertIsNone(result['gps_raw']['latitude'])
        self.assertTrue(result['global_position']['available'])
        self.assertFalse(result['map_position_valid'])

    def test_no_receiver_no_fix_2d_3d_rtk(self):
        for fix, state in [(0, 'no_receiver'), (1, 'no_fix'), (2, 'no_fix'), (3, 'fix_3d'), (4, 'fix_3d'), (5, 'fix_3d'), (6, 'fix_3d')]:
            with self.subTest(fix=fix):
                result = diagnose(evidence(fix).snapshot(), gps_position())
                self.assertEqual(result['overall']['state'], state)
                self.assertEqual(result['gps_raw']['hdop'], .9)
                self.assertEqual(result['map_position_valid'], fix >= 3)

    def test_stale_raw_and_heartbeat(self):
        data = evidence().snapshot()
        data['records']['GPS_RAW_INT']['received_monotonic'] -= 4
        self.assertEqual(diagnose(data)['overall']['state'], 'stale')
        data['records']['HEARTBEAT']['received_monotonic'] -= 4
        self.assertEqual(diagnose(data)['overall']['state'], 'fcu_timeout')

    def test_invalid_coordinates(self):
        for lat in (91, float('nan'), None):
            data = evidence().snapshot()
            data['records']['GPS_RAW_INT']['latitude'] = lat
            self.assertEqual(diagnose(data)['overall']['state'], 'invalid_coordinates')

    def test_mavros_disconnect_independent(self):
        result = diagnose(evidence().snapshot(), mavros={'connected': False, 'received_monotonic': time.monotonic()})
        self.assertTrue(result['fcu']['heartbeat_valid'])
        self.assertFalse(result['fcu']['mavros_connected'])
        self.assertEqual(result['overall']['state'], 'fix_3d')
        self.assertIn('FCU连接正常', result['warnings'][0])

    def test_wrong_source_and_companion_heartbeat_ignored(self):
        cache = GPSDiagnosticsCache()
        for system, component, autopilot in [(1, 191, 8), (255, 1, 3), (1, 1, 8)]:
            cache.ingest(packet('HEARTBEAT', system, component, autopilot=autopilot))
        self.assertFalse(diagnose(cache.snapshot())['fcu']['heartbeat_valid'])

    def test_disabled_and_unknown_parameters(self):
        cache = evidence()
        self.assertIsNone(diagnose(cache.snapshot())['gps_config']['gps1_type'])
        cache.ingest(packet('PARAM_VALUE', param_id=b'GPS1_TYPE\x00', param_value=0))
        self.assertEqual(diagnose(cache.snapshot())['overall']['state'], 'disabled')
        self.assertFalse(diagnose(cache.snapshot(), gps_position())['map_position_valid'])

    def test_sentinels_extensions_and_bounded_text(self):
        cache = evidence()
        cache.ingest(packet('GPS_RAW_INT', fix_type=1, eph=65535, epv=65535, satellites_visible=255, h_acc=0))
        raw = diagnose(cache.snapshot())['gps_raw']
        for field in ('satellites', 'hdop', 'vdop', 'horizontal_accuracy_m', 'vertical_accuracy_m'):
            self.assertIsNone(raw[field])
        for index in range(20):
            cache.ingest(packet('STATUSTEXT', text='GPS message %d' % index, severity=4))
        self.assertEqual(len(cache.texts), 12)
        json.dumps(diagnose(cache.snapshot()), allow_nan=False)

    def test_version_and_system_status(self):
        cache = evidence()
        cache.ingest(packet('AUTOPILOT_VERSION', flight_sw_version=0x040603ff,
                            flight_custom_version=list(b'1234abcd')))
        cache.ingest(packet('SYS_STATUS', onboard_control_sensors_present=32,
                            onboard_control_sensors_enabled=32, onboard_control_sensors_health=0))
        result = diagnose(cache.snapshot())
        self.assertEqual(result['autopilot_version']['git_hash'], '1234abcd')
        self.assertIn('4.6.3', result['autopilot_version']['firmware_version'])
        self.assertTrue(result['sys_status']['gps_present'])
        self.assertFalse(result['sys_status']['gps_health'])

    def test_stale_configuration_is_not_authoritative(self):
        cache = evidence()
        cache.ingest(packet('PARAM_VALUE', param_id='GPS1_TYPE', param_value=0), now=time.monotonic() - 61)
        self.assertEqual(diagnose(cache.snapshot())['overall']['state'], 'fix_3d')


class BridgeDiagnosticsTests(unittest.TestCase):
    def test_existing_tcp_receive_path_publishes_without_mavros(self):
        from test_mavlink_command_compat import _load_script, FakeConnection
        module = _load_script('bridge_gps_test', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        bridge._conn = FakeConnection([packet('HEARTBEAT', autopilot=3),
                                      packet('GPS_RAW_INT', fix_type=3, lat=300000000, lon=1200000000)])
        bridge._receive_mavlink_messages()
        bridge._conn.mav = Mock()
        bridge._publish_gps_evidence()
        data = json.loads(bridge._gps_pub.messages[-1].data)
        self.assertTrue(diagnose(data)['fcu']['heartbeat_valid'])
        self.assertEqual(diagnose(data)['overall']['state'], 'fix_3d')
        bridge._conn.mav.param_request_read_send.assert_called_once_with(1, 1, b'GPS1_TYPE', -1)
        bridge._publish_gps_evidence()
        bridge._conn.mav.param_request_read_send.assert_called_once()

    def test_parameter_requests_do_not_run_without_fcu_heartbeat(self):
        from test_mavlink_command_compat import _load_script
        module = _load_script('bridge_gps_offline_test', 'scripts/usv_mavlink_router_bridge.py')
        with patch.object(module.USVMavlinkRouterBridge, '_connect_router'):
            bridge = module.USVMavlinkRouterBridge()
        bridge._conn = types.SimpleNamespace(mav=Mock())
        bridge._publish_gps_evidence()
        self.assertEqual(bridge._conn.mav.mock_calls, [])

    def test_diagnostics_never_relaxes_sampling(self):
        for reason, pos in [('gps_no_fix', dict(gps_position(), fix_status=-1)),
                            ('gps_stale', gps_position(age=4)),
                            ('gps_invalid_coordinates', gps_position(lat=91)),
                            ('gps_missing_receive_time', dict(gps_position(), received_monotonic=None))]:
            with self.subTest(reason=reason):
                result = diagnose(evidence().snapshot(), pos)
                self.assertFalse(result['sampling_position']['valid'])
                self.assertEqual(result['sampling_position']['reason'], reason)
                with self.assertRaisesRegex(ValueError, reason):
                    freeze_position(pos)

    def test_clock_offset_is_diagnostic_and_never_gates_sampling(self):
        # header.stamp far behind the Jetson wall clock: sampling stays valid.
        pos = gps_position()
        pos['gps_timestamp'] = time.time() - 5.0
        pos['header_clock_offset_s'] = 5.0
        result = diagnose(evidence().snapshot(), pos)
        self.assertTrue(result['sampling_position']['valid'])
        self.assertIsNone(result['sampling_position']['reason'])
        self.assertAlmostEqual(result['navsat']['header_clock_offset_s'], 5.0)
        self.assertTrue(any('偏差' in warning for warning in result['warnings']))
        # Future header.stamp: allowed too, warning points the other direction.
        pos['gps_timestamp'] = time.time() + 30.0
        pos['header_clock_offset_s'] = -30.0
        result = diagnose(evidence().snapshot(), pos)
        self.assertTrue(result['sampling_position']['valid'])
        self.assertTrue(any('偏差' in warning for warning in result['warnings']))
        # Small offsets stay quiet.
        result = diagnose(evidence().snapshot(), gps_position())
        self.assertFalse(any('偏差' in warning for warning in result['warnings']))

    def test_navsat_age_uses_local_receive_clock_only(self):
        pos = gps_position()
        pos['gps_timestamp'] = time.time() - 9.0
        pos['header_clock_offset_s'] = 9.0
        result = diagnose(evidence().snapshot(), pos)
        self.assertLess(result['navsat']['age_s'], 1.0)
        self.assertTrue(result['sampling_position']['valid'])


@unittest.skipUnless(FLASK_AVAILABLE, 'Flask unavailable')
class DiagnosticsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {'USV_MISSION_DATA_DIR': self.tmp.name}):
            self.server = WebConfigServer(standalone=True)
        self.addCleanup(self.server.sample_storage.close)
        self.server._gps_evidence = evidence().snapshot()

    def gps(self, **kwargs):
        clock = types.SimpleNamespace(Time=types.SimpleNamespace(now=lambda: types.SimpleNamespace(to_sec=time.time)))
        with patch('scripts.web_config_server.rospy', clock):
            self.server._gps_cb(gps_message(**kwargs))

    def test_api_is_no_store_and_missing_fields_null(self):
        self.server._gps_evidence = evidence(None).snapshot()
        response = self.server.app.test_client().get('/api/gps/diagnostics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json['overall']['state'], 'raw_missing')
        self.assertIsNone(response.json['gps_raw']['latitude'])

    def test_invalid_fix_never_moves_map_or_appends_track(self):
        self.gps()
        self.assertEqual(len(self.server.live_track_points), 1)
        self.gps(lat=31, status=-1)
        self.assertEqual(len(self.server.live_track_points), 1)
        position = self.server._map_position_snapshot()
        self.assertTrue(position['last_valid'])
        self.assertEqual(position['wgs84']['lat'], 30)
        self.assertIsNotNone(position['age_s'])

    def test_missing_raw_invalid_and_no_fix_cannot_move_map(self):
        self.server._gps_evidence = evidence(None).snapshot()
        self.gps()
        self.assertIsNone(self.server.current_position)
        self.server._gps_evidence = evidence().snapshot()
        for fields in ({'lat': 91}, {'status': -1}):
            self.gps(**fields)
        self.assertEqual(self.server.live_track_points, [])

    def test_stale_header_stamp_with_fresh_local_receive_moves_map(self):
        # Field scenario behind the clock-domain fix: header.stamp trails the
        # Jetson wall clock, but the fix was received just now. The map must
        # move; sampling admission stays strict on the local receive clock.
        self.gps(age=4)
        self.assertEqual(len(self.server.live_track_points), 1)
        self.assertTrue(self.server._map_position_snapshot()['gps_valid'])

    def test_silent_gps_expiry_marks_last_position(self):
        self.gps()
        self.server._latest_real_gps['received_monotonic'] -= 4
        self.assertTrue(self.server._map_position_snapshot()['last_valid'])
