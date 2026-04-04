#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
USV MAVLink Telemetry Bridge
============================
ROS topics -> MAVLink NAMED_VALUE_FLOAT -> 飞控串口 -> 数传 -> QGC

关键:
  nano 连接飞控后的上发消息默认使用 sysid=1、compid=240，
  与当前飞控 / QGC 联调观测结果保持一致。

使用 mavros 的 /mavlink/to 话题发送原始 MAVLink 帧，MAVROS 负责
序列化并写入 FCU 串口。
"""

from __future__ import print_function

import json
import struct
import threading
import time

import rospy
from std_msgs.msg import String
from mavros_msgs.msg import Mavlink, State

TELEMETRY_RATE_HZ = 2
HEARTBEAT_RATE_HZ = 1
DIAG_REPORT_INTERVAL = 10   # 每 N 秒输出一次链路诊断

MAVLINK_MSG_ID_HEARTBEAT = 0
MAVLINK_MSG_ID_NAMED_VALUE_FLOAT = 251

MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_INVALID = 8
MAV_STATE_ACTIVE = 4

# 默认采用现场验证通过的 nano -> 飞控 MAVLink 源 ID
SYS_ID = 1
COMP_ID = 240


class USVMavlinkBridge(object):

    def __init__(self):
        rospy.init_node('usv_mavlink_bridge', anonymous=False)

        # sysid/compid 默认与现场验证通过的 nano -> 飞控配置一致
        self._sys_id = int(rospy.get_param('~source_system_id', SYS_ID))
        self._comp_id = int(rospy.get_param('~source_component_id', COMP_ID))

        # 运行时参数记录
        fcu_sysid = int(rospy.get_param('/mavros/target_system_id', 1))
        if self._sys_id == fcu_sysid:
            rospy.loginfo("source_system_id (%d) matches FCU sysid (%d) by current deployment design",
                          self._sys_id, fcu_sysid)

        self._lock = threading.Lock()
        self._voltage = 0.0
        self._absorbance = 0.0
        self._pump_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self._status_code = 0
        self._mavros_connected = False
        self._pkt_count = 0
        self._seq = 0
        self._boot_time = time.time()
        self._last_heartbeat = 0.0

        # 诊断计数器
        self._diag_lock = threading.Lock()
        self._diag_tx_total = 0        # 总发送包数
        self._diag_tx_heartbeat = 0    # 心跳包数
        self._diag_tx_named = 0        # NAMED_VALUE_FLOAT 包数
        self._diag_pub_errors = 0      # 发布错误数
        self._diag_mavros_drops = 0    # MAVROS 断连计数
        self._diag_last_report = time.time()

        self._mavlink_pub = rospy.Publisher('/mavros/mavlink/to', Mavlink, queue_size=30)
        # 诊断状态话题 - 供 Web 面板和其他节点消费
        self._diag_pub = rospy.Publisher('/usv/bridge_diagnostics', String, queue_size=5)

        rospy.Subscriber('/usv/spectrometer_voltage', String, self._voltage_cb)
        rospy.Subscriber('/usv/pump_angles', String, self._angles_cb)
        rospy.Subscriber('/usv/pump_status', String, self._pump_status_cb)
        rospy.Subscriber('/usv/trigger_status', String, self._trigger_status_cb)
        rospy.Subscriber('/mavros/state', State, self._mavros_state_cb)

        rospy.loginfo("USV MAVLink Bridge initialized  sysid=%d compid=%d rate=%dHz fcu_sysid=%d",
                      self._sys_id, self._comp_id, TELEMETRY_RATE_HZ, fcu_sysid)

    def _voltage_cb(self, msg):
        try:
            data = json.loads(msg.data)
        except Exception:
            data = {"voltage": 0.0, "absorbance": 0.0}
        with self._lock:
            self._voltage = float(data.get('voltage', data.get('sample_voltage', 0.0)) or 0.0)
            self._absorbance = float(data.get('absorbance', 0.0) or 0.0)

    def _angles_cb(self, msg):
        try:
            parts = msg.data.split(',')
            with self._lock:
                for part in parts:
                    kv = part.split(':')
                    if len(kv) == 2 and kv[0] in self._pump_angles:
                        self._pump_angles[kv[0]] = float(kv[1])
        except Exception as e:
            rospy.logwarn_throttle(10, "parse pump angles: %s", str(e))

    def _pump_status_cb(self, msg):
        data = msg.data.lower()
        with self._lock:
            if "automation: running" in data or "automation: step" in data:
                self._status_code = 1
            elif "automation: finished" in data or "automation: stopped" in data:
                self._status_code = 0
            elif "error" in data:
                self._status_code = 3

    def _trigger_status_cb(self, msg):
        data = msg.data.lower()
        with self._lock:
            if "sampling_started" in data:
                self._status_code = 1
            elif "sampling_stopped" in data:
                self._status_code = 0
            elif "sampling_paused" in data:
                self._status_code = 1
            elif "calibrate" in data:
                self._status_code = 4

    def _mavros_state_cb(self, msg):
        with self._lock:
            prev = self._mavros_connected
            self._mavros_connected = msg.connected
        if prev and not msg.connected:
            rospy.logwarn("MAVROS disconnected")
            with self._diag_lock:
                self._diag_mavros_drops += 1
        elif not prev and msg.connected:
            rospy.loginfo("MAVROS connected")

    def _time_boot_ms(self):
        return int((time.time() - self._boot_time) * 1000) & 0xFFFFFFFF

    def _next_seq(self):
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    def _payload_to_uint64(self, payload):
        r = len(payload) % 8
        if r:
            payload += b'\x00' * (8 - r)
        out = []
        for i in range(0, len(payload), 8):
            out.append(struct.unpack_from('<Q', payload, i)[0])
        return out

    def _publish_raw(self, msgid, payload):
        try:
            m = Mavlink()
            m.header.stamp = rospy.Time.now()
            m.framing_status = 1  # MAVLINK_FRAMING_OK
            m.magic = 253         # MAVLink v2
            m.len = len(payload)
            m.incompat_flags = 0
            m.compat_flags = 0
            m.seq = self._next_seq()
            m.sysid = self._sys_id
            m.compid = self._comp_id
            m.msgid = msgid
            m.checksum = 0
            m.payload64 = self._payload_to_uint64(payload)
            self._mavlink_pub.publish(m)
            with self._diag_lock:
                self._diag_tx_total += 1
                if msgid == MAVLINK_MSG_ID_HEARTBEAT:
                    self._diag_tx_heartbeat += 1
                elif msgid == MAVLINK_MSG_ID_NAMED_VALUE_FLOAT:
                    self._diag_tx_named += 1
        except Exception as e:
            with self._diag_lock:
                self._diag_pub_errors += 1
            rospy.logerr_throttle(10, "publish_raw error: %s", str(e))

    def _send_named_value_float(self, name, value):
        name_bytes = name.encode('ascii')[:10].ljust(10, b'\x00')
        payload = struct.pack('<If', self._time_boot_ms(), value) + name_bytes
        self._publish_raw(MAVLINK_MSG_ID_NAMED_VALUE_FLOAT, payload)

    def _send_heartbeat(self):
        payload = struct.pack(
            '<IBBBBB',
            0,                          # custom_mode
            MAV_TYPE_ONBOARD_CONTROLLER, # type
            MAV_AUTOPILOT_INVALID,       # autopilot
            0,                          # base_mode
            MAV_STATE_ACTIVE,            # system_status
            3,                          # mavlink_version
        )
        self._publish_raw(MAVLINK_MSG_ID_HEARTBEAT, payload)

    def _publish_diagnostics(self):
        """发布结构化诊断信息到 /usv/bridge_diagnostics"""
        now = time.time()
        with self._diag_lock:
            uptime = now - self._boot_time
            diag = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
                "uptime_s": int(uptime),
                "sysid": self._sys_id,
                "compid": self._comp_id,
                "mavros_connected": self._mavros_connected,
                "tx_total": self._diag_tx_total,
                "tx_heartbeat": self._diag_tx_heartbeat,
                "tx_named_value": self._diag_tx_named,
                "pub_errors": self._diag_pub_errors,
                "mavros_drops": self._diag_mavros_drops,
                "pkt_count": self._pkt_count,
                "rate_hz": TELEMETRY_RATE_HZ,
            }
        try:
            msg = String()
            msg.data = json.dumps(diag)
            self._diag_pub.publish(msg)
        except Exception:
            pass

    def run(self):
        rate = rospy.Rate(TELEMETRY_RATE_HZ)
        rospy.loginfo("USV MAVLink Bridge: telemetry loop started")

        while not rospy.is_shutdown():
            with self._lock:
                connected = self._mavros_connected
            if not connected:
                rospy.logwarn_throttle(10, "MAVROS not connected, waiting...")
                # 即使断连也定期发送诊断
                now = time.time()
                if now - self._diag_last_report >= DIAG_REPORT_INTERVAL:
                    self._publish_diagnostics()
                    self._diag_last_report = now
                rate.sleep()
                continue

            with self._lock:
                voltage = self._voltage
                absorbance = self._absorbance
                angles = self._pump_angles.copy()
                status = self._status_code

            now = time.time()
            if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                self._send_heartbeat()
                self._last_heartbeat = now

            self._send_named_value_float("USV_VOLT", voltage)
            self._send_named_value_float("USV_ABS", absorbance)
            self._send_named_value_float("PUMP_X", angles["X"])
            self._send_named_value_float("PUMP_Y", angles["Y"])
            self._send_named_value_float("PUMP_Z", angles["Z"])
            self._send_named_value_float("PUMP_A", angles["A"])
            self._send_named_value_float("USV_STAT", float(status))
            self._pkt_count = (self._pkt_count + 1) % 65536
            self._send_named_value_float("USV_PKT", float(self._pkt_count))

            # 定期诊断报告
            if now - self._diag_last_report >= DIAG_REPORT_INTERVAL:
                self._publish_diagnostics()
                self._diag_last_report = now

            rate.sleep()


def main():
    try:
        bridge = USVMavlinkBridge()
        bridge.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("USV MAVLink Bridge error: %s", str(e))


if __name__ == '__main__':
    main()
