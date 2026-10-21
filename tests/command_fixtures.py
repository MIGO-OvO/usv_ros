"""Bounded waits for the real asynchronous MAVLink command lanes."""
import time
from types import SimpleNamespace


def remote_command(node, command, param1=0, param2=0):
    """A current protocol client, with a new durable identity for each request."""
    node._mavlink_tcp_session = node._mavlink_tcp_session or 1
    node._test_request_id = getattr(node, '_test_request_id', 0) + 1
    return SimpleNamespace(executor=node._executor_id, data=[command, param1, param2, 1, 191, 255, 190,
                                 node._test_request_id, 1, node._mavlink_tcp_session])


def wait_commands(node, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with node._command_dispatch_lock:
            if not any(lane['busy'] for lane in node._command_lanes.values()):
                return
        time.sleep(0.001)
    raise AssertionError('MAVLink command worker did not finish')
