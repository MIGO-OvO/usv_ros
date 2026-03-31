#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
USV MAVLink Telemetry Bridge (载荷遥测桥接节点)
=================================================
将 ROS 载荷数据转为 MAVLink NAMED_VALUE_FLOAT 消息回传给 QGC。

功能:
  - 订阅 ROS 载荷话题 (泵角度、电压、状态)
  - 转发为 MAVLink NAMED_VALUE_FLOAT
  - 通过 MAVROS 的 /mavros/mavlink/to 话题发送

QGC 侧 USVPayloadFactGroup 期望的遥测名:
  - USV_VOLT   : 分光检测器电压
  - USV_ABS    : 吸光度 (本阶段占位)
  - PUMP_X     : 泵X角度
  - PUMP_Y     : 泵Y角度
  - PUMP_Z     : 泵Z角度
  - PUMP_A     : 泵A角度
  - USV_STAT   : 载荷状态码 (0=idle 1=sampling 2=detecting 3=fault 4=calibrating)

Target: Jetson Nano
ROS: Noetic
Python: 3.8
"""

from __future__ import print_function

import json
import struct
import threading
import time

import rospy
from std_msgs.msg import String
from mavros_msgs.msg import Mavlink, State

# MAVLink 常量
MAVLINK_MSG_ID_NAMED_VALUE_FLOAT = 251
# NAMED_VALUE_FLOAT 载荷: time_boot_ms(uint32) + value(float32) + name(char[10]) = 18 bytes

# 遥测发送频率 (Hz)
TELEMETRY_RATE_HZ = 2


class USVMavlinkBridge(object):
    """将 ROS 载荷数据桥接为 MAVLink NAMED_VALUE_FLOAT 消息。"""

    def __init__(self):
        rospy.init_node('usv_mavlink_bridge', anonymous=False)

        # 数据缓存 (线程安全)
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._absorbance = 0.0
        self._pump_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self._status_code = 0  # 0=idle
        self._mavros_connected = False
        self._pkt_count = 0

        # 启动时间基准 (用于 time_boot_ms)
        self._boot_time = time.time()

        # Publisher: 通过 MAVROS 发送原始 MAVLink 消息
        self._mavlink_pub = rospy.Publisher(
            '/mavros/mavlink/to', Mavlink, queue_size=20
        )

        # Subscribers
        rospy.Subscriber('/usv/spectrometer_voltage', String, self._voltage_cb)
        rospy.Subscriber('/usv/pump_angles', String, self._angles_cb)
        rospy.Subscriber('/usv/pump_status', String, self._pump_status_cb)
        rospy.Subscriber('/usv/trigger_status', String, self._trigger_status_cb)
        rospy.Subscriber('/mavros/state', State, self._mavros_state_cb)

        rospy.loginfo("USV MAVLink Bridge initialized")
        rospy.loginfo("  Telemetry rate: %d Hz", TELEMETRY_RATE_HZ)

    # ==================== ROS 数据订阅 ====================

    def _voltage_cb(self, msg):
        """解析分光 JSON 数据。"""
        try:
            data = json.loads(msg.data)
        except Exception:
            data = {"voltage": 0.0, "absorbance": 0.0, "raw": msg.data}

        with self._lock:
            self._voltage = float(data.get('voltage', data.get('sample_voltage', 0.0)) or 0.0)
            self._absorbance = float(data.get('absorbance', 0.0) or 0.0)

    def _angles_cb(self, msg):
        """解析泵角度字符串: 'X:123.456,Y:78.901,Z:0.000,A:45.678'"""
        try:
            parts = msg.data.split(',')
            with self._lock:
                for part in parts:
                    kv = part.split(':')
                    if len(kv) == 2 and kv[0] in self._pump_angles:
                        self._pump_angles[kv[0]] = float(kv[1])
        except Exception as e:
            rospy.logwarn_throttle(10, "Failed to parse pump angles: %s", str(e))

    def _pump_status_cb(self, msg):
        """从泵状态推断载荷状态码。"""
        data = msg.data.lower()
        with self._lock:
            if "automation: running" in data or "automation: step" in data:
                self._status_code = 1  # sampling
            elif "automation: finished" in data or "automation: stopped" in data:
                self._status_code = 0  # idle
            elif "error" in data:
                self._status_code = 3  # fault

    def _trigger_status_cb(self, msg):
        """从触发节点状态推断载荷状态码。"""
        data = msg.data.lower()
        with self._lock:
            if "sampling_started" in data:
                self._status_code = 1
            elif "sampling_stopped" in data:
                self._status_code = 0
            elif "sampling_paused" in data:
                self._status_code = 1  # 暂停仍算 sampling 状态
            elif "calibrate" in data:
                self._status_code = 4

    def _mavros_state_cb(self, msg):
        with self._lock:
            prev = self._mavros_connected
            self._mavros_connected = msg.connected
        if prev and not msg.connected:
            rospy.logwarn("MAVROS disconnected, pausing telemetry")
        elif not prev and msg.connected:
            rospy.loginfo("MAVROS reconnected, resuming telemetry")

    # ==================== MAVLink 打包与发送 ====================

    def _get_time_boot_ms(self):
        """获取自启动以来的毫秒数。"""
        return int((time.time() - self._boot_time) * 1000) & 0xFFFFFFFF

    def _build_named_value_float(self, name, value):
        """
        构建 NAMED_VALUE_FLOAT 的 payload 字节。

        载荷格式 (18 bytes, little-endian):
          offset 0: time_boot_ms  uint32
          offset 4: value         float32
          offset 8: name          char[10]
        """
        time_ms = self._get_time_boot_ms()
        # name 字段固定 10 字节, 不足补 \0
        name_bytes = name.encode('ascii')[:10].ljust(10, b'\x00')
        payload = struct.pack('<If', time_ms, value) + name_bytes
        return payload

    def _payload_to_uint64_list(self, payload):
        """将字节载荷转为 uint64 列表 (8 字节对齐)。"""
        # 补齐到 8 的倍数
        remainder = len(payload) % 8
        if remainder:
            payload += b'\x00' * (8 - remainder)
        result = []
        for i in range(0, len(payload), 8):
            val = struct.unpack_from('<Q', payload, i)[0]
            result.append(val)
        return result

    def _send_named_value_float(self, name, value):
        """发送一条 NAMED_VALUE_FLOAT 消息。"""
        payload = self._build_named_value_float(name, value)

        mavlink_msg = Mavlink()
        mavlink_msg.header.stamp = rospy.Time.now()
        mavlink_msg.framing_status = 1  # MAVLINK_FRAMING_OK
        mavlink_msg.magic = 253  # MAVLink v2
        mavlink_msg.len = len(payload)
        mavlink_msg.sysid = 1
        mavlink_msg.compid = 191  # MAV_COMP_ID_ONBOARD_COMPUTER
        mavlink_msg.msgid = MAVLINK_MSG_ID_NAMED_VALUE_FLOAT
        mavlink_msg.payload64 = self._payload_to_uint64_list(payload)

        self._mavlink_pub.publish(mavlink_msg)

    # ==================== 主循环 ====================

    def run(self):
        """定时发送遥测数据。"""
        rate = rospy.Rate(TELEMETRY_RATE_HZ)

        rospy.loginfo("USV MAVLink Bridge: starting telemetry loop")

        while not rospy.is_shutdown():
            with self._lock:
                connected = self._mavros_connected
            if not connected:
                rospy.logwarn_throttle(10, "MAVROS not connected, skipping telemetry")
                rate.sleep()
                continue

            with self._lock:
                voltage = self._voltage
                absorbance = self._absorbance
                angles = self._pump_angles.copy()
                status = self._status_code

            # 发送所有遥测值
            self._send_named_value_float("USV_VOLT", voltage)
            self._send_named_value_float("USV_ABS", absorbance)
            self._send_named_value_float("PUMP_X", angles["X"])
            self._send_named_value_float("PUMP_Y", angles["Y"])
            self._send_named_value_float("PUMP_Z", angles["Z"])
            self._send_named_value_float("PUMP_A", angles["A"])
            self._send_named_value_float("USV_STAT", float(status))
            self._pkt_count = (self._pkt_count + 1) % 65536
            self._send_named_value_float("USV_PKT", float(self._pkt_count))

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
