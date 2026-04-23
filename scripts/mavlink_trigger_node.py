#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false
"""
MAVLink Trigger Node (MAVLink 指令触发节点)
============================================
监听 MAVLink 自定义指令，触发采样任务。

功能:
  - 监听 MAVROS 的 COMMAND_LONG 消息
  - 接收自定义指令 (如 MAV_CMD 31010) 触发采样
  - 监听航点到达事件自动触发
  - 控制飞行模式 (HOLD/AUTO)

Target: Jetson Nano
ROS: Noetic
Python: 3.8

MAVLink 指令:
  - 31010: 开始采样任务
  - 31011: 停止采样任务
  - 31012: 暂停采样
  - 31013: 恢复采样

QGC 发送自定义指令:
  在 QGC 中使用 MAVLink Inspector 或自定义按钮发送 COMMAND_LONG
"""

from __future__ import print_function

import json
import math
import os
import struct
import threading
import time

import rospy
from geometry_msgs.msg import TwistStamped
from sensor_msgs.msg import Imu
from std_msgs.msg import String, Float32MultiArray
from std_srvs.srv import Trigger, TriggerResponse
from mavros_msgs.msg import State, WaypointReached, Mavlink
from mavros_msgs.srv import SetMode, SetModeRequest

# 自定义 MAVLink 指令 ID
CMD_START_SAMPLING = 31010
CMD_STOP_SAMPLING = 31011
CMD_PAUSE_SAMPLING = 31012
CMD_RESUME_SAMPLING = 31013
CMD_CALIBRATE = 31014
CMD_START_SURVEY = 31015
CMD_STOP_SURVEY = 31016

# MAVLink 协议常量
MAVLINK_MSG_ID_COMMAND_LONG = 76
MAVLINK_MSG_ID_COMMAND_ACK = 77

# MAV_RESULT enum
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_TEMPORARILY_REJECTED = 1
MAV_RESULT_FAILED = 5

CONFIG_FILE = os.path.expanduser("~/usv_ws/config/sampling_config.json")


class MissionState(object):
    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    WAYPOINT_REACHED = "WAYPOINT_REACHED"
    HOLDING = "HOLDING"
    WAITING_STABLE = "WAITING_STABLE"
    SAMPLING = "SAMPLING"
    SAMPLING_DONE = "SAMPLING_DONE"
    RESUMING_AUTO = "RESUMING_AUTO"
    HOLD_NO_MISSION = "HOLD_NO_MISSION"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    ABORTED = "ABORTED"


