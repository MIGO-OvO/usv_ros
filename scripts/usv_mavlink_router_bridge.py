#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""USV telemetry bridge via mavlink-router TCP endpoint."""

from __future__ import print_function

import json
import math
import threading
import time
import os
import sys
from collections import deque, OrderedDict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from scripts.lib.gps_diagnostics import GPSDiagnosticsCache, PARAMETERS
from scripts.lib.mavlink_tcp_transport import MAVLinkTCPTransport, connect_router
from scripts.lib.payload_protocol import encode_event, next_transport_session, SAFETY_COMMANDS

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
MAX_PENDING_SENDS = 50
RECONNECT_BACKOFF_SEC = 2.0

DEFAULT_HEALTH_FIELDS = {
    "USV_JTMP": -1.0,
    "USV_ETMP": -1.0,
    "USV_JCPU": -1.0,
    "USV_JMEM": -1.0,
    "USV_EHEAP": -1.0,
}

SOURCE_FIELDS = {
    'spectro': ('USV_VOLT', 'USV_ABS', 'USV_BSET', 'USV_REF', 'USV_BASE', 'USV_VLD'),
    'automation': ('USV_STEP', 'USV_STOT', 'USV_LOOP', 'USV_LTOT', 'USV_PMOD', 'USV_ADS'),
    'pid': ('USV_PERR',),
    'health': tuple(DEFAULT_HEALTH_FIELDS),
}

# USV 自定义命令范围
CMD_START_SAMPLING = 31010
CMD_CALIBRATE = 31014
CMD_START_SURVEY = 31015
CMD_STOP_SURVEY = 31016
CMD_SET_BASELINE = 31017
CMD_SPECTROMETER_START = 31018
CMD_SPECTROMETER_STOP = 31019


