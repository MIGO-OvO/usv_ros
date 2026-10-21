"""Only actual source updates may refresh outbound measurement fields."""
import json
import time
import unittest
from types import SimpleNamespace

from test_mavlink_command_compat import _load_script, RecordingMav


class SourceAgeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('source_age_bridge', 'scripts/usv_mavlink_router_bridge.py')
        self.bridge = self.module.USVMavlinkRouterBridge()
        self.mav = RecordingMav()
        self.bridge._conn = SimpleNamespace(mav=self.mav)

    def send(self):
        b = self.bridge
        self.mav.named_values.clear()
        b._send_payload(b._voltage, b._absorbance, b._pump_angles, b._status_code,
                        b._automation_step, b._automation_total, b._current_loop, b._total_loops,
                        b._sample_count, b._pid_error, b._pid_mode, b._baseline_set,
                        b._reference_voltage, b._baseline_voltage, b._spectrometer_valid)
        return dict(self.mav.named_values)

    def test_angle_update_cannot_renew_old_measurement_or_other_axes(self):
        b = self.bridge
        b._voltage_cb(SimpleNamespace(data=json.dumps({'voltage': 1.2, 'valid': True, 'baseline_set': True})))
        b._angles_cb(SimpleNamespace(data='X:1,Y:2'))
        self.assertEqual(self.send()['USV_VLD'], 1)
        b._field_received_at = {field: at - 4 for field, at in b._field_received_at.items()}
        b._angles_cb(SimpleNamespace(data='X:3'))
        sent = self.send()
        self.assertEqual(sent['PUMP_X'], -1)  # No saved zero: explicit uncalibrated display.
        self.assertNotIn('PUMP_Y', sent)
        self.assertNotIn('USV_VOLT', sent)
        self.assertEqual(sent['USV_VLD'], 0)
        self.assertEqual(sent['USV_BSET'], 0)

    def test_latched_old_status_and_invalid_signal_do_not_confirm_stop(self):
        b = self.bridge
        b._automation_status_cb(SimpleNamespace(data=json.dumps({
            'source_monotonic': time.monotonic() - 10, 'ads_state': 1, 'status': 'idle'})))
        self.assertEqual(self.send()['USV_ADS'], 0)
        b._automation_status_cb(SimpleNamespace(data=json.dumps({'ads_state': 3, 'status': 'idle'})))
        b._voltage_cb(SimpleNamespace(data=json.dumps({'valid': False})))
        self.assertEqual(self.send()['USV_ADS'], 3)
        b._automation_status_cb(SimpleNamespace(data=json.dumps({'ads_state': 1, 'status': 'idle'})))
        self.assertEqual(self.send()['USV_ADS'], 1)

    def test_old_automation_does_not_override_fresh_mission(self):
        b = self.bridge
        b._automation_status_cb(SimpleNamespace(data=json.dumps({
            'source_monotonic': time.monotonic() - 10, 'running': True, 'status': 'running'})))
        b._mission_status_cb(SimpleNamespace(data='IDLE'))
        self.assertEqual(self.send()['USV_STAT'], 0)


if __name__ == '__main__':
    unittest.main()
