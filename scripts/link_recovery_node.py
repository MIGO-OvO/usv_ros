#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover the launch-managed MAVROS process without restarting payload nodes."""

import json
import subprocess
import threading
import time

import rospy
from mavros_msgs.msg import State
from std_msgs.msg import String
from std_srvs.srv import Trigger, TriggerResponse


class LinkRecoveryNode:
    def __init__(self):
        rospy.init_node('link_recovery_node', anonymous=False)
        self.enabled = bool(rospy.get_param('~restart_enabled', True))
        self.disconnect_timeout = max(5.0, float(rospy.get_param('~disconnect_timeout', 30.0)))
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._state_at = None
        self._connected = False
        self._unhealthy_since = self._started_at
        self._pending = False
        self._next_retry = self._started_at
        self._backoff = 30.0
        self._attempts = 0
        self._phase = 'waiting'
        self._message = '等待 MAVROS 状态'
        self._last_error = ''
        self._reason = ''
        self._pub = rospy.Publisher('/usv/link_recovery', String, queue_size=1, latch=True)
        rospy.Subscriber('/mavros/state', State, self._state_cb, queue_size=5)
        rospy.Subscriber('/usv/link_recovery/request', String, self._request_cb, queue_size=1)
        self._service = rospy.Service('/usv/link_reconnect', Trigger, self._reconnect_cb)

    def _healthy(self, now):
        return self._connected and self._state_at is not None and 0 <= now - self._state_at <= 5.0

    def _state_cb(self, msg):
        with self._lock:
            self._connected = bool(msg.connected)
            self._state_at = time.monotonic()
            if self._connected:
                self._unhealthy_since = None
            elif self._unhealthy_since is None:
                self._unhealthy_since = self._state_at

    def _request(self, reason):
        with self._lock:
            if not self.enabled:
                return TriggerResponse(False, 'MAVROS 自动重启未启用，请检查 mavros_respawn')
            if self._healthy(time.monotonic()):
                return TriggerResponse(True, 'MAVROS 已连接')
            self._pending = True
            self._reason = reason
            return TriggerResponse(True, '已请求重连 MAVROS，等待飞控心跳')

    def _request_cb(self, msg):
        self._request('ground_station')

    def _reconnect_cb(self, request):
        return self._request('web')

    def _restart_mavros(self):
        # roslaunch owns respawn. A bounded child keeps a wedged XML-RPC call
        # out of the bridge/STOP loop and does not touch the FCU or pumps.
        with self._lock:
            if self._healthy(time.monotonic()):
                return False
        result = subprocess.run(['rosnode', 'kill', '/mavros'], capture_output=True,
                                text=True, timeout=4.0)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[:300] or 'rosnode kill failed')
        return True

    def step(self):
        now = time.monotonic()
        restart = False
        with self._lock:
            if self._healthy(now):
                self._unhealthy_since = None
                self._pending = False
                self._backoff = 30.0
                self._phase, self._message = 'connected', 'MAVROS 已连接'
                self._last_error = ''
            else:
                if self._unhealthy_since is None:
                    self._unhealthy_since = self._state_at + 5.0 if self._state_at is not None else self._started_at
                due = self._pending or now - self._unhealthy_since >= self.disconnect_timeout
                if not self.enabled:
                    self._phase, self._message = 'disabled', 'MAVROS 自动重启未启用'
                elif now < self._next_retry:
                    self._phase, self._message = 'retry_wait', '等待下一次重连'
                elif due:
                    self._reason = self._reason if self._pending else 'mavros_state_timeout'
                    self._pending = False
                    self._attempts += 1
                    self._next_retry = now + self._backoff
                    self._backoff = min(120.0, self._backoff * 2)
                    self._phase, self._message = 'reconnecting', '正在重连 MAVROS'
                    restart = True
                else:
                    self._phase, self._message = 'disconnected', '等待 MAVROS 恢复飞控心跳'
        if restart:
            self._publish()
            try:
                requested = self._restart_mavros()
                with self._lock:
                    if requested is False:
                        self._phase, self._message = 'connected', 'MAVROS 已连接'
                    else:
                        self._phase, self._message = 'waiting_heartbeat', '已请求重启 MAVROS，等待飞控心跳'
                    self._last_error = ''
                rospy.logwarn('MAVROS recovery attempt=%d reason=%s', self._attempts, self._reason)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                with self._lock:
                    self._phase, self._message = 'failed', 'MAVROS 重连请求失败'
                    self._last_error = str(exc)
                rospy.logwarn('MAVROS recovery failed: %s', exc)
        self._publish()

    def _publish(self):
        now = time.monotonic()
        with self._lock:
            data = {'source_monotonic': now, 'enabled': self.enabled, 'state': self._phase,
                    'message': self._message, 'connected': self._healthy(now),
                    'state_age_s': now - self._state_at if self._state_at is not None else None,
                    'attempts': self._attempts, 'reason': self._reason, 'last_error': self._last_error,
                    'retry_in_s': max(0.0, self._next_retry - now)}
        msg = String()
        msg.data = json.dumps(data)
        self._pub.publish(msg)

    def run(self):
        while not rospy.is_shutdown():
            self.step()
            time.sleep(1.0)


if __name__ == '__main__':
    try:
        LinkRecoveryNode().run()
    except rospy.ROSInterruptException:
        pass
