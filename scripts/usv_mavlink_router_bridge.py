#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""USV telemetry bridge via mavlink-router TCP endpoint."""

from __future__ import print_function

import json
import threading
import time

import rospy
from std_msgs.msg import String, Float32MultiArray
from mavros_msgs.msg import State
from pymavlink import mavutil

TELEMETRY_RATE_HZ = 2
HEARTBEAT_RATE_HZ = 1
DIAG_REPORT_INTERVAL = 10
SYS_ID = 1
COMP_ID = 191   # MAV_COMP_ID_ONBOARD_COMPUTER, matches QGC USVPayloadPanel target
ROUTER_URL = "tcp:127.0.0.1:5760"
WAIT_HEARTBEAT_TIMEOUT = 10.0


class USVMavlinkRouterBridge(object):

    def __init__(self):
        rospy.init_node("usv_mavlink_router_bridge", anonymous=False)
        self._sys_id = int(rospy.get_param("~source_system_id", SYS_ID))
        self._comp_id = int(rospy.get_param("~source_component_id", COMP_ID))
        self._router_url = rospy.get_param("~router_url", ROUTER_URL)
        self._wait_heartbeat_timeout = float(rospy.get_param("~wait_heartbeat_timeout", WAIT_HEARTBEAT_TIMEOUT))
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._absorbance = 0.0
        self._pump_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self._status_code = 0
        self._mavros_connected = False
        self._pkt_count = 0
        self._boot_time = time.time()
        self._last_heartbeat = 0.0
        self._diag_last_report = time.time()
        self._diag_pub = rospy.Publisher("/usv/bridge_diagnostics", String, queue_size=5)
        self._cmd_rx_pub = rospy.Publisher("/usv/mavlink_cmd_rx", Float32MultiArray, queue_size=5)
        rospy.Subscriber("/usv/mavlink_cmd_ack", Float32MultiArray, self._cmd_ack_cb)
        rospy.Subscriber("/usv/spectrometer_voltage", String, self._voltage_cb)
        rospy.Subscriber("/usv/pump_angles", String, self._angles_cb)
        rospy.Subscriber("/usv/pump_status", String, self._pump_status_cb)
        rospy.Subscriber("/usv/trigger_status", String, self._trigger_status_cb)
        rospy.Subscriber("/mavros/state", State, self._mavros_state_cb, queue_size=5)
        rospy.loginfo("USV Router Bridge: sysid=%d compid=%d router=%s", self._sys_id, self._comp_id, self._router_url)
        self._conn = mavutil.mavlink_connection(
            self._router_url,
            source_system=self._sys_id,
            source_component=self._comp_id,
            autoreconnect=True,
            force_connected=True,
        )
        self._wait_router_heartbeat()

    def _wait_router_heartbeat(self):
        hb = self._conn.wait_heartbeat(timeout=self._wait_heartbeat_timeout)
        if hb is None:
            rospy.logwarn("Router bridge: no FCU heartbeat within %.1fs, continue anyway", self._wait_heartbeat_timeout)
        else:
            rospy.loginfo("Router bridge: FCU heartbeat detected sys=%s comp=%s", self._conn.target_system, self._conn.target_component)

    def _voltage_cb(self, msg):
        try:
            data = json.loads(msg.data)
        except Exception:
            data = {"voltage": 0.0, "absorbance": 0.0}
        with self._lock:
            self._voltage = float(data.get("voltage", data.get("sample_voltage", 0.0)) or 0.0)
            self._absorbance = float(data.get("absorbance", 0.0) or 0.0)

    def _angles_cb(self, msg):
        try:
            parts = msg.data.split(",")
            with self._lock:
                for part in parts:
                    kv = part.split(":")
                    if len(kv) == 2 and kv[0] in self._pump_angles:
                        self._pump_angles[kv[0]] = float(kv[1])
        except Exception as exc:
            rospy.logwarn_throttle(10, "parse pump angles: %s", str(exc))

    def _pump_status_cb(self, msg):
        data = msg.data.lower()
        with self._lock:
            if "automation: running" in data or "automation: step" in data or "sampling_paused" in data:
                self._status_code = 1
            elif "automation: finished" in data or "automation: stopped" in data or "sampling_stopped" in data:
                self._status_code = 0
            elif "sampling_started" in data:
                self._status_code = 1
            elif "calibrate" in data:
                self._status_code = 4
            elif "error" in data:
                self._status_code = 3

    def _trigger_status_cb(self, msg):
        self._pump_status_cb(msg)

    def _mavros_state_cb(self, msg):
        self._mavros_connected = bool(msg.connected)

    def _cmd_ack_cb(self, msg):
        try:
            command, result, target_sys, target_comp = msg.data
            self._conn.mav.command_ack_send(
                int(command),
                int(result),
                0xFF,  # progress not supported
                0,     # result_param2
                int(target_sys),
                int(target_comp)
            )
        except Exception as exc:
            rospy.logwarn("Failed to send COMMAND_ACK: %s", str(exc))

    def _receive_mavlink_messages(self):
        """非阻塞 drain：消费 pymavlink 接收缓冲，保持协议状态机推进。"""
        while True:
            msg = self._conn.recv_match(blocking=False)
            if not msg:
                break

    def _publish_diagnostics(self):
        msg = String()
        msg.data = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "router_url": self._router_url, "sysid": self._sys_id, "compid": self._comp_id, "pkt_count": self._pkt_count, "mavros_connected": self._mavros_connected, "rate_hz": TELEMETRY_RATE_HZ})
        self._diag_pub.publish(msg)

    def _send_heartbeat(self):
        self._conn.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, mavutil.mavlink.MAV_STATE_ACTIVE)

    def _send_payload(self, voltage, absorbance, angles, status):
        t = int((time.time() - self._boot_time) * 1000) & 0xFFFFFFFF
        self._pkt_count = (self._pkt_count + 1) % 65536
        self._conn.mav.named_value_float_send(t, b"USV_VOLT\x00\x00", voltage)
        self._conn.mav.named_value_float_send(t, b"USV_ABS\x00\x00\x00", absorbance)
        self._conn.mav.named_value_float_send(t, b"PUMP_X\x00\x00\x00\x00", angles["X"])
        self._conn.mav.named_value_float_send(t, b"PUMP_Y\x00\x00\x00\x00", angles["Y"])
        self._conn.mav.named_value_float_send(t, b"PUMP_Z\x00\x00\x00\x00", angles["Z"])
        self._conn.mav.named_value_float_send(t, b"PUMP_A\x00\x00\x00\x00", angles["A"])
        self._conn.mav.named_value_float_send(t, b"USV_STAT\x00\x00", float(status))
        self._conn.mav.named_value_float_send(t, b"USV_PKT\x00\x00\x00", float(self._pkt_count))

    def run(self):
        rate = rospy.Rate(TELEMETRY_RATE_HZ)
        while not rospy.is_shutdown():
            # 命令接收始终运行，不依赖 MAVROS 连接状态
            self._receive_mavlink_messages()

            now = time.time()
            if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                self._send_heartbeat()
                self._last_heartbeat = now

            if self._mavros_connected:
                with self._lock:
                    voltage = self._voltage
                    absorbance = self._absorbance
                    angles = self._pump_angles.copy()
                    status = self._status_code
                self._send_payload(voltage, absorbance, angles, status)
            else:
                rospy.logwarn_throttle(10, "MAVROS not connected, telemetry paused (cmd rx active)")

            if now - self._diag_last_report >= DIAG_REPORT_INTERVAL:
                self._publish_diagnostics()
                self._diag_last_report = now
            rate.sleep()


def main():
    try:
        USVMavlinkRouterBridge().run()
    except rospy.ROSInterruptException:
        pass
    except Exception as exc:
        rospy.logerr("USV Router Bridge error: %s", str(exc))


if __name__ == "__main__":
    main()
