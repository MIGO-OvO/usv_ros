"""Real sampling may never use lab/disabled-ADS as a measurement bypass."""
import time
import unittest
from unittest.mock import patch

import test_automation_preflight as preparation
from measurement_fixtures import prepare_measurement, fresh_measurement


class RealMeasurementAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation.PreflightWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.node = self.fixture.node

    def test_lab_and_disabled_spectrometer_rejected_before_preflight_motion(self):
        for lab in (False, True):
            with self.subTest(lab=lab):
                self.node.lab_mode_enabled = lab
                self.node.spectro_config['enabled'] = False
                self.assertFalse(self.fixture.start()[0])
                self.assertEqual(self.fixture.commands, [])

    def test_disabled_or_idle_cannot_satisfy_sample_wait(self):
        self.node.lab_mode_enabled = True
        self.node.spectro_config['enabled'] = False
        self.assertFalse(self.node._wait_for_new_spectro_sample(None))
        self.node.spectro_config['enabled'] = True
        self.node.spectro_state = 'configured'
        self.assertFalse(self.node._wait_for_new_spectro_sample(None))

    def test_baseline_does_not_survive_restart_config_change_or_reconnect(self):
        for change in ('restart', 'config', 'reconnect'):
            with self.subTest(change=change):
                prepare_measurement(self.node)
                self.assertTrue(self.node._real_measurement_ready())
                if change == 'restart':
                    self.node._spectro_start_ack_at = time.monotonic()
                    fresh_measurement(self.node)
                elif change == 'config':
                    self.node.spectro_config['gain'] = 2
                else:
                    from types import SimpleNamespace
                    self.node.serial_conn = SimpleNamespace(is_open=True)
                self.assertFalse(self.node._real_measurement_ready())

    def test_only_ack_test_frame_stale_frame_or_no_baseline_cannot_admit(self):
        for condition in ('only_ack', 'test', 'stale', 'baseline'):
            with self.subTest(condition=condition):
                prepare_measurement(self.node)
                if condition == 'only_ack':
                    self.node._last_valid_spectro_at = None
                elif condition == 'test':
                    self.node.latest_spectro['simulated'] = True
                elif condition == 'stale':
                    self.node._last_valid_spectro_at -= self.node.measurement_timeout + 1
                else:
                    self.node._clear_spectro_reference()
                self.assertFalse(self.fixture.start()[0])
                self.assertEqual(self.fixture.commands, [])

    def test_late_unowned_ack_does_not_change_acquisition_state(self):
        self.node.spectro_state = 'acquiring'
        original = self.node._spectro_start_ack_at
        self.node._on_text_received('ADS_OK:START')
        self.node._on_text_received('ADS_OK:STOP')
        self.assertEqual(self.node.spectro_state, 'acquiring')
        self.assertEqual(self.node._spectro_start_ack_at, original)

    def test_recovery_allows_waiting_but_never_admits_new_attempt_or_invalid_device(self):
        for invalid in (None, 'disabled', 'baseline', 'simulated', 'fault', 'i2c_error'):
            with self.subTest(invalid=invalid):
                prepare_measurement(self.node)
                now = time.monotonic()
                with patch.object(self.fixture.module.time, 'monotonic', return_value=now + 3):
                    self.node._check_spectro_freshness()
                    if invalid == 'disabled':
                        self.node.spectro_config['enabled'] = False
                    elif invalid == 'baseline':
                        self.node._clear_spectro_reference()
                    elif invalid == 'simulated':
                        self.node.latest_spectro['simulated'] = True
                    elif invalid == 'fault':
                        self.node._controller_fault = 'serial_disconnected'
                    elif invalid == 'i2c_error':
                        self.node.spectro_state = 'i2c_error'
                    self.assertFalse(self.node._real_measurement_ready())
                    self.assertEqual(self.node._real_measurement_ready(allow_recovery=True), invalid is None)
                self.node._controller_fault = None


if __name__ == '__main__':
    unittest.main()
