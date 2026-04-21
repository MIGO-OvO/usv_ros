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

# USV 自定义命令范围
CMD_START_SAMPLING = 31010
CMD_CALIBRATE = 31014


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
        self._automation_step = 0.0
        self._automation_total = 0.0
        self._sample_count = 0.0
        self._pid_error = 0.0
        self._pid_mode = 0.0
        self._boot_time = time.time()
        self._last_heartbeat = 0.0
        self._diag_last_report = time.time()
        self._diag_pub = rospy.Publisher("/usv/bridge_diagnostics", String, queue_size=5)
        self._diag_tx_total = 0        # 总发送包数
        self._diag_tx_heartbeat = 0    # 心跳包数
        self._diag_tx_named = 0        # NAMED_VALUE_FLOAT 包数
        self._diag_pub_errors = 0      # 发布错误数
        self._diag_mavros_drops = 0    # MAVROS 断连计数
        # pymavlink TCP 连接非线程安全：
        # ROS 回调线程不直接操作 self._conn，只把待发送项入队，
        # 由 run() 主循环统一发送，避免点击采样后桥接卡死。
        self._pending_statustexts = []   # [(text, severity), ...]
        self._pending_acks = []          # [(command, result, target_sys, target_comp), ...]
        self._cmd_rx_pub = rospy.Publisher("/usv/mavlink_cmd_rx", Float32MultiArray, queue_size=5)
        self._radio_status_pub = rospy.Publisher("/usv/radio_status", String, queue_size=5)
        rospy.Subscriber("/usv/mavlink_cmd_ack", Float32MultiArray, self._cmd_ack_cb)
        rospy.Subscriber("/usv/spectrometer_voltage", String, self._voltage_cb)
        rospy.Subscriber("/usv/pump_angles", String, self._angles_cb)
        rospy.Subscriber("/usv/pump_status", String, self._pump_status_cb)
        rospy.Subscriber("/usv/trigger_status", String, self._trigger_status_cb)
        rospy.Subscriber("/usv/mission_status", String, self._mission_status_cb)
        rospy.Subscriber("/usv/pump_pid_error", String, self._pid_error_cb)
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
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            # 兼容旧的 "X:1.23,Y:4.56" 格式
            data = {}
            try:
                for part in msg.data.split(","):
                    kv = part.split(":")
                    if len(kv) == 2:
                        data[kv[0].strip()] = float(kv[1])
            except Exception:
                pass
        with self._lock:
            for motor in ("X", "Y", "Z", "A"):
                if motor in data:
                    self._pump_angles[motor] = float(data[motor])

    def _pump_status_cb(self, msg):
        try:
            data_dict = json.loads(msg.data)
        except Exception:
            data_dict = {}

        with self._lock:
            if isinstance(data_dict, dict):
                self._automation_step = float(data_dict.get("automation_step", 0.0) or 0.0)
                self._automation_total = float(data_dict.get("automation_total", 0.0) or 0.0)
                pid_mode_str = str(data_dict.get("pid_mode", "idle")).lower()
                if "running" in pid_mode_str:
                    self._pid_mode = 1.0
                elif "done" in pid_mode_str:
                    self._pid_mode = 2.0
                elif "error" in pid_mode_str:
                    self._pid_mode = 3.0
                else:
                    self._pid_mode = 0.0
            # 注意：_status_code 现在完全由 _mission_status_cb 驱动，此处不再覆盖。

    def _trigger_status_cb(self, msg):
        data = msg.data.lower()
        with self._lock:
            if "sampling_started" in data:
                self._sample_count = (self._sample_count + 1) % 65536
                self._pending_statustexts.append(("USV: Sampling Started", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif "sampling_stopped" in data:
                self._pending_statustexts.append(("USV: Sampling Completed", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif "sampling_paused" in data:
                self._pending_statustexts.append(("USV: Sampling Paused", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif "calibrate" in data:
                self._pending_statustexts.append(("USV: Calibrating", mavutil.mavlink.MAV_SEVERITY_NOTICE))

    # 任务阶段 → USV_STAT 扩展编码映射
    _MISSION_STATE_CODES = {
        "IDLE": 0, "NAVIGATING": 5, "WAYPOINT_REACHED": 6,
        "HOLDING": 7, "WAITING_STABLE": 8, "SAMPLING": 1,
        "SAMPLING_DONE": 9, "RESUMING_AUTO": 10,
        "HOLD_NO_MISSION": 13, "FAILED": 3, "PAUSED": 11,
        "ABORTED": 12, "DETECTING": 2, "CALIBRATING": 4,
    }

    def _mission_status_cb(self, msg):
        """从 /usv/mission_status 解析阶段并更新 USV_STAT 编码。"""
        raw = (msg.data or "").strip()
        state = raw.split(":")[0].upper() if raw else "IDLE"
        code = self._MISSION_STATE_CODES.get(state)
        if code is not None:
            with self._lock:
                self._status_code = code

    def _pid_error_cb(self, msg):
        try:
            data = json.loads(msg.data)
            with self._lock:
                self._pid_error = float(data.get("error", 0.0) or 0.0)
        except Exception:
            pass

    def _mavros_state_cb(self, msg):
        was_connected = self._mavros_connected
        self._mavros_connected = bool(msg.connected)
        if was_connected and not self._mavros_connected:
            with self._lock:
                self._diag_mavros_drops += 1

    def _cmd_ack_cb(self, msg):
        try:
            command, result, target_sys, target_comp = msg.data
            with self._lock:
                self._pending_acks.append((
                    int(command),
                    int(result),
                    int(target_sys),
                    int(target_comp),
                ))
        except Exception as exc:
            rospy.logwarn("Failed to queue COMMAND_ACK: %s", str(exc))

    def _send_statustext(self, text, severity):
        try:
            self._conn.mav.statustext_send(severity, text.encode('utf-8'))
            with self._lock:
                self._diag_tx_total += 1
        except Exception as exc:
            with self._lock:
                self._diag_pub_errors += 1
            rospy.logwarn("Failed to send STATUSTEXT: %s", str(exc))

    def _send_command_ack(self, command, result, target_sys, target_comp):
        try:
            self._conn.mav.command_ack_send(
                int(command),
                int(result),
                0xFF,
                0,
                int(target_sys),
                int(target_comp)
            )
            with self._lock:
                self._diag_tx_total += 1
        except Exception as exc:
            with self._lock:
                self._diag_pub_errors += 1
            rospy.logwarn("Failed to send COMMAND_ACK: %s", str(exc))

    def _receive_mavlink_messages(self):
        """接收 QGC 发来的 COMMAND_LONG，并兼容转发到旧内部总线。"""
        while True:
            msg = self._conn.recv_match(blocking=False)
            if not msg:
                break

            msg_type = msg.get_type()
            if msg_type == "BAD_DATA":
                continue

            # RADIO_STATUS: 电台链路质量数据
            if msg_type == "RADIO_STATUS":
                try:
                    radio_msg = String()
                    radio_msg.data = json.dumps({
                        "rssi": int(getattr(msg, "rssi", 0)),
                        "remrssi": int(getattr(msg, "remrssi", 0)),
                        "noise": int(getattr(msg, "noise", 0)),
                        "remnoise": int(getattr(msg, "remnoise", 0)),
                        "rxerrors": int(getattr(msg, "rxerrors", 0)),
                        "fixed": int(getattr(msg, "fixed", 0)),
                        "txbuf": int(getattr(msg, "txbuf", 0)),
                    })
                    self._radio_status_pub.publish(radio_msg)
                except Exception:
                    pass
                continue

            if msg_type != "COMMAND_LONG":
                continue

            try:
                command = int(msg.command)
                if command < CMD_START_SAMPLING or command > CMD_CALIBRATE:
                    continue

                target_system = int(getattr(msg, "target_system", 0) or 0)
                target_component = int(getattr(msg, "target_component", 0) or 0)
                ack_target_system = int(getattr(msg, "get_srcSystem", lambda: 0)())
                ack_target_component = int(getattr(msg, "get_srcComponent", lambda: 0)())

                rx_msg = Float32MultiArray()
                rx_msg.data = [
                    float(command),
                    float(getattr(msg, "param1", 0.0) or 0.0),
                    float(getattr(msg, "param2", 0.0) or 0.0),
                    float(target_system),
                    float(target_component),
                    float(ack_target_system),
                    float(ack_target_component),
                ]
                self._cmd_rx_pub.publish(rx_msg)

                rospy.loginfo(
                    "Forward COMMAND_LONG cmd=%d target=%d/%d from=%d/%d",
                    command,
                    target_system,
                    target_component,
                    ack_target_system,
                    ack_target_component,
                )
            except Exception as exc:
                rospy.logwarn("Failed to forward COMMAND_LONG: %s", str(exc))

    def _publish_diagnostics(self):
        now = time.time()
        with self._lock:
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
                "router_url": self._router_url,
            }
        try:
            msg = String()
            msg.data = json.dumps(diag)
            self._diag_pub.publish(msg)
        except Exception:
            pass

    def _send_heartbeat(self):
        try:
            self._conn.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, mavutil.mavlink.MAV_STATE_ACTIVE)
            with self._lock:
                self._diag_tx_total += 1
                self._diag_tx_heartbeat += 1
        except Exception as exc:
            with self._lock:
                self._diag_pub_errors += 1
            rospy.logwarn("Failed to send HEARTBEAT: %s", str(exc))

    def _send_payload(self, voltage, absorbance, angles, status, automation_step, automation_total, sample_count, pid_error, pid_mode):
        t = int((time.time() - self._boot_time) * 1000) & 0xFFFFFFFF
        try:
            self._conn.mav.named_value_float_send(t, b"USV_VOLT\x00\x00", voltage)
            self._conn.mav.named_value_float_send(t, b"USV_ABS\x00\x00\x00", absorbance)
            self._conn.mav.named_value_float_send(t, b"PUMP_X\x00\x00\x00\x00", angles["X"])
            self._conn.mav.named_value_float_send(t, b"PUMP_Y\x00\x00\x00\x00", angles["Y"])
            self._conn.mav.named_value_float_send(t, b"PUMP_Z\x00\x00\x00\x00", angles["Z"])
            self._conn.mav.named_value_float_send(t, b"PUMP_A\x00\x00\x00\x00", angles["A"])
            self._conn.mav.named_value_float_send(t, b"USV_STAT\x00\x00", float(status))
            self._conn.mav.named_value_float_send(t, b"USV_PKT\x00\x00\x00", float(self._pkt_count))
            self._conn.mav.named_value_float_send(t, b"USV_STEP\x00\x00", automation_step)
            self._conn.mav.named_value_float_send(t, b"USV_STOT\x00\x00", automation_total)
            self._conn.mav.named_value_float_send(t, b"USV_SCNT\x00\x00", sample_count)
            self._conn.mav.named_value_float_send(t, b"USV_PERR\x00\x00", pid_error)
            self._conn.mav.named_value_float_send(t, b"USV_PMOD\x00\x00", pid_mode)
            self._pkt_count = (self._pkt_count + 1) % 65536
            with self._lock:
                self._diag_tx_total += 13
                self._diag_tx_named += 13
        except Exception as exc:
            with self._lock:
                self._diag_pub_errors += 1
            rospy.logwarn("Failed to send payload: %s", str(exc))

    def run(self):
        rate = rospy.Rate(TELEMETRY_RATE_HZ)
        while not rospy.is_shutdown():
            # 命令接收始终运行，不依赖 MAVROS 连接状态
            self._receive_mavlink_messages()

            now = time.time()
            if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                self._send_heartbeat()
                self._last_heartbeat = now

            # 载荷遥测始终发送：bridge 通过 pymavlink 直连 router TCP，
            # 不依赖 MAVROS 连接状态。MAVROS 状态仅影响诊断统计。
            # 回调线程只入队，所有 socket 发送都在本主循环执行。
            with self._lock:
                voltage = self._voltage
                absorbance = self._absorbance
                angles = self._pump_angles.copy()
                status = self._status_code
                automation_step = self._automation_step
                automation_total = self._automation_total
                sample_count = self._sample_count
                pid_error = self._pid_error
                pid_mode = self._pid_mode
                pending_st = list(self._pending_statustexts)
                self._pending_statustexts.clear()
                pending_ack = list(self._pending_acks)
                self._pending_acks.clear()

            for text, severity in pending_st:
                self._send_statustext(text, severity)
            for command, result, target_sys, target_comp in pending_ack:
                self._send_command_ack(command, result, target_sys, target_comp)

            self._send_payload(voltage, absorbance, angles, status, automation_step, automation_total, sample_count, pid_error, pid_mode)

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