class WaypointSamplingState(object):
    IDLE = "IDLE"
    ARRIVED = "ARRIVED"
    HOLDING = "HOLDING"
    WAITING_STABLE = "WAITING_STABLE"
    SAMPLING = "SAMPLING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class MAVLinkTriggerNode(object):
    """
    MAVLink 指令触发节点。
    监听 MAVLink 指令和航点事件，触发采样任务。
    """

    def __init__(self):
        rospy.init_node('mavlink_trigger_node', anonymous=False)

        self._source_system_id = int(rospy.get_param('~source_system_id', rospy.get_param('/mavros/target_system_id', 1)))
        self._source_component_id = int(rospy.get_param('~source_component_id', 191))

        # 参数
        self.mavros_timeout = rospy.get_param('~mavros_timeout', 30.0)
        self.hold_settle_time = float(rospy.get_param('~hold_settle_time', 3.0))
        self.stable_check_timeout = float(rospy.get_param('~stable_check_timeout', 20.0))
        self.stable_speed_threshold = float(rospy.get_param('~stable_speed_threshold', 0.15))
        self.stable_yaw_rate_threshold = float(rospy.get_param('~stable_yaw_rate_threshold', 0.08))
        self.default_retry_count = int(rospy.get_param('~sampling_retry_count', 0))
        self.default_on_fail = str(rospy.get_param('~sampling_on_fail', 'HOLD')).strip().upper()

        # 状态
        self.mavros_state = State()
        self.mavros_connected = False
        self.is_sampling = False
        self.current_waypoint = 0
        self.current_mission_state = MissionState.IDLE
        self.current_sampling_context = None
        self.last_linear_speed = 0.0
        self.last_yaw_rate = 0.0
        self.waypoint_states = {}
        self.state_lock = threading.Lock()

        # 走航采样状态
        self._survey_active = False
        self._survey_interval = 5.0
        self._survey_thread = None

        # 服务客户端
        self.set_mode_client = None

        # Publishers
        self.status_pub = rospy.Publisher('/usv/trigger_status', String, queue_size=10)
        self.mission_status_pub = rospy.Publisher('/usv/mission_status', String, queue_size=10)
        self.steps_pub = rospy.Publisher('/usv/automation_steps', String, queue_size=1)
        self.pump_command_pub = rospy.Publisher('/usv/pump_command', String, queue_size=10)
        self.mavlink_to_pub = rospy.Publisher('/mavros/mavlink/to', Mavlink, queue_size=10)

        # Subscribers
        self.state_sub = rospy.Subscriber('/mavros/state', State, self._state_cb)
        self.waypoint_sub = rospy.Subscriber('/mavros/mission/reached', WaypointReached, self._waypoint_cb)
        self.pump_status_sub = rospy.Subscriber('/usv/pump_status', String, self._pump_status_cb)
        self.velocity_sub = rospy.Subscriber('/mavros/local_position/velocity_local', TwistStamped, self._velocity_cb, queue_size=10)
        self.imu_sub = rospy.Subscriber('/mavros/imu/data', Imu, self._imu_cb, queue_size=10)
        self.mavlink_cmd_sub = rospy.Subscriber('/usv/mavlink_cmd_rx', Float32MultiArray, self._mavlink_cmd_rx_cb, queue_size=20)
        self.mavlink_sub = rospy.Subscriber('/mavros/mavlink/from', Mavlink, self._mavlink_from_cb, queue_size=20)

        rospy.loginfo("MAVLink Trigger Node initialized")
        rospy.loginfo("  Auto trigger on waypoint: %s", self.auto_trigger_on_waypoint)
        rospy.loginfo("  Listening for COMMAND_LONG on /mavros/mavlink/from")
        rospy.loginfo("  MAVLink source IDs: sysid=%d compid=%d", self._source_system_id, self._source_component_id)
        rospy.loginfo("  Stable check: hold_settle_time=%.1fs timeout=%.1fs speed<=%.3f yaw_rate<=%.3f",
                      self.hold_settle_time, self.stable_check_timeout,
                      self.stable_speed_threshold, self.stable_yaw_rate_threshold)

    def _state_cb(self, msg):
        """MAVROS 状态回调。"""
        with self.state_lock:
            self.mavros_state = msg
            self.mavros_connected = msg.connected


    def _velocity_cb(self, msg):
        """速度回调。"""
        twist = msg.twist
        self.last_linear_speed = math.sqrt(
            twist.linear.x * twist.linear.x +
            twist.linear.y * twist.linear.y +
            twist.linear.z * twist.linear.z
        )

    def _imu_cb(self, msg):
        """IMU 回调。"""
        self.last_yaw_rate = abs(msg.angular_velocity.z)

    def _set_mission_state(self, state, detail=""):
        """设置并发布任务阶段状态。"""
        self.current_mission_state = state
        payload = state if not detail else "{}:{}".format(state, detail)
        msg = String()
        msg.data = payload
        self.mission_status_pub.publish(msg)
        self._publish_status("mission:{}".format(payload))

    def _get_waypoint_state(self, wp_seq):
        return self.waypoint_states.get(int(wp_seq), WaypointSamplingState.IDLE)

    def _set_waypoint_state(self, wp_seq, state):
        self.waypoint_states[int(wp_seq)] = state

    def _load_config(self):
        """加载配置文件。"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            rospy.logwarn("Failed to load config: %s", str(e))
        return None

    def _get_default_config(self):
        return {
            "sampling_sequence": {"steps": [], "loop_count": 1},
            "pump_settings": {"pid_mode": True, "pid_precision": 0.1},
            "waypoint_sampling": {}
        }

    def _get_waypoint_sampling_config(self, config, waypoint_seq):
        """获取指定航点采样配置。"""
        config = config or {}
        wp_cfg = config.get('waypoint_sampling', {}) or {}
        seq_key = str(int(waypoint_seq))
        waypoint_config = dict(wp_cfg.get(seq_key, {}) or {})

        try:
            loop_count = int(waypoint_config.get('loop_count', config.get('sampling_sequence', {}).get('loop_count', 1)) or 1)
        except (TypeError, ValueError):
            loop_count = int(config.get('sampling_sequence', {}).get('loop_count', 1) or 1)

        try:
            retry_count = int(waypoint_config.get('retry_count', self.default_retry_count) or 0)
        except (TypeError, ValueError):
            retry_count = self.default_retry_count

        try:
            settle_time = float(waypoint_config.get('hold_before_sampling_s', self.hold_settle_time) or self.hold_settle_time)
        except (TypeError, ValueError):
            settle_time = self.hold_settle_time

        on_fail = str(waypoint_config.get('on_fail', self.default_on_fail) or self.default_on_fail).strip().upper()
        enabled = waypoint_config.get('enabled', True)
        return {
            'enabled': bool(enabled),
            'loop_count': max(0, loop_count),
            'retry_count': max(0, retry_count),
            'hold_before_sampling_s': max(0.0, settle_time),
            'on_fail': on_fail if on_fail in ('HOLD', 'SKIP', 'ABORT') else 'HOLD',
        }

    def _build_steps_payload(self, config, waypoint_seq):
        sampling_sequence = dict((config or {}).get('sampling_sequence', {}) or {})
        waypoint_cfg = self._get_waypoint_sampling_config(config, waypoint_seq)
        return {
            'steps': sampling_sequence.get('steps', []),
            'loop_count': waypoint_cfg['loop_count'],
            'pid_mode': (config or {}).get('pump_settings', {}).get('pid_mode', True),
            'pid_precision': (config or {}).get('pump_settings', {}).get('pid_precision', 0.1),
            'waypoint_seq': int(waypoint_seq),
            'retry_count': waypoint_cfg['retry_count'],
            'on_fail': waypoint_cfg['on_fail'],
        }

    def _wait_until_stable(self, settle_time):
        """轻量稳定判定：速度/角速度持续低于阈值。"""
        self._set_mission_state(MissionState.WAITING_STABLE, str(self.current_waypoint))
        stable_since = None
        deadline = time.time() + self.stable_check_timeout
        rate = rospy.Rate(5)
        while not rospy.is_shutdown() and time.time() < deadline:
            if self.mavros_state.mode and self.mavros_state.mode.upper() != 'HOLD':
                rospy.logwarn("Vehicle left HOLD during stable wait: %s", self.mavros_state.mode)
                return False, 'mode_left_hold'

            linear_ok = self.last_linear_speed <= self.stable_speed_threshold
            yaw_ok = self.last_yaw_rate <= self.stable_yaw_rate_threshold
            if linear_ok and yaw_ok:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= settle_time:
                    return True, 'stable'
            else:
                stable_since = None
            rate.sleep()
        return False, 'stable_timeout'

    def _waypoint_cb(self, msg):
        """航点到达回调 — 仅记录状态，采样由飞控 mission 原生触发。"""
        self.current_waypoint = msg.wp_seq
        rospy.loginfo("Waypoint %d reached", msg.wp_seq)
        self._set_mission_state(MissionState.WAYPOINT_REACHED, str(msg.wp_seq))
        self._publish_status("waypoint_reached:{}".format(msg.wp_seq))

    def _handle_completion(self, success=True, reason='finished'):
        with self.state_lock:
            if not self.is_sampling:
                return
            self.is_sampling = False
        rospy.loginfo("Handling sampling completion: success=%s reason=%s", success, reason)
        wp_seq = self.current_waypoint
        if success:
            self._set_waypoint_state(wp_seq, WaypointSamplingState.DONE)
            self._set_mission_state(MissionState.SAMPLING_DONE, str(wp_seq))
            self._publish_status("sampling_stopped")
            rospy.sleep(1.0)
            self._resume_auto_if_mission_exists()
        else:
            self._set_waypoint_state(wp_seq, WaypointSamplingState.FAILED)
            self._set_mission_state(MissionState.FAILED, "{}:{}".format(wp_seq, reason))
            self._handle_failure_action(reason)

    def _pump_status_cb(self, msg):
        data = (msg.data or '').lower()
        if 'automation: finished' in data:
            with self.state_lock:
                if self.is_sampling:
                    threading.Thread(target=self._handle_completion, kwargs={'success': True, 'reason': 'finished'}, daemon=True).start()
            return
        if 'automation:' in data and ('error' in data or 'fail' in data or 'timeout' in data):
            with self.state_lock:
                if self.is_sampling:
                    threading.Thread(target=self._handle_completion, kwargs={'success': False, 'reason': data[:80]}, daemon=True).start()

    def _start_sampling_sequence(self, waypoint_seq=None):
        """启动采样序列。"""
        waypoint_seq = self.current_waypoint if waypoint_seq is None else int(waypoint_seq)
        if self.is_sampling:
            rospy.logwarn("Sampling already in progress")
            return False

        config = self._load_config() or self._get_default_config()
        waypoint_cfg = self._get_waypoint_sampling_config(config, waypoint_seq)
        if not waypoint_cfg['enabled']:
            rospy.loginfo("Waypoint %d sampling disabled, skip and resume AUTO", waypoint_seq)
            self._set_waypoint_state(waypoint_seq, WaypointSamplingState.SKIPPED)
            self._resume_auto_if_mission_exists()
            return True

        self.current_waypoint = waypoint_seq
        self.current_sampling_context = {
            'waypoint_seq': waypoint_seq,
            'retry_count': waypoint_cfg['retry_count'],
            'on_fail': waypoint_cfg['on_fail'],
        }
        self._set_waypoint_state(waypoint_seq, WaypointSamplingState.HOLDING)
        self._set_mission_state(MissionState.HOLDING, str(waypoint_seq))
        rospy.loginfo("Starting sampling sequence at waypoint %d", waypoint_seq)

        if not self.set_mode('HOLD'):
            self._set_waypoint_state(waypoint_seq, WaypointSamplingState.FAILED)
            self._set_mission_state(MissionState.FAILED, "{}:set_hold_failed".format(waypoint_seq))
            self._handle_failure_action('set_hold_failed')
            return False

        self._set_waypoint_state(waypoint_seq, WaypointSamplingState.WAITING_STABLE)
        stable_ok, stable_reason = self._wait_until_stable(waypoint_cfg['hold_before_sampling_s'])
        if not stable_ok:
            self._set_waypoint_state(waypoint_seq, WaypointSamplingState.FAILED)
            self._set_mission_state(MissionState.FAILED, "{}:{}".format(waypoint_seq, stable_reason))
            self._handle_failure_action(stable_reason)
            return False

        steps_data = self._build_steps_payload(config, waypoint_seq)
        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)

        self._set_waypoint_state(waypoint_seq, WaypointSamplingState.SAMPLING)
        self._set_mission_state(MissionState.SAMPLING, str(waypoint_seq))
        self.is_sampling = True
        if not self._call_automation_service('start'):
            self.is_sampling = False
            self._set_waypoint_state(waypoint_seq, WaypointSamplingState.FAILED)
            self._set_mission_state(MissionState.FAILED, "{}:automation_start_failed".format(waypoint_seq))
            self._handle_failure_action('automation_start_failed')
            return False

        return True

    def _stop_sampling_sequence(self):
        """停止采样序列。"""
        rospy.loginfo("Stopping sampling sequence...")
        self._call_automation_service('stop')
        self.is_sampling = False
        self._set_mission_state(MissionState.HOLD_NO_MISSION, str(self.current_waypoint))
        self._publish_status("sampling_stopped")
        rospy.sleep(1.0)
        self._resume_auto_if_mission_exists()

    def _handle_failure_action(self, reason):
        ctx = self.current_sampling_context or {}
        retries_left = int(ctx.get('retry_count', 0) or 0)
        on_fail = str(ctx.get('on_fail', self.default_on_fail) or self.default_on_fail).upper()
        waypoint_seq = int(ctx.get('waypoint_seq', self.current_waypoint) or self.current_waypoint)

        if retries_left > 0:
            ctx['retry_count'] = retries_left - 1
            self.current_sampling_context = ctx
            rospy.logwarn("Sampling failed at waypoint %d, retry left=%d reason=%s", waypoint_seq, ctx['retry_count'], reason)
            threading.Thread(target=self._start_sampling_sequence, args=(waypoint_seq,), daemon=True).start()
            return

        if on_fail == 'SKIP':
            rospy.logwarn("Sampling failed at waypoint %d, skip and resume AUTO: %s", waypoint_seq, reason)
            self._set_waypoint_state(waypoint_seq, WaypointSamplingState.SKIPPED)
            self._resume_auto_if_mission_exists()
            return
        if on_fail == 'ABORT':
            rospy.logerr("Sampling failed at waypoint %d, abort mission: %s", waypoint_seq, reason)
            self._set_mission_state(MissionState.ABORTED, str(waypoint_seq))
            self.set_mode('HOLD')
            return

        rospy.logwarn("Sampling failed at waypoint %d, staying HOLD: %s", waypoint_seq, reason)
        self.set_mode('HOLD')

    def _mavlink_from_cb(self, msg):
        """
        MAVROS 原始 MAVLink 入站消息回调。

        mavros_msgs/Mavlink 消息结构:
          - msgid: uint32     MAVLink 消息 ID
          - payload64: uint64[]  载荷数据 (8 字节对齐的 little-endian 块)
          - sysid: uint8      发送方系统 ID
          - compid: uint8     发送方组件 ID
        """
        # 只处理 COMMAND_LONG (msgid=76)
        if msg.msgid != MAVLINK_MSG_ID_COMMAND_LONG:
            return

        self._handle_command_long_payload(msg)

    def _mavlink_cmd_rx_cb(self, msg):
        """兼容旧内部总线 /usv/mavlink_cmd_rx。"""
        try:
            if len(msg.data) < 7:
                return

            command = int(msg.data[0])
            param1 = float(msg.data[1])
            param2 = float(msg.data[2])
            target_system = int(msg.data[3])
            target_component = int(msg.data[4])
            sender_system = int(msg.data[5])
            sender_component = int(msg.data[6])

            if target_system != 0 and target_system != self._source_system_id:
                return
            if target_component != 0 and target_component != self._source_component_id:
                return

            self._dispatch_command_long(
                command,
                param1,
                param2,
                sender_system,
                sender_component,
                log_prefix="Received forwarded COMMAND_LONG",
            )
        except Exception as e:
            rospy.logerr("Error handling forwarded COMMAND_LONG payload: %s", str(e))

    def _dispatch_command_long(self, command, param1, param2, sender_system, sender_component, log_prefix):
        """处理并确认自定义 COMMAND_LONG。"""
        if command < CMD_START_SAMPLING or command > CMD_STOP_SURVEY:
            return

        rospy.loginfo(
            "%s from sysid=%d compid=%d: cmd=%d param1=%.1f param2=%.1f",
            log_prefix, sender_system, sender_component, command, param1, param2
        )

        success = self.handle_mavlink_command(command, param1, param2)

        if success:
            ack_result = MAV_RESULT_ACCEPTED
        elif command == CMD_START_SAMPLING and self.is_sampling:
            ack_result = MAV_RESULT_TEMPORARILY_REJECTED
        else:
            ack_result = MAV_RESULT_FAILED

        self._send_command_ack(command, ack_result, sender_system, sender_component)

    def _handle_command_long_payload(self, msg):
        """
        解析 COMMAND_LONG 的 payload64 并分发命令。

        COMMAND_LONG 载荷格式 (33 bytes, little-endian):
          offset 0:  param1  float32
          offset 4:  param2  float32
          offset 8:  param3  float32
          offset 12: param4  float32
          offset 16: param5  float32
          offset 20: param6  float32
          offset 24: param7  float32
          offset 28: command uint16
          offset 30: target_system    uint8
          offset 31: target_component uint8
          offset 32: confirmation     uint8
        """
        try:
            # 将 payload64 (uint64 数组) 还原为连续字节流
            payload_bytes = b''
            for val in msg.payload64:
                payload_bytes += struct.pack('<Q', val)

            # COMMAND_LONG 载荷至少 33 字节
            if len(payload_bytes) < 33:
                return

            # 解析关键字段
            param1 = struct.unpack_from('<f', payload_bytes, 0)[0]
            param2 = struct.unpack_from('<f', payload_bytes, 4)[0]
            command = struct.unpack_from('<H', payload_bytes, 28)[0]

            self._dispatch_command_long(
                command,
                param1,
                param2,
                msg.sysid,
                msg.compid,
                log_prefix="Received COMMAND_LONG",
            )

        except Exception as e:
            rospy.logerr("Error parsing COMMAND_LONG payload: %s", str(e))

    def _init_services(self):
        """初始化 MAVROS 服务。"""
        rospy.loginfo("Waiting for MAVROS services...")
        try:
            rospy.wait_for_service('/mavros/set_mode', timeout=self.mavros_timeout)
            self.set_mode_client = rospy.ServiceProxy('/mavros/set_mode', SetMode)
            rospy.loginfo("MAVROS services connected")
            return True
        except rospy.ROSException as e:
            rospy.logerr("MAVROS service timeout: %s", str(e))
            return False

    def wait_for_mavros(self):
        """等待 MAVROS 连接。"""
        rospy.loginfo("Waiting for MAVROS connection...")
        rate = rospy.Rate(1)
        start_time = rospy.Time.now()

        while not rospy.is_shutdown():
            with self.state_lock:
                connected = self.mavros_connected

            if connected:
                rospy.loginfo("MAVROS connected")
                return True

            elapsed = (rospy.Time.now() - start_time).to_sec()
            if elapsed > self.mavros_timeout:
                rospy.logwarn("MAVROS connection timeout, continuing without MAVROS")
                return False

            rate.sleep()

        return False

    def set_mode(self, mode):
        """设置飞行模式。"""
        if not self.set_mode_client:
            rospy.logwarn("Set mode service not available")
            return False

        try:
            req = SetModeRequest()
            req.custom_mode = mode
            resp = self.set_mode_client(req)

            if resp.mode_sent:
                rospy.loginfo("Mode set to: %s", mode)
                self._publish_status("mode_changed:{}".format(mode))
                return True
            else:
                rospy.logwarn("Failed to set mode: %s", mode)
                return False

        except rospy.ServiceException as e:
            rospy.logerr("Set mode error: %s", str(e))
            return False


    def _pause_sampling(self):
        """暂停采样。"""
        self._call_automation_service('pause')
        self._set_mission_state(MissionState.PAUSED, str(self.current_waypoint))
        self._publish_status("sampling_paused")

    def _resume_sampling(self):
        """恢复采样。"""
        self._call_automation_service('resume')
        self._set_mission_state(MissionState.SAMPLING, str(self.current_waypoint))
        self._publish_status("sampling_resumed")

    def _resume_auto_if_mission_exists(self):
        """采样完成后的模式处理。

        飞控原生 mission 模式下（NAV_SCRIPT_TIME）：飞控自行恢复 AUTO，
        ROS 只负责通过 USV_DONE 通知飞控采样完成。
        手动采样模式下：维持当前模式不变。
        """
        self._set_mission_state(MissionState.SAMPLING_DONE, str(self.current_waypoint))
        rospy.loginfo("Sampling done at waypoint %d, FCU manages mode transition", self.current_waypoint)

    def _call_automation_service(self, action):
        """调用自动化服务。"""
        service_map = {
            'start': '/usv/automation_start',
            'stop': '/usv/automation_stop',
            'pause': '/usv/automation_pause',
            'resume': '/usv/automation_resume'
        }

        service_name = service_map.get(action)
        if not service_name:
            return False

        try:
            rospy.wait_for_service(service_name, timeout=2.0)
            service = rospy.ServiceProxy(service_name, Trigger)
            resp = service()
            rospy.loginfo("Automation %s: %s", action, resp.message)
            return resp.success
        except Exception as e:
            rospy.logerr("Automation service error: %s", str(e))
            return False

    def _publish_status(self, status):
        """发布状态。"""
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def _payload_to_uint64_list(self, payload):
        """将字节载荷转为 uint64 列表 (8 字节对齐)。"""
        remainder = len(payload) % 8
        if remainder:
            payload += b'\x00' * (8 - remainder)
        result = []
        for i in range(0, len(payload), 8):
            val = struct.unpack_from('<Q', payload, i)[0]
            result.append(val)
        return result

    def _send_command_ack(self, command, result, target_system, target_component):
        """
        发送 COMMAND_ACK (msgid=77) 回传给命令发送方。

        COMMAND_ACK 载荷格式 (10 bytes, little-endian):
          offset 0: command          uint16 (被确认的命令 ID)
          offset 2: result           uint8  (MAV_RESULT)
          offset 3: progress         uint8  (0xFF = 不支持进度)
          offset 4: result_param2    int32  (附加结果参数, 0)
          offset 8: target_system    uint8
          offset 9: target_component uint8
        """
        payload = struct.pack('<HBBiBB',
                              command,
                              result,
                              0xFF,  # progress: not supported
                              0,     # result_param2
                              target_system,
                              target_component)

        mavlink_msg = Mavlink()
        mavlink_msg.header.stamp = rospy.Time.now()
        mavlink_msg.framing_status = 1  # MAVLINK_FRAMING_OK
        mavlink_msg.magic = 253  # MAVLink v2
        mavlink_msg.len = len(payload)
        mavlink_msg.sysid = self._source_system_id
        mavlink_msg.compid = self._source_component_id
        mavlink_msg.msgid = MAVLINK_MSG_ID_COMMAND_ACK
        mavlink_msg.payload64 = self._payload_to_uint64_list(payload)

        self.mavlink_to_pub.publish(mavlink_msg)
        rospy.loginfo("Sent COMMAND_ACK: cmd=%d result=%d target=%d/%d",
                      command, result, target_system, target_component)

    def handle_mavlink_command(self, cmd_id, param1=0, param2=0):
        """
        处理 MAVLink 自定义指令。

        31010: param2 > 0 表示飞控 NAV_SCRIPT_TIME 原生触发(不切模式)，
               param2 == 0 表示 QGC 手动按钮触发。
        31011~31014: 停止/暂停/恢复/校准
        31015/31016: 走航采样开启/停止
        """
        rospy.loginfo("Processing MAVLink command: %d param1=%.1f param2=%.1f", cmd_id, param1, param2)

        if cmd_id == CMD_START_SAMPLING:
            if param2 > 0:
                # 飞控原生触发（NAV_SCRIPT_TIME）：不切 HOLD、不判稳定
                return self._do_fcu_sample(int(param2))
            else:
                # QGC 手动按钮或 Web 触发
                return self._do_manual_sample()
        elif cmd_id == CMD_STOP_SAMPLING:
            self._stop_sampling_sequence()
            return True
        elif cmd_id == CMD_PAUSE_SAMPLING:
            self._pause_sampling()
            return True
        elif cmd_id == CMD_RESUME_SAMPLING:
            self._resume_sampling()
            return True
        elif cmd_id == CMD_CALIBRATE:
            if self.is_sampling:
                rospy.logwarn("Cannot calibrate while sampling is active")
                return False
            rospy.loginfo("Calibration command received, sending CALXYZA to pump")
            self._publish_status("calibrate_started")
            cmd_msg = String()
            cmd_msg.data = "CALXYZA\r\n"
            self.pump_command_pub.publish(cmd_msg)
            return True
        elif cmd_id == CMD_START_SURVEY:
            return self._start_survey(param1)
        elif cmd_id == CMD_STOP_SURVEY:
            return self._stop_survey()
        else:
            rospy.logwarn("Unknown command: %d", cmd_id)
            return False

    def _do_manual_sample(self):
        """手动采样：不切模式，不判稳定，直接执行当前配置的采样序列。"""
        if self.is_sampling:
            rospy.logwarn("Sampling already in progress")
            return False

        config = self._load_config() or self._get_default_config()
        steps_data = self._build_steps_payload(config, self.current_waypoint)

        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)

        self._set_mission_state(MissionState.SAMPLING, str(self.current_waypoint))
        self.is_sampling = True
        if not self._call_automation_service('start'):
            self.is_sampling = False
            self._set_mission_state(MissionState.FAILED, "manual_start_failed")
            return False

        rospy.loginfo("Manual sampling started (no HOLD, no stable wait)")
        return True

    def _do_fcu_sample(self, sample_id):
        """飞控原生定点采样：飞控已暂停 mission（NavScriptTime），不切模式、不判稳定。

        采样完成后 bridge 会通过 USV_DONE 通知飞控恢复 mission。
        """
        if self.is_sampling:
            rospy.logwarn("Sampling already in progress")
            return False

        config = self._load_config() or self._get_default_config()
        steps_data = self._build_steps_payload(config, self.current_waypoint)

        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)

        self._set_mission_state(MissionState.SAMPLING, str(self.current_waypoint))
        self.is_sampling = True
        if not self._call_automation_service('start'):
            self.is_sampling = False
            self._set_mission_state(MissionState.FAILED, "fcu_sample_start_failed")
            return False

        rospy.loginfo("FCU-triggered sampling started (id=%d, no HOLD, no stable wait)", sample_id)
        return True

    def _start_survey(self, interval=0):
        """启动走航采样：边走边测，不切 HOLD。"""
        if self._survey_active:
            rospy.logwarn("Survey already active")
            return False
        if self.is_sampling:
            rospy.logwarn("Cannot start survey while point sampling is active")
            return False

        self._survey_interval = max(1.0, float(interval) if interval > 0 else 5.0)
        self._survey_active = True
        self._survey_thread = threading.Thread(target=self._survey_loop, daemon=True)
        self._survey_thread.start()
        rospy.loginfo("Survey started, interval=%.1fs", self._survey_interval)
        self._publish_status("survey_started")
        return True

    def _stop_survey(self):
        """停止走航采样。"""
        if not self._survey_active:
            rospy.logwarn("Survey not active")
            return True
        self._survey_active = False
        rospy.loginfo("Survey stopped")
        self._publish_status("survey_stopped")
        return True

    def _survey_loop(self):
        """走航采样循环线程：按间隔反复触发单次采样。"""
        rospy.loginfo("Survey loop thread started, interval=%.1fs", self._survey_interval)
        while self._survey_active and not rospy.is_shutdown():
            if not self.is_sampling:
                config = self._load_config() or self._get_default_config()
                steps_data = self._build_steps_payload(config, self.current_waypoint)
                # 走航模式强制 loop_count=1，单次采样
                steps_data['loop_count'] = 1

                msg = String()
                msg.data = json.dumps(steps_data)
                self.steps_pub.publish(msg)

                self.is_sampling = True
                self._call_automation_service('start')
                # 等待本次采样完成
                while self.is_sampling and self._survey_active and not rospy.is_shutdown():
                    rospy.sleep(0.2)

            # 采样完成后等待间隔
            wait_end = time.time() + self._survey_interval
            while self._survey_active and not rospy.is_shutdown() and time.time() < wait_end:
                rospy.sleep(0.2)

        self._survey_active = False
        rospy.loginfo("Survey loop thread exited")

    def _trigger_srv_cb(self, req):
        """
        ROS 服务回调: 手动触发采样。
        可通过 rosservice call /usv/trigger_sampling 或其他 ROS 节点调用。
        """
        success = self._start_sampling_sequence(self.current_waypoint)
        return TriggerResponse(
            success=success,
            message="Sampling triggered" if success else "Sampling already in progress"
        )

    def run(self):
        """主循环。"""
        self._init_services()
        self.wait_for_mavros()
        self._set_mission_state(MissionState.IDLE)
        rospy.loginfo("MAVLink Trigger Node ready")

        self._trigger_srv = rospy.Service('/usv/trigger_sampling', Trigger, self._trigger_srv_cb)

        rate = rospy.Rate(1)
        prev_connected = False
        prev_mode = ""
        while not rospy.is_shutdown():
            with self.state_lock:
                connected = self.mavros_connected
                mode = self.mavros_state.mode
                armed = self.mavros_state.armed

            if prev_connected and not connected:
                rospy.logwarn("MAVROS connection lost")
                self._publish_status("mavros_disconnected")
            elif not prev_connected and connected:
                rospy.loginfo("MAVROS connection restored")
                self._publish_status("mavros_connected")
            prev_connected = connected

            if mode != prev_mode and not self.is_sampling:
                if mode == 'AUTO':
                    self._set_mission_state(MissionState.NAVIGATING, str(self.current_waypoint))
                elif mode == 'HOLD' and self.current_mission_state == MissionState.IDLE:
                    self._set_mission_state(MissionState.HOLD_NO_MISSION, str(self.current_waypoint))
            prev_mode = mode

            rospy.logdebug_throttle(10, "Mode: %s, Armed: %s, Sampling: %s, Connected: %s, v=%.3f, yaw=%.3f",
                                    mode, armed, self.is_sampling, connected,
                                    self.last_linear_speed, self.last_yaw_rate)
            rate.sleep()


def main():
    try:
        node = MAVLinkTriggerNode()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("MAVLink Trigger Node error: %s", str(e))


if __name__ == '__main__':
    main()
