"""Mission heartbeats must agree with the current controller state."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_mavlink_command_compat import _load_script, RecordingMav


class PayloadStatusArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_script('payload_status_arbitration', 'scripts/usv_mavlink_router_bridge.py')
        self.bridge = self.module.USVMavlinkRouterBridge()
        self.mav = RecordingMav()
        self.bridge._conn = SimpleNamespace(mav=self.mav)

    def automation(self, status, **fields):
        self.bridge._automation_status_cb(SimpleNamespace(data=json.dumps(dict(
            status=status, source_monotonic=self.module.time.monotonic(), **fields))))

    def mission(self, state):
        self.bridge._mission_status_cb(SimpleNamespace(data=state))

    def transmitted_status(self):
        b = self.bridge
        self.mav.named_values.clear()
        b._send_payload(b._voltage, b._absorbance, b._pump_angles, b._status_code,
                        b._automation_step, b._automation_total, b._current_loop, b._total_loops,
                        b._sample_count, b._pid_error, b._pid_mode, b._baseline_set,
                        b._reference_voltage, b._baseline_voltage, b._spectrometer_valid)
        return dict(self.mav.named_values).get('USV_STAT')

    def test_idle_controller_and_hold_heartbeats_do_not_alternate_ready_state(self):
        for status in ('idle', 'stopped'):
            with self.subTest(controller_status=status):
                observed = []
                with patch.object(self.module.time, 'monotonic', return_value=100.0):
                    self.mission('HOLD_NO_MISSION:0')
                    self.automation(status, running=False, paused=False, serial_connected=True)
                    observed.append(self.transmitted_status())
                    # The trigger republishes its HOLD phase once per second;
                    # the controller republishes its status between heartbeats.
                    for _ in range(3):
                        self.mission('HOLD_NO_MISSION:0')
                        observed.append(self.transmitted_status())
                        self.automation(status, running=False, paused=False, serial_connected=True)
                        observed.append(self.transmitted_status())
                self.assertEqual(observed, [0] * len(observed))

    def test_mission_heartbeats_keep_current_controller_gates(self):
        cases = (
            ('running', dict(running=True), 1),
            ('paused', dict(running=True, paused=True), 11),
            ('preflight', dict(running=True, preflight={'active': True}), 8),
            ('idle', dict(calibration={'active': True}), 4),
            ('failed', dict(terminal_reason='spectrometer_not_ready'), 3),
            ('cleanup_failed', dict(cleanup_failed=True), 3),
            ('stopped', dict(cleanup_failed=True), 3),
            ('idle', dict(serial_connected=False), 3),
            ('idle', dict(serial_recovering=True), 3),
            ('idle', dict(spectrometer_config_state='failed'), 3),
        )
        for status, fields, expected in cases:
            with self.subTest(controller_status=status, fields=fields):
                with patch.object(self.module.time, 'monotonic', return_value=100.0):
                    self.automation(status, **fields)
                    self.assertEqual(self.transmitted_status(), expected)
                    for state in ('HOLD_NO_MISSION:0', 'IDLE', 'HOLD_NO_MISSION:0'):
                        self.mission(state)
                        self.assertEqual(self.transmitted_status(), expected)

    def test_pending_fcu_result_cannot_be_declared_idle(self):
        self.bridge._fcu_sample_id = 42
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            for _ in range(3):
                self.mission('HOLD_NO_MISSION:0')
                self.assertEqual(self.transmitted_status(), 13)
                self.automation('stopped', running=False)
                self.assertEqual(self.transmitted_status(), 13)

    def test_hold_without_fresh_controller_does_not_imply_idle_or_renew_it(self):
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            self.mission('HOLD_NO_MISSION:0')
            self.assertEqual(self.transmitted_status(), 13)
            self.automation('idle', running=False)
        with patch.object(self.module.time, 'monotonic', return_value=102.0):
            self.mission('HOLD_NO_MISSION:0')
            self.assertEqual(self.transmitted_status(), 0)
            self.assertEqual(self.bridge._field_received_at['USV_STAT'], 100.0)
        with patch.object(self.module.time, 'monotonic', return_value=103.1):
            self.mission('HOLD_NO_MISSION:0')
            self.assertEqual(self.transmitted_status(), 13)

    def test_current_sampling_and_survey_are_not_cleared_by_previous_idle(self):
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            self.automation('idle', running=False)
            for state, expected in (('SAMPLING:2', 1), ('SURVEYING:5.0', 14)):
                self.mission(state)
                self.assertEqual(self.transmitted_status(), expected)

    def test_old_failure_reason_does_not_override_a_current_confirmed_stop(self):
        with patch.object(self.module.time, 'monotonic', return_value=100.0):
            self.automation('failed', terminal_reason='spectrometer_not_ready')
            self.automation('stopped', running=False, terminal_reason='spectrometer_not_ready')
            self.mission('HOLD_NO_MISSION:0')
            self.assertEqual(self.transmitted_status(), 0)


if __name__ == '__main__':
    unittest.main()
