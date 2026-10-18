"""Calibration needs all axis results and an explicit stop, not just CAL_START."""
import json
import time
import unittest

import test_automation_preflight as preparation
from test_mavlink_command_compat import _load_script


class CalibrationLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixture = preparation.PreflightWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.node = fixture.node
        self.commands = []
        self.node.send_command = fixture.module.PumpControlNode.send_command.__get__(self.node)
        self.node.serial_conn.write = self.write

    def write(self, raw):
        command = raw.decode().strip()
        self.commands.append(command)
        if command == 'CALSTATUS':
            self.node._on_text_received('CAL_STATUS:')
        elif command == 'CALXYZA':
            self.node._on_text_received('CAL_START:mask=0xF')
        return len(raw)

    def start(self):
        success, _, _ = self.node._execute_control_action('calibration_start', {})
        self.assertTrue(success)

    def test_active_calibration_blocks_all_new_motion_but_stop_is_available(self):
        self.start()
        for action in ('automation_start', 'injection_on', 'calibration_start', 'manual_mode'):
            self.assertFalse(self.node._execute_control_action(action, {})[0])
        self.assertFalse(self.node.send_command('XEFV10'))
        self.assertFalse(self.node._auto_start_callback(None).success)
        self.assertTrue(self.node._request_output_stop())
        self.node._finish_output_stop()
        self.assertEqual(self.commands[-1], 'STOPALL')
        self.assertEqual(self.node._calibration['outcome'], 'cancelled')

    def test_all_axis_done_complete_and_stopped_required(self):
        self.start()
        for axis in 'XYZA':
            self.node._on_text_received('CAL_DONE:' + axis + '=0.00')
        self.node._on_text_received('CAL_COMPLETE:ALL_DONE')
        self.assertTrue(self.node._calibration['active'])
        self.node._on_text_received('CAL_STOPPED')
        self.assertFalse(self.node._calibration['active'])
        self.assertEqual(self.node._calibration['outcome'], 'succeeded')

    def test_axis_failure_cannot_be_erased_by_firmware_complete(self):
        self.start()
        self.node._on_text_received('CAL_FAIL:X=SENSOR_ERR')
        for axis in 'YZA':
            self.node._on_text_received('CAL_DONE:' + axis + '=0.00')
        self.node._on_text_received('CAL_COMPLETE:ALL_DONE')
        self.node._on_text_received('CAL_STOPPED')
        self.assertEqual(self.node._calibration['outcome'], 'failed')
        self.assertEqual(self.node._calibration['reason'], 'CAL_FAIL:X=SENSOR_ERR')
        self.assertTrue(self.node._controller_fault)

    def test_timeout_sends_stop_and_late_success_does_not_revive(self):
        self.start()
        self.node._calibration['deadline'] = time.monotonic() - 1
        self.node._check_calibration()
        self.assertEqual(self.commands[-2:], ['STOPALL', 'CALSTOP'])
        self.node._on_text_received('CAL_COMPLETE:ALL_DONE')
        self.node._on_text_received('CAL_STOPPED')
        self.assertEqual(self.node._calibration['reason'], 'calibration_timeout')
        self.assertEqual(self.node._calibration['outcome'], 'failed')

    def test_bridge_keeps_calibration_status_over_idle_mission_heartbeat(self):
        module = _load_script('calibration_bridge', 'scripts/usv_mavlink_router_bridge.py')
        bridge = module.USVMavlinkRouterBridge()
        self.start()
        payload = {'status': 'idle', 'source_monotonic': time.monotonic(),
                   'calibration': self.node._calibration}
        message = module.String()
        message.data = json.dumps(payload)
        bridge._automation_status_cb(message)
        message.data = 'IDLE'
        bridge._mission_status_cb(message)
        self.assertEqual(bridge._status_code, 4)


if __name__ == '__main__':
    unittest.main()
