#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
USV MAVLink Telemetry Bridge
============================
ROS topics -> 多通道 MAVLink -> 飞控串口 -> 数传 -> QGC

消息通道策略 (ArduPilot 白名单转发):
  NAMED_VALUE_FLOAT (251) - 电压/吸光度 (保留，QGC MAVLink Inspector 可直读)
  STATUSTEXT        (253) - 状态码文本   (白名单，QGC 主 HUD 直接显示)
  DEBUG_VECT        (254) - 泵角度 xyz   (白名单，单帧携带 3 个 float)
  DEBUG             (255) - 包计数/泵A   (白名单，ind + value)

sysid=1 compid=240，与 MAVROS 默认配置一致。
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

# MAVLink 消息 ID
MAVLINK_MSG_ID_HEARTBEAT = 0
MAVLINK_MSG_ID_NAMED_VALUE_FLOAT = 251
MAVLINK_MSG_ID_STATUSTEXT = 253
MAVLINK_MSG_ID_DEBUG_VECT = 250
MAVLINK_MSG_ID_DEBUG = 254

# MAVLink CRC_EXTRA seed bytes (per-message type, from MAVLink XML definitions)
# MAVROS send_message(mavlink_message_t*) does NOT recalculate CRC,
# so we must provide the correct checksum ourselves.
MAVLINK_CRC_EXTRA = {
    0: 50,      # HEARTBEAT
    250: 49,    # DEBUG_VECT
    251: 170,   # NAMED_VALUE_FLOAT
    253: 83,    # STATUSTEXT
    254: 46,    # DEBUG
}

MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_INVALID = 8
MAV_STATE_ACTIVE = 4

# STATUSTEXT severity
MAV_SEVERITY_NOTICE = 6

# 状态码 -> 可读文本映射
STATUS_TEXT_MAP = {
    0: "IDLE",
    1: "SAMPLING",
    2: "DETECTING",
    3: "FAULT",
    4: "CALIBRATING",
}

SYS_ID = 1
COMP_ID = 240


class USVMavlinkBridge(object):

    def __init__(self):
        rospy.init_node('usv_mavlink_bridge', anonymous=False)

        self._sys_id = int(rospy.get_param('~source_system_id', SYS_ID))
        self._comp_id = int(rospy.get_param('~source_component_id', COMP_ID))

        rospy.loginfo("USV MAVLink Bridge: sysid=%d compid=%d", self._sys_id, self._comp_id)

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

        rospy.loginfo("USV MAVLink Bridge initialized  sysid=%d compid=%d rate=%dHz",
                      self._sys_id, self._comp_id, TELEMETRY_RATE_HZ)

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

    @staticmethod
    def _mavlink_crc16(buf):
        """MAVLink X.25 CRC-16/MCRF4XX checksum."""
        crc = 0xFFFF
        for b in buf:
            tmp = b ^ (crc & 0xFF)
            tmp = (tmp ^ (tmp << 4)) & 0xFF
            crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
            crc &= 0xFFFF
        return crc

    def _compute_checksum(self, msgid, seq, payload):
        """Compute MAVLink v2 checksum over header+payload+CRC_EXTRA."""
        crc_extra = MAVLINK_CRC_EXTRA.get(msgid, 0)
        # MAVLink v2 CRC covers: len, incompat, compat, seq, sysid, compid,
        # msgid (3 bytes LE), payload, then CRC_EXTRA seed
        header = struct.pack(
            '<BBBBBBBBB',
            len(payload),       # payload length
            0,                  # incompat_flags
            0,                  # compat_flags
            seq,
            self._sys_id,
            self._comp_id,
            msgid & 0xFF,
            (msgid >> 8) & 0xFF,
            (msgid >> 16) & 0xFF,
        )
        return self._mavlink_crc16(header + payload + struct.pack('B', crc_extra))

    def _publish_raw(self, msgid, payload):
        try:
            seq = self._next_seq()
            m = Mavlink()
            m.header.stamp = rospy.Time.now()
            m.framing_status = 1  # MAVLINK_FRAMING_OK
            m.magic = 253         # MAVLink v2
            m.len = len(payload)
            m.incompat_flags = 0
            m.compat_flags = 0
            m.seq = seq
            m.sysid = self._sys_id
            m.compid = self._comp_id
            m.msgid = msgid
            m.checksum = self._compute_checksum(msgid, seq, payload)
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

    def _send_statustext(self, text, severity=MAV_SEVERITY_NOTICE):
        """STATUSTEXT (253) - 白名单消息，QGC HUD 直接显示。
        载荷: severity(u8) + text(50 bytes, NUL padded) + id(u16) + chunk_seq(u8)
        """
        text_bytes = text.encode('ascii')[:50].ljust(50, b'\x00')
        payload = struct.pack('<B', severity) + text_bytes + struct.pack('<HB', 0, 0)
        self._publish_raw(MAVLINK_MSG_ID_STATUSTEXT, payload)

    def _send_debug_vect(self, name, x, y, z):
        """DEBUG_VECT (254) - 白名单消息，用于泵角度 (3 float / 帧)。
        载荷: time_usec(u64) + x(f32) + y(f32) + z(f32) + name(10 bytes)
        """
        name_bytes = name.encode('ascii')[:10].ljust(10, b'\x00')
        time_us = int((time.time() - self._boot_time) * 1e6) & 0xFFFFFFFFFFFFFFFF
        payload = struct.pack('<Qfff', time_us, x, y, z) + name_bytes
        self._publish_raw(MAVLINK_MSG_ID_DEBUG_VECT, payload)

    def _send_debug(self, ind, value):
        """DEBUG (255) - 白名单消息，用于包计数等单值调试。
        载荷: time_boot_ms(u32) + value(f32) + ind(u8)
        """
        payload = struct.pack('<IfB', self._time_boot_ms(), value, ind)
        self._publish_raw(MAVLINK_MSG_ID_DEBUG, payload)

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
        rospy.loginfo("USV MAVLink Bridge: telemetry loop started at %dHz", TELEMETRY_RATE_HZ)

        while not rospy.is_shutdown():
            with self._lock:
                connected = self._mavros_connected
            if not connected:
                rospy.logwarn_throttle(10, "MAVROS not connected, waiting...")
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

            # 全部通过 NAMED_VALUE_FLOAT (251) 发送
            # 与 SITL 测试脚本 test_sitl_usv.py 保持一致
            # 飞控固件 handle_message 只拦截 NAMED_VALUE_FLOAT
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
