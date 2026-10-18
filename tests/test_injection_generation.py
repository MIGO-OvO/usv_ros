"""Queued injection work must expire before the actual hardware write."""
import threading
import unittest
from unittest.mock import Mock

import test_field_transactions as field_fixture


class InjectionGenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = field_fixture.FieldTransactionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.node = self.fixture.node
        self.ports = self.fixture.serial_environment()
        self.node._apply_runtime_configuration = Mock(return_value=True)
        self.assertTrue(self.node.connect())
        self.entered = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()
        self.addCleanup(self.release.set)
        dispatch = self.node.injection_pump_worker._send_command

        def delayed(command, *args, **kwargs):
            if command == 'PUMP:ON':
                self.entered.set()
                if not self.release.wait(5):
                    raise AssertionError('test did not release the paused write')
            try:
                return dispatch(command, *args, **kwargs)
            finally:
                if command == 'PUMP:ON':
                    self.completed.set()
        self.node.injection_pump_worker._send_command = delayed

    def queue_on(self):
        self.assertTrue(self.node.injection_pump_worker.submit('PUMP:ON', enabled=True, wait=False))
        self.assertTrue(self.entered.wait(1))

    def release_on(self):
        self.release.set()
        self.assertTrue(self.completed.wait(1))

    def test_dequeued_on_cannot_cross_reconnect(self):
        self.queue_on()
        self.assertTrue(self.node._reconnect_callback(None).success)
        self.release_on()
        self.assertEqual(len(self.ports), 2)
        self.assertFalse(any(b'PUMP:ON' in data for _, data, _ in self.ports[1].writes))
        self.assertFalse(self.node.inject_pump_enabled)

    def test_dequeued_on_cannot_follow_stop(self):
        self.queue_on()
        self.node._auto_stop_callback(None)
        self.release_on()
        commands = [data.strip() for _, data, _ in self.ports[0].writes]
        self.assertIn(b'STOPALL', commands)
        self.assertNotIn(b'PUMP:ON', commands)

    def test_timed_out_on_is_not_sent_later(self):
        result = []
        caller = threading.Thread(target=lambda: result.append(
            self.node.injection_pump_worker.submit('PUMP:ON', enabled=True, timeout=0.05)))
        caller.start()
        self.assertTrue(self.entered.wait(1))
        caller.join(1)
        self.assertEqual(result, [False])
        self.release_on()
        self.assertFalse(any(b'PUMP:ON' in data for _, data, _ in self.ports[0].writes))


if __name__ == '__main__':
    unittest.main()