class USVMavlinkRouterBridge(object):

    def __init__(self):
        rospy.init_node("usv_mavlink_router_bridge", anonymous=False)
        self._sys_id = int(rospy.get_param("~source_system_id", SYS_ID))
        self._comp_id = int(rospy.get_param("~source_component_id", COMP_ID))
        self._router_url = rospy.get_param("~router_url", ROUTER_URL)
        self._gps_cache = GPSDiagnosticsCache(
            int(rospy.get_param('~target_system_id', 1)),
            int(rospy.get_param('~target_component_id', 1)))
        self._gps_pub = rospy.Publisher('/usv/gps_diagnostics', String, queue_size=1)
        self._gps_request_at = 0.0
        self._gps_request_index = 0
        self._wait_heartbeat_timeout = float(rospy.get_param("~wait_heartbeat_timeout", WAIT_HEARTBEAT_TIMEOUT))
        self._lock = threading.Lock()
        self._field_received_at = {}
        self._ads_state = 0
        self._voltage = 0.0
        self._absorbance = 0.0
        self._baseline_set = 0.0
        self._reference_voltage = 0.0
        self._baseline_voltage = 0.0
        self._spectrometer_valid = 0.0
        self._spectrometer_received_at = None
        self._measurement_timeout = max(0.1, float(rospy.get_param('~measurement_timeout', 2.0)))
        self._pump_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self._status_code = 0
        self._last_mission_state = "IDLE"
        self._automation_running = False
        self._automation_paused = False
        self._automation_preflight_active = False
        self._mavros_connected = False
        self._pkt_count = 0
        self._automation_step = 0.0
        self._automation_total = 0.0
        self._current_loop = 0.0
        self._total_loops = 0.0
        self._sample_count = 0.0
        self._pid_error = 0.0
        self._pid_mode = 0.0
        self._health_fields = DEFAULT_HEALTH_FIELDS.copy()
        self._health_received_at = None
        self._boot_time = time.time()
        self._boot_monotonic = time.monotonic()
        self._last_heartbeat = 0.0
        self._last_reconnect_attempt = 0.0
        self._diag_last_report = time.monotonic()
        self._transport = None
        self._last_full_write_at = None
        self._tcp_session = 0
        self._session_path = os.path.expanduser(rospy.get_param('~session_file', '~/.ros/usv_bridge_sessions.sqlite3'))
        self._session_pub = rospy.Publisher('/usv/mavlink_session', Float32MultiArray, queue_size=5, latch=True)
        self._protocol_at = None
        self._protocol_version = 0
        self._recent_events = OrderedDict()
        self._event_chunk_id = 0
        self._reconnect_reason = None
        self._transport_counts = dict.fromkeys(('generated_frames', 'fully_written_frames',            'fully_written_bytes', 'short_writes', 'would_block', 'send_errors',
            'receive_errors', 'dropped_session_items'), 0)
        self._bad_data_counts = {}
        self._bad_data_last_reason = ''
        self._diag_pub = rospy.Publisher("/usv/bridge_diagnostics", String, queue_size=5)
        self._diag_tx_total = 0        # 总发送包数
        self._diag_tx_heartbeat = 0    # 心跳包数
        self._diag_tx_named = 0        # NAMED_VALUE_FLOAT 包数
        self._diag_pub_errors = 0      # 发布错误数
        self._diag_mavros_drops = 0    # MAVROS 断连计数
        # pymavlink TCP 连接非线程安全：
        # ROS 回调线程不直接操作 self._conn，只把待发送项入队，
        # 由 run() 主循环统一发送，避免点击采样后桥接卡死。
        self._pending_statustexts = deque(maxlen=MAX_PENDING_SENDS)   # [(text, severity), ...]
        self._pending_acks = deque(maxlen=MAX_PENDING_SENDS)          # [(command, result, target_sys, target_comp), ...]
        self._pending_usv_done = deque(maxlen=MAX_PENDING_SENDS)      # [sample_id, ...]
        self._pending_usv_fail = deque(maxlen=MAX_PENDING_SENDS)
        self._fcu_sample_id = 0          # current sampling id from FCU NAV_SCRIPT_TIME
        self._last_fcu_sample_id = 0     # suppress retransmitted triggers, including terminal ones
        self._cmd_rx_pub = rospy.Publisher("/usv/mavlink_cmd_rx", Float32MultiArray, queue_size=5)
        self._radio_status_pub = rospy.Publisher("/usv/radio_status", String, queue_size=5)
        rospy.Subscriber("/usv/mavlink_cmd_ack", Float32MultiArray, self._cmd_ack_cb)
        rospy.Subscriber('/usv/payload_protocol', String, self._protocol_cb)
        rospy.Subscriber('/usv/payload_event', String, self._payload_event_cb)
        rospy.Subscriber("/usv/spectrometer_voltage", String, self._voltage_cb)
        rospy.Subscriber("/usv/pump_angles", String, self._angles_cb)
        rospy.Subscriber("/usv/pump_status", String, self._pump_status_cb)
        rospy.Subscriber("/usv/automation_status", String, self._automation_status_cb)
        rospy.Subscriber("/usv/trigger_status", String, self._trigger_status_cb)
        rospy.Subscriber("/usv/sampling_result", String, self._sampling_result_cb)
        rospy.Subscriber("/usv/mission_status", String, self._mission_status_cb)
        rospy.Subscriber("/usv/pump_pid_error", String, self._pid_error_cb)
        rospy.Subscriber("/usv/system_health", String, self._system_health_cb)
        rospy.Subscriber("/mavros/state", State, self._mavros_state_cb, queue_size=5)
        rospy.loginfo("USV Router Bridge: sysid=%d compid=%d router=%s", self._sys_id, self._comp_id, self._router_url)
        self._conn = None
        # Advertise component 191 and receive commands immediately. FCU evidence
        # is ingested by the receive loop; discovery must not block control.
        try:
            self._connect_router()
        except Exception as exc:
            self._retire_router('connect: %s' % exc)

    def _connect_router(self, wait_heartbeat=False):
        self._conn = connect_router(mavutil, self._router_url, self._sys_id, self._comp_id)
        # Keep pymavlink framing/parsing; replace its swallowed TCP errors and
        # single-send write at the actual byte boundary. The loop owns recovery.
        if getattr(self._conn, 'port', None) is not None:
            self._transport = MAVLinkTCPTransport(self._conn.port, self._transport_counts)
            self._conn.write = self._transport.write
            transport, connection = self._transport, self._conn
            self._conn.recv = lambda n=None: transport.recv(n or connection.mav.bytes_needed())
        self._tcp_session = next_transport_session(self._session_path)
        message = Float32MultiArray()
        message.data = [float(self._tcp_session)]
        self._session_pub.publish(message)
        self._gps_cache.records.pop('HEARTBEAT', None)
        if wait_heartbeat:
            self._wait_router_heartbeat()
        return True

    def _wait_router_heartbeat(self):
        hb = self._conn.wait_heartbeat(timeout=self._wait_heartbeat_timeout)
        if hb is None:
            rospy.logwarn("Router bridge: no FCU heartbeat within %.1fs, continue anyway", self._wait_heartbeat_timeout)
        else:
            self._gps_cache.ingest(hb)
            rospy.loginfo("Router bridge: FCU heartbeat detected sys=%s comp=%s", self._conn.target_system, self._conn.target_component)

    def _record_send_error(self):
        with self._lock:
            self._diag_pub_errors += 1

    def _reconnect_router(self):
        now = time.monotonic()
        if now - self._last_reconnect_attempt < RECONNECT_BACKOFF_SEC:
            return False
        self._last_reconnect_attempt = now
        rospy.logwarn("Router bridge: reconnecting MAVLink router endpoint %s", self._router_url)
        try:
            old_conn = self._conn
            if old_conn is not None and hasattr(old_conn, "close"):
                try:
                    old_conn.close()
                except Exception:
                    pass
            return self._connect_router(wait_heartbeat=False)
        except Exception as exc:
            self._retire_router('connect: %s' % exc)
            rospy.logwarn("Router bridge: reconnect failed: %s", str(exc))
            return False

    def _retire_router(self, reason):
        self._reconnect_reason = str(reason)
        connection, self._conn = self._conn, None
        if self._transport and self._transport.last_write_at is not None:
            self._last_full_write_at = self._transport.last_write_at
        self._transport = None
        message = Float32MultiArray()
        message.data = [0.0]
        self._session_pub.publish(message)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        with self._lock:
            for pending in (self._pending_acks, self._pending_statustexts,
                            self._pending_usv_done, self._pending_usv_fail):
                self._transport_counts['dropped_session_items'] += len(pending)
                pending.clear()
            self._recent_events.clear()

    def _mav_send_with_retry(self, send_fn, label):
        # Historical method name retained for callers; no cross-session replay.
        if self._conn is None:
            return False
        try:
            send_fn()
            return True
        except Exception as exc:
            self._record_send_error()
            rospy.logwarn("Failed to send %s: %s", label, str(exc))
            self._retire_router('%s: %s' % (label, exc))
        return False

    def _voltage_cb(self, msg):
        try:
            data = json.loads(msg.data)
            if not isinstance(data, dict):
                raise ValueError('expected object')
            voltage = float(data.get('voltage', data.get('sample_voltage', 0.0)) or 0.0)
            valid = (bool(data.get('valid')) and math.isfinite(voltage)
                     and not data.get('stale') and not data.get('simulated'))
            if data.get('received_at') is not None:
                age = time.time() - float(data['received_at'])
                valid = valid and 0.0 <= age <= self._measurement_timeout
        except Exception:
            data, voltage, valid = {}, 0.0, False
        with self._lock:
            self._voltage = voltage if math.isfinite(voltage) else 0.0
            self._absorbance = self._finite_float(data.get("absorbance"), 0.0)
            self._baseline_set = 1.0 if data.get("baseline_set", False) else 0.0
            self._reference_voltage = self._finite_float(data.get("reference_voltage"), 0.0)
            self._baseline_voltage = self._finite_float(data.get("baseline_voltage"), 0.0)
            self._spectrometer_valid = 1.0 if valid else 0.0
            self._spectrometer_received_at = time.monotonic()
            self._mark_source_locked('spectro', data)

    def _mark_source_locked(self, source, data):
        now = time.monotonic()
        issued = data.get('source_monotonic', now)
        if not isinstance(issued, (int, float)) or not math.isfinite(issued) or not 0 <= issued <= now:
            return
        for field in SOURCE_FIELDS[source]:
            self._field_received_at[field] = issued

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
            if not isinstance(data, dict):
                return
            for motor in ("X", "Y", "Z", "A"):
                if motor in data:
                    value = self._finite_float(data[motor], float('nan'))
                    if math.isfinite(value):
                        self._pump_angles[motor] = value
                        self._field_received_at['PUMP_' + motor] = time.monotonic()

    def _pump_status_cb(self, msg):
        try:
            data_dict = json.loads(msg.data)
        except Exception:
            data_dict = {}

        with self._lock:
            self._apply_automation_status_locked(data_dict)
            # Legacy JSON compatibility; structured updates use /usv/automation_status.

    def _automation_status_cb(self, msg):
        try:
            data_dict = json.loads(msg.data)
        except Exception:
            data_dict = {}

        with self._lock:
            self._apply_automation_status_locked(data_dict)
            if (isinstance(data_dict, dict) and data_dict.get('controller_fault') == 'owner_lost'
                    and data_dict.get('source') == 'fcu' and self._fcu_sample_id > 0
                    and data_dict.get('sample_id') == self._fcu_sample_id):
                self._pending_usv_fail.append(self._fcu_sample_id)
                self._pending_usv_done.clear()
                self._fcu_sample_id = 0

    def _apply_automation_status_locked(self, data_dict):
        if not isinstance(data_dict, dict) or not data_dict:
            return

        self._mark_source_locked('automation', data_dict)
        ads = data_dict.get('ads_state')
        self._ads_state = ads if type(ads) is int and 0 <= ads <= 5 else 0
        calibration = data_dict.get('calibration', {})
        self._calibration_active = isinstance(calibration, dict) and bool(calibration.get('active'))

        self._automation_step = float(data_dict.get("automation_step", 0.0) or 0.0)
        self._automation_total = float(data_dict.get("automation_total", 0.0) or 0.0)
        self._current_loop = float(data_dict.get("current_loop", 0.0) or 0.0)
        self._total_loops = float(data_dict.get("total_loops", 0.0) or 0.0)
        status_text = str(data_dict.get("status", "") or "").lower()
        reported_running = bool(data_dict.get("running", False))
        terminal_status = (
            "finish" in status_text
            or "done" in status_text
            or "stop" in status_text
            or "error" in status_text
            or "fail" in status_text
            or status_text == "idle"
            or (status_text == "" and not reported_running)
        )
        self._automation_running = reported_running and not terminal_status
        self._automation_paused = bool(data_dict.get("paused", False)) and not terminal_status
        preflight = data_dict.get('preflight', {})
        self._automation_preflight_active = (
            isinstance(preflight, dict) and bool(preflight.get('active')) and not terminal_status)

        pid_mode_str = str(data_dict.get("pid_mode", "idle")).lower()
        if "running" in pid_mode_str or "paused" in pid_mode_str:
            self._pid_mode = 1.0
        elif "done" in pid_mode_str or "finish" in pid_mode_str:
            self._pid_mode = 2.0
        elif "error" in pid_mode_str or "fail" in pid_mode_str:
            self._pid_mode = 3.0
        else:
            self._pid_mode = 0.0

        last_state = getattr(self, "_last_mission_state", "IDLE")
        automation_at = self._field_received_at.get('USV_STEP', float('-inf'))
        if ("error" in status_text or "fail" in status_text or self._automation_preflight_active
                or self._automation_paused or last_state in ("IDLE", "HOLD_NO_MISSION", "SAMPLING", "SAMPLING_DONE", "PAUSED")):
            self._field_received_at['USV_STAT'] = automation_at
        if self._calibration_active:
            self._status_code = self._MISSION_STATE_CODES["CALIBRATING"]
            self._field_received_at['USV_STAT'] = automation_at
        elif "error" in status_text or "fail" in status_text:
            self._status_code = self._MISSION_STATE_CODES["FAILED"]
        elif self._automation_preflight_active:
            # Preparation cannot be paused. Reuse the existing preparation/wait
            # code so QGC offers STOP instead of the unsupported pause command.
            self._status_code = self._MISSION_STATE_CODES["WAITING_STABLE"]
        elif self._automation_paused:
            self._status_code = self._MISSION_STATE_CODES["PAUSED"]
        elif self._automation_running and last_state in ("IDLE", "HOLD_NO_MISSION", "SAMPLING", "SAMPLING_DONE", "PAUSED"):
            self._status_code = self._MISSION_STATE_CODES["SAMPLING"]
        elif not self._automation_running:
            sampling_state = last_state in ("IDLE", "HOLD_NO_MISSION", "SAMPLING", "SAMPLING_DONE", "PAUSED")
            # An explicit, completed stop is the current device state. The
            # previous failure stays visible through terminal_reason and the
            # request ledger, but it must not pin USV_STAT to FAILED forever:
            # gating on last_state here left the operator locked out after a
            # run that ended during preparation.
            run_stopped = (terminal_status and not self._automation_paused
                           and getattr(self, "_fcu_sample_id", 0) <= 0)
            if run_stopped and (sampling_state or "stop" in status_text):
                self._last_mission_state = "IDLE"
                self._status_code = self._MISSION_STATE_CODES["IDLE"]
            elif last_state in ("IDLE", "HOLD_NO_MISSION"):
                self._status_code = self._MISSION_STATE_CODES.get(last_state, 0)

    def _trigger_status_cb(self, msg):
        data = msg.data.strip().lower()
        with self._lock:
            if "sampling_started" in data:
                self._sample_count = (self._sample_count + 1) % 65536
                self._pending_statustexts.append(("USV: Sampling Started", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif data == "sampling_stopped":
                # Lifecycle only: failure/cancellation also closes Web recording.
                # Never infer a successful FCU result from this uncorrelated event.
                self._pending_statustexts.append(("USV: Sampling Stopped", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif "sampling_paused" in data:
                self._pending_statustexts.append(("USV: Sampling Paused", mavutil.mavlink.MAV_SEVERITY_NOTICE))
            elif "calibrate" in data:
                self._pending_statustexts.append(("USV: Calibrating", mavutil.mavlink.MAV_SEVERITY_NOTICE))

    def _sampling_result_cb(self, msg):
        """Only a matching, explicit FCU success/skip may release NAV_SCRIPT_TIME."""
        try:
            result = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if not isinstance(result, dict) or result.get("source") != "fcu":
            return
        sample_id = result.get("sample_id")
        outcome = result.get("outcome")
        if type(sample_id) is not int or not 1 <= sample_id <= 65535:
            return
        labels = {"succeeded": "Completed", "failed": "Failed",
                  "cancelled": "Cancelled", "skipped": "Skipped"}
        if not isinstance(outcome, str) or outcome not in labels:
            return
        with self._lock:
            if sample_id != self._fcu_sample_id:
                return  # stale, duplicate, or unrelated sampling result
            self._fcu_sample_id = 0
            if outcome in ("succeeded", "skipped"):
                self._pending_usv_done.append(sample_id)
            else:
                self._pending_usv_fail.append(sample_id)
            self._pending_statustexts.append((
                "USV: Sampling %s id=%d" % (labels[outcome], sample_id),
                mavutil.mavlink.MAV_SEVERITY_NOTICE))
        rospy.loginfo("FCU sampling result id=%d outcome=%s reason=%s",
                      sample_id, outcome, str(result.get("reason", ""))[:120])

    # 任务阶段 → USV_STAT 扩展编码映射
    _MISSION_STATE_CODES = {
        "IDLE": 0, "NAVIGATING": 5, "WAYPOINT_REACHED": 6,
        "HOLDING": 7, "WAITING_STABLE": 8, "SAMPLING": 1,
        "SAMPLING_DONE": 9, "RESUMING_AUTO": 10,
        "HOLD_NO_MISSION": 13, "FAILED": 3, "PAUSED": 11,
        "ABORTED": 12, "DETECTING": 2, "CALIBRATING": 4,
        "SURVEYING": 14,
    }

    def _mission_status_cb(self, msg):
        """从 /usv/mission_status 解析阶段并更新 USV_STAT 编码。"""
        raw = (msg.data or "").strip()
        state = raw.split(":")[0].upper() if raw else "IDLE"
        code = self._MISSION_STATE_CODES.get(state)
        if code is not None:
            with self._lock:
                self._last_mission_state = state
                automation_at = self._field_received_at.get('USV_STEP', float('-inf'))
                automation_fresh = time.monotonic() - automation_at <= 3.0
                state_at = time.monotonic()
                if automation_fresh and getattr(self, '_calibration_active', False):
                    code = self._MISSION_STATE_CODES["CALIBRATING"]
                    state_at = automation_at
                elif (automation_fresh and getattr(self, '_automation_preflight_active', False)
                        and state in ("IDLE", "HOLD_NO_MISSION", "SAMPLING", "PAUSED")):
                    code = self._MISSION_STATE_CODES["WAITING_STABLE"]
                    state_at = automation_at
                elif automation_fresh and self._automation_running and state in ("IDLE", "HOLD_NO_MISSION"):
                    code = self._MISSION_STATE_CODES["SAMPLING"]
                    state_at = automation_at
                self._status_code = code
                self._field_received_at['USV_STAT'] = state_at

    def _pid_error_cb(self, msg):
        try:
            data = json.loads(msg.data)
            with self._lock:
                self._pid_error = float(data.get("error", 0.0) or 0.0)
                self._mark_source_locked('pid', data)
        except Exception:
            pass

    def _system_health_cb(self, msg):
        try:
            data = json.loads(msg.data)
            jetson = data.get("jetson", {}) if isinstance(data, dict) else {}
            detector = data.get("detector", {}) if isinstance(data, dict) else {}
            fields = DEFAULT_HEALTH_FIELDS.copy()
            fields["USV_JTMP"] = self._finite_float(jetson.get("temperature_c"), -1.0)
            fields["USV_ETMP"] = self._finite_float(detector.get("temperature_c"), -1.0)
            fields["USV_JCPU"] = self._finite_float(jetson.get("cpu_percent"), -1.0)
            fields["USV_JMEM"] = self._finite_float(jetson.get("memory_percent"), -1.0)
            fields["USV_EHEAP"] = self._finite_float(detector.get("heap_percent_free"), -1.0)
            with self._lock:
                self._health_fields = fields
                self._health_received_at = time.monotonic()
                self._mark_source_locked('health', data)
        except Exception:
            pass

    @staticmethod
    def _finite_float(value, default):
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float(default)
        if not math.isfinite(result):
            return float(default)
        return result

    def _mavros_state_cb(self, msg):
        was_connected = self._mavros_connected
        self._mavros_connected = bool(msg.connected)
        if was_connected and not self._mavros_connected:
            with self._lock:
                self._diag_mavros_drops += 1

    def _cmd_ack_cb(self, msg):
        try:
            if len(msg.data) not in (4, 5):
                return
            if len(msg.data) == 5 and (self._conn is None or msg.data[4] != self._tcp_session):
                return
            command, result, target_sys, target_comp = msg.data[:4]
            ack = (int(command), int(result), int(target_sys), int(target_comp))
            with self._lock:
                self._pending_acks.append(ack)
        except Exception as exc:
            rospy.logwarn("Failed to queue COMMAND_ACK: %s", str(exc))

    def _protocol_cb(self, msg):
        try:
            data = json.loads(msg.data)
            stamp = float(data['source_monotonic'])
            if not 0 <= time.monotonic() - stamp <= 3.0:
                return
            with self._lock:
                self._protocol_version = 1 if data.get('version') == 1 else 0
                self._protocol_at = stamp
        except (ValueError, TypeError, KeyError):
            pass

    def _payload_event_cb(self, msg):
        try:
            event = json.loads(msg.data)
            text = encode_event(event)
            key = (event['sender_system'], event['sender_component'], event['session'], event['request'])
            with self._lock:
                if self._conn is None or event['tcp_session'] != self._tcp_session:
                    return
                previous = self._recent_events.get(key)
                if previous and event['seq'] <= previous['seq']:
                    return
                # Keep one unsent admission/terminal plus the latest progress.
                # A terminal dominates all older evidence for this request.
                pending = previous['pending'][:] if previous else []
                if event['phase'] == 'progress':
                    pending = [item for item in pending if item['phase'] != 'progress']
                else:
                    pending = []
                pending.append({'text': text, 'phase': event['phase']})
                self._recent_events[key] = {'text': text, 'seq': event['seq'],
                                           'command': event['command'], 'pending': pending,
                                           'expires': time.monotonic() + 30.0, 'sent_at': -1.0}
                self._recent_events.move_to_end(key)
                while len(self._recent_events) > 32:
                    # Protect unsent STOP/terminal evidence under pressure.
                    # At equal priority evict the oldest; even all-STOP floods
                    # remain bounded and must fail closed at the requester.
                    retired = min(self._recent_events, key=lambda item:
                        0 if not self._recent_events[item]['pending'] else
                        4 if self._recent_events[item]['command'] in SAFETY_COMMANDS else
                        3 if self._recent_events[item]['pending'][-1]['phase'] == 'terminal' else
                        2 if self._recent_events[item]['pending'][0]['phase'] == 'request' else 1)
                    del self._recent_events[retired]
        except (ValueError, TypeError, KeyError):
            rospy.logwarn('Invalid correlated payload event')

    def _send_payload_event(self):
        # One bounded message per cycle. New transitions precede history;
        # STOP precedes terminals, then FIFO admissions/progress. Round-robin
        # replay only uses otherwise idle cycles and cannot delay a first send.
        now = time.monotonic()
        text = None
        selected = None
        with self._lock:
            for key in list(self._recent_events):
                event = self._recent_events[key]
                if now >= event['expires']:
                    del self._recent_events[key]
            fresh = [(0 if event['command'] in SAFETY_COMMANDS else
                      1 if event['pending'][0]['phase'] == 'terminal' else 2, key)
                     for key, event in self._recent_events.items() if event['pending']]
            if fresh:
                selected = min(fresh, key=lambda item: item[0])[1]
                event = self._recent_events[selected]
                text = event['pending'].pop(0)['text']
            else:
                for key, event in self._recent_events.items():
                    if now - event['sent_at'] >= 1.0:
                        selected, text = key, event['text']
                        break
            if selected is not None:
                self._recent_events.move_to_end(selected)
        if text is None:
            return
        self._event_chunk_id = self._event_chunk_id % 65535 + 1
        raw = text.encode('ascii') + b'\x00'
        for seq, offset in enumerate(range(0, len(raw), 50)):
            chunk = raw[offset:offset + 50]
            if not self._mav_send_with_retry(lambda: self._conn.mav.statustext_send(
                    mavutil.mavlink.MAV_SEVERITY_NOTICE, chunk, self._event_chunk_id, seq), 'USVE1'):
                return
            self._diag_tx_total += 1
        with self._lock:
            if self._recent_events.get(selected) is event:
                event['sent_at'] = now

    def _send_statustext(self, text, severity):
        if self._mav_send_with_retry(
            lambda: self._conn.mav.statustext_send(severity, text.encode('utf-8')),
            "STATUSTEXT",
        ):
            with self._lock:
                self._diag_tx_total += 1

    def _send_command_ack(self, command, result, target_sys, target_comp):
        sent = self._mav_send_with_retry(
            lambda: self._conn.mav.command_ack_send(
                int(command),
                int(result),
                0xFF,
                0,
                int(target_sys),
                int(target_comp)
            ),
            "COMMAND_ACK",
        )
        if sent:
            with self._lock:
                self._diag_tx_total += 1
            return True
        return False

    def _send_usv_done(self, sample_id):
        t = int((time.monotonic() - self._boot_monotonic) * 1000) & 0xFFFFFFFF
        sent = self._mav_send_with_retry(
            lambda: self._conn.mav.named_value_float_send(t, b"USV_DONE\x00\x00", float(sample_id)),
            "USV_DONE",
        )
        if sent:
            with self._lock:
                self._diag_tx_total += 1
                self._diag_tx_named += 1
            rospy.loginfo("Sent USV_DONE id=%d to FCU", sample_id)

    def _receive_mavlink_messages(self):
        """接收 QGC 发来的 COMMAND_LONG，并兼容转发到旧内部总线。"""
        if self._conn is None:
            return
        deadline = time.monotonic() + 0.01
        for _ in range(64):
            if time.monotonic() >= deadline:
                break
            try:
                msg = self._conn.recv_match(blocking=False)
            except Exception as exc:
                with self._lock:
                    self._diag_pub_errors += 1
                rospy.logwarn_throttle(5.0, "Failed to receive MAVLink: %s", str(exc))
                self._transport_counts['receive_errors'] += 1
                self._retire_router('receive: %s' % exc)
                break
            if not msg:
                break

            msg_type = msg.get_type()
            if msg_type == "BAD_DATA":
                # A rejected frame is a diagnosable event, not a disconnect and
                # not a command. Classify it so the field can tell a corrupt
                # stream from an unknown-but-legal message id.
                self._count_bad_data(getattr(msg, 'reason', '') or '')
                continue

            if hasattr(self, '_gps_cache'):
                try:
                    self._gps_cache.ingest(msg)
                except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                    rospy.logwarn_throttle(10.0, 'GPS evidence rejected: %s', str(exc))

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

            # USV_SMPL / USV_SURV: 飞控 mission 原生采样触发
            if msg_type == "NAMED_VALUE_FLOAT":
                try:
                    if (msg.get_srcSystem() != self._sys_id or msg.get_srcComponent() != 1):
                        continue
                    name = msg.name.rstrip('\x00')
                    if name == 'USV_FAIL':
                        sample_id = int(msg.value)
                        if msg.value != sample_id or sample_id != self._fcu_sample_id or sample_id <= 0:
                            continue
                        rx = Float32MultiArray()
                        rx.data = [31011.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                        self._cmd_rx_pub.publish(rx)
                    elif name == "USV_SMPL":
                        sample_id = int(msg.value)
                        if msg.value != sample_id or not 1 <= sample_id <= 65535:
                            continue
                        with self._lock:
                            if sample_id == self._last_fcu_sample_id:
                                continue
                            self._last_fcu_sample_id = sample_id
                            self._fcu_sample_id = sample_id
                            # A newer script supersedes any not-yet-sent old completion.
                            self._pending_usv_done.clear()
                            self._pending_usv_fail.clear()
                        rospy.loginfo("FCU triggered point sampling id=%d", sample_id)
                        rx = Float32MultiArray()
                        # param1=0, param2=sample_id — trigger uses param2>0 to detect FCU origin
                        rx.data = [float(CMD_START_SAMPLING), 0.0, float(sample_id), 0.0, 0.0, 0.0, 0.0]
                        self._cmd_rx_pub.publish(rx)
                    elif name == "USV_SURV":
                        survey_on = int(msg.value)
                        cmd = CMD_START_SURVEY if survey_on else CMD_STOP_SURVEY
                        rospy.loginfo("FCU triggered survey cmd=%d", cmd)
                        rx = Float32MultiArray()
                        rx.data = [float(cmd), float(msg.value), 0.0, 0.0, 0.0, 0.0, 0.0]
                        self._cmd_rx_pub.publish(rx)
                except Exception:
                    pass
                continue

            if msg_type != "COMMAND_LONG":
                continue

            try:
                command = int(msg.command)
                if command < CMD_START_SAMPLING or command > CMD_SPECTROMETER_STOP:
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
                    float(getattr(msg, 'param3', 0) or 0),
                    float(getattr(msg, 'param4', 0) or 0),
                    float(self._tcp_session),
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

    @staticmethod
    def _bad_data_class(reason):
        text = str(reason or '').lower()
        if 'crc' in text:
            return 'bad_crc'
        if 'unknown' in text and 'message' in text:
            return 'unknown_message_id'
        if 'length' in text or 'short' in text:
            return 'bad_length'
        if 'signature' in text or 'signing' in text:
            return 'bad_signature'
        return 'other'

    def _count_bad_data(self, reason):
        """Classify a rejected inbound frame for diagnostics only.

        Nothing here dispatches a command or retires the session: the parser
        already refused those bytes, and resynchronisation is its job.
        """
        bucket = self._bad_data_class(reason)
        with self._lock:
            self._bad_data_counts[bucket] = self._bad_data_counts.get(bucket, 0) + 1
            self._bad_data_counts['total'] = self._bad_data_counts.get('total', 0) + 1
            self._bad_data_last_reason = str(reason or '')[:120]

    def _publish_diagnostics(self):
        now = time.time()
        last_write = (self._transport.last_write_at if self._transport and self._transport.last_write_at is not None
                      else self._last_full_write_at)
        with self._lock:
            uptime = time.monotonic() - self._boot_monotonic
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
                "tcp_session": self._tcp_session,
                "tcp_connected": self._conn is not None,
                "reconnect_reason": self._reconnect_reason,
                "transport": dict(self._transport_counts),
                "bad_data": dict(self._bad_data_counts),
                "bad_data_last_reason": self._bad_data_last_reason,
                "fcu_heartbeat_age_s": (time.monotonic() - self._gps_cache.records['HEARTBEAT']['received_monotonic']
                    if 'HEARTBEAT' in self._gps_cache.records else None),
                "last_complete_write_age_s": time.monotonic() - last_write if last_write is not None else None,
            }
        try:
            msg = String()
            msg.data = json.dumps(diag)
            self._diag_pub.publish(msg)
        except Exception:
            pass

    def _publish_gps_evidence(self):
        """Same TCP owner; bounded read-only requests, never PARAM_SET or stream changes."""
        message = String()
        message.data = json.dumps(self._gps_cache.snapshot(), allow_nan=False)
        self._gps_pub.publish(message)
        now = time.monotonic()
        heartbeat = self._gps_cache.records.get('HEARTBEAT', {})
        if now - heartbeat.get('received_monotonic', -100) > 3 or now - self._gps_request_at < 5:
            return
        self._gps_request_at = now
        index = self._gps_request_index % 4
        self._gps_request_index += 1
        system, component = self._gps_cache.system_id, self._gps_cache.component_id
        if index < 3:
            self._mav_send_with_retry(lambda: self._conn.mav.param_request_read_send(
                system, component, PARAMETERS[index].encode('ascii'), -1), 'GPS parameter read')
        elif 'AUTOPILOT_VERSION' not in self._gps_cache.records:
            self._mav_send_with_retry(lambda: self._conn.mav.command_long_send(
                system, component, 512, 0, 148, 0, 0, 0, 0, 0, 0), 'AUTOPILOT_VERSION request')

    def _send_heartbeat(self):
        sent = self._mav_send_with_retry(
            lambda: self._conn.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
            ),
            "HEARTBEAT",
        )
        if sent:
            with self._lock:
                self._diag_tx_total += 1
                self._diag_tx_heartbeat += 1

    def _send_payload(self, voltage, absorbance, angles, status, automation_step, automation_total, current_loop, total_loops, sample_count, pid_error, pid_mode, baseline_set, reference_voltage, baseline_voltage, spectrometer_valid, health_fields=None):
        t = int((time.monotonic() - self._boot_monotonic) * 1000) & 0xFFFFFFFF
        health = dict(DEFAULT_HEALTH_FIELDS)
        health.update(health_fields if health_fields is not None else getattr(self, "_health_fields", {}))
        with self._lock:
            protocol = self._protocol_version if self._protocol_at is not None and time.monotonic() - self._protocol_at < 3.0 else 0
        fields = (
            (b"USV_VOLT\x00\x00", voltage),
            (b"USV_ABS\x00\x00\x00", absorbance),
            (b"PUMP_X\x00\x00\x00\x00", angles["X"]),
            (b"PUMP_Y\x00\x00\x00\x00", angles["Y"]),
            (b"PUMP_Z\x00\x00\x00\x00", angles["Z"]),
            (b"PUMP_A\x00\x00\x00\x00", angles["A"]),
            (b"USV_STAT\x00\x00", float(status)),
            (b"USV_PKT\x00\x00\x00", float(self._pkt_count)),
            (b"USV_STEP\x00\x00", automation_step),
            (b"USV_STOT\x00\x00", automation_total),
            (b"USV_LOOP\x00\x00", current_loop),
            (b"USV_LTOT\x00\x00", total_loops),
            (b"USV_SCNT\x00\x00", sample_count),
            (b"USV_PERR\x00\x00", pid_error),
            (b"USV_PMOD\x00\x00", pid_mode),
            (b"USV_BSET\x00\x00", baseline_set),
            (b"USV_REF\x00\x00\x00", reference_voltage),
            (b"USV_BASE\x00\x00", baseline_voltage),
            (b"USV_VLD\x00\x00\x00", spectrometer_valid),
            (b"USV_JTMP\x00\x00", health["USV_JTMP"]),
            (b"USV_ETMP\x00\x00", health["USV_ETMP"]),
            (b"USV_JCPU\x00\x00", health["USV_JCPU"]),
            (b"USV_JMEM\x00\x00", health["USV_JMEM"]),
            (b"USV_EHEAP\x00", health["USV_EHEAP"]),
            (b"USV_ADS\x00\x00\x00", self._ads_state),
            (b"USV_PROTO\x00", float(protocol)),
        )

        sent_all = True
        for name, value in fields:
            field = name.rstrip(b'\x00').decode('ascii')
            with self._lock:
                age = time.monotonic() - self._field_received_at.get(field, float('-inf'))
            limit = self._measurement_timeout if field in SOURCE_FIELDS['spectro'] else 3.0
            if field not in ('USV_PKT', 'USV_SCNT', 'USV_PROTO') and age > limit:
                if field in ('USV_VLD', 'USV_BSET', 'USV_ADS'):
                    value = 0.0  # Explicit unknown/invalid is safe; never renew an old true value.
                else:
                    continue
            if not self._mav_send_with_retry(
                    lambda: self._conn.mav.named_value_float_send(t, name, value), 'payload telemetry'):
                sent_all = False
                break
            with self._lock:
                self._diag_tx_total += 1
                self._diag_tx_named += 1
        if sent_all:
            self._pkt_count = (self._pkt_count + 1) % 65536

    def run(self):
        rate = rospy.Rate(TELEMETRY_RATE_HZ)
        while not rospy.is_shutdown():
            if self._conn is None:
                self._reconnect_router()
            if self._transport:
                self._transport.begin_cycle()
            # 命令接收始终运行，不依赖 MAVROS 连接状态
            self._receive_mavlink_messages()

            now = time.monotonic()
            if now - self._last_heartbeat >= (1.0 / HEARTBEAT_RATE_HZ):
                self._send_heartbeat()
                self._last_heartbeat = now

            # 载荷遥测始终发送：bridge 通过 pymavlink 直连 router TCP，
            # 不依赖 MAVROS 连接状态。MAVROS 状态仅影响诊断统计。
            # 回调线程只入队，所有 socket 发送都在本主循环执行。
            with self._lock:
                measured_at = self._spectrometer_received_at
                if measured_at is None or time.monotonic() - measured_at > self._measurement_timeout:
                    self._spectrometer_valid = 0.0
                if self._health_received_at is None or time.monotonic() - self._health_received_at > 5.0:
                    self._health_fields = DEFAULT_HEALTH_FIELDS.copy()
                voltage = self._voltage
                absorbance = self._absorbance
                angles = self._pump_angles.copy()
                status = self._status_code
                automation_step = self._automation_step
                automation_total = self._automation_total
                current_loop = self._current_loop
                total_loops = self._total_loops
                sample_count = self._sample_count
                pid_error = self._pid_error
                pid_mode = self._pid_mode
                baseline_set = self._baseline_set
                reference_voltage = self._reference_voltage
                baseline_voltage = self._baseline_voltage
                spectrometer_valid = self._spectrometer_valid
                health_fields = self._health_fields.copy()
                pending_st = list(self._pending_statustexts)
                self._pending_statustexts.clear()
                pending_ack = list(self._pending_acks)
                self._pending_acks.clear()
                pending_done = list(self._pending_usv_done)
                self._pending_usv_done.clear()
                pending_fail = list(self._pending_usv_fail)
                self._pending_usv_fail.clear()

            # Safety outcomes precede routine ACKs, logs and bulk telemetry.
            for sample_id in pending_fail:
                t = int((time.monotonic() - self._boot_monotonic) * 1000) & 0xFFFFFFFF
                self._mav_send_with_retry(
                    lambda: self._conn.mav.named_value_float_send(t, b'USV_FAIL\x00\x00', float(sample_id)),
                    'USV_FAIL')
            for sample_id in pending_done:
                self._send_usv_done(sample_id)
            for command, result, target_sys, target_comp in pending_ack:
                self._send_command_ack(command, result, target_sys, target_comp)
            for text, severity in pending_st:
                self._send_statustext(text, severity)
            self._send_payload_event()

            self._send_payload(voltage, absorbance, angles, status, automation_step, automation_total, current_loop, total_loops, sample_count, pid_error, pid_mode, baseline_set, reference_voltage, baseline_voltage, spectrometer_valid, health_fields)

            try:
                self._publish_gps_evidence()
            except Exception as exc:
                # Diagnostics must never interrupt control, ACK or sampling outcomes.
                rospy.logwarn_throttle(10.0, 'GPS diagnostics unavailable: %s', str(exc))

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
