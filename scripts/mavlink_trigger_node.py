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
import os
import struct
import threading

import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger, TriggerResponse
from mavros_msgs.msg import State, WaypointReached, Mavlink
from mavros_msgs.srv import SetMode, SetModeRequest

# 自定义 MAVLink 指令 ID
CMD_START_SAMPLING = 31010
CMD_STOP_SAMPLING = 31011
CMD_PAUSE_SAMPLING = 31012
CMD_RESUME_SAMPLING = 31013
CMD_CALIBRATE = 31014

# MAVLink 协议常量
MAVLINK_MSG_ID_COMMAND_LONG = 76
MAVLINK_MSG_ID_COMMAND_ACK = 77

# MAV_RESULT enum
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_TEMPORARILY_REJECTED = 1
MAV_RESULT_FAILED = 5

# 配置文件路径
CONFIG_FILE = os.path.expanduser("~/usv_ws/config/sampling_config.json")


class MAVLinkTriggerNode(object):
    """
    MAVLink 指令触发节点。
    监听 MAVLink 指令和航点事件，触发采样任务。
    """

    def __init__(self):
        rospy.init_node('mavlink_trigger_node', anonymous=False)

        # 参数
        self.mavros_timeout = rospy.get_param('~mavros_timeout', 30.0)
        self.auto_trigger_on_waypoint = rospy.get_param('~auto_trigger_on_waypoint', True)
        self.trigger_waypoints = rospy.get_param('~trigger_waypoints', [])  # 空列表表示所有航点

        # 状态
        self.mavros_state = State()
        self.mavros_connected = False
        self.is_sampling = False
        self.current_waypoint = 0
        self.state_lock = threading.Lock()

        # 服务客户端
        self.set_mode_client = None

        # Publishers
        self.status_pub = rospy.Publisher('/usv/trigger_status', String, queue_size=10)
        self.steps_pub = rospy.Publisher('/usv/automation_steps', String, queue_size=1)
        self.pump_command_pub = rospy.Publisher('/usv/pump_command', String, queue_size=10)
        # MAVLink ACK 发送通道
        self.mavlink_to_pub = rospy.Publisher(
            '/mavros/mavlink/to', Mavlink, queue_size=10
        )

        # Subscribers
        self.state_sub = rospy.Subscriber('/mavros/state', State, self._state_cb)
        self.waypoint_sub = rospy.Subscriber('/mavros/mission/reached', WaypointReached, self._waypoint_cb)

        # 监听 MAVROS 原始 MAVLink 入站消息 (从飞控/GCS 收到的所有 MAVLink 帧)
        # mavros_msgs/Mavlink 包含 msgid、payload64 等字段
        # TODO: QGC sends COMMAND_LONG with compId=1 (autopilot). The autopilot
        # NAKs unknown cmds 31010-31014, but MAVROS forwards all /from messages
        # so we still receive them. Ideally use compId=191 (ONBOARD_COMPUTER),
        # but this requires MAVROS routing verification.
        self.mavlink_sub = rospy.Subscriber(
            '/mavros/mavlink/from', Mavlink, self._mavlink_from_cb, queue_size=20
        )

        rospy.loginfo("MAVLink Trigger Node initialized")
        rospy.loginfo("  Auto trigger on waypoint: %s", self.auto_trigger_on_waypoint)
        rospy.loginfo("  Listening for COMMAND_LONG on /mavros/mavlink/from")

    def _state_cb(self, msg):
        """MAVROS 状态回调。"""
        with self.state_lock:
            self.mavros_state = msg
            self.mavros_connected = msg.connected

    def _waypoint_cb(self, msg):
        """航点到达回调。"""
        self.current_waypoint = msg.wp_seq
        rospy.loginfo("Waypoint %d reached", msg.wp_seq)
        self._publish_status("waypoint_reached:{}".format(msg.wp_seq))

        # 自动触发采样
        if self.auto_trigger_on_waypoint:
            # 检查是否在触发列表中 (空列表表示所有航点)
            if not self.trigger_waypoints or msg.wp_seq in self.trigger_waypoints:
                rospy.loginfo("Auto-triggering sampling at waypoint %d", msg.wp_seq)
                self._start_sampling_sequence()

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

            # 仅处理 USV 自定义命令范围 (31010~31014)
            if command < CMD_START_SAMPLING or command > CMD_CALIBRATE:
                return

            rospy.loginfo(
                "Received COMMAND_LONG from sysid=%d compid=%d: cmd=%d param1=%.1f param2=%.1f",
                msg.sysid, msg.compid, command, param1, param2
            )

            success = self.handle_mavlink_command(command, param1, param2)

            # 根据命令执行结果决定 ACK 类型
            if success:
                ack_result = MAV_RESULT_ACCEPTED
            elif command == CMD_START_SAMPLING and self.is_sampling:
                ack_result = MAV_RESULT_TEMPORARILY_REJECTED
            else:
                ack_result = MAV_RESULT_FAILED

            self._send_command_ack(command, ack_result, msg.sysid, msg.compid)

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

    def _load_config(self):
        """加载配置文件。"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            rospy.logwarn("Failed to load config: %s", str(e))
        return None

    def _start_sampling_sequence(self):
        """启动采样序列。"""
        if self.is_sampling:
            rospy.logwarn("Sampling already in progress")
            return False

        rospy.loginfo("Starting sampling sequence...")
        self._publish_status("sampling_started")

        # 1. 切换到 HOLD 模式
        self.set_mode("HOLD")
        rospy.sleep(1.0)

        # 2. 加载最新配置
        config = self._load_config()
        if not config:
            rospy.logwarn("No config found, using defaults")
            config = {
                "sampling_sequence": {"steps": [], "loop_count": 1},
                "pump_settings": {"pid_mode": True, "pid_precision": 0.1}
            }

        # 3. 发送步骤到泵控制节点
        steps_data = {
            "steps": config.get('sampling_sequence', {}).get('steps', []),
            "loop_count": config.get('sampling_sequence', {}).get('loop_count', 1),
            "pid_mode": config.get('pump_settings', {}).get('pid_mode', True),
            "pid_precision": config.get('pump_settings', {}).get('pid_precision', 0.1)
        }

        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)

        # 4. 触发自动化启动
        self._call_automation_service('start')

        self.is_sampling = True
        return True

    def _stop_sampling_sequence(self):
        """停止采样序列。"""
        rospy.loginfo("Stopping sampling sequence...")
        self._call_automation_service('stop')
        self.is_sampling = False
        self._publish_status("sampling_stopped")

        # 恢复 AUTO 模式
        rospy.sleep(1.0)
        self.set_mode("AUTO")

    def _pause_sampling(self):
        """暂停采样。"""
        self._call_automation_service('pause')
        self._publish_status("sampling_paused")

    def _resume_sampling(self):
        """恢复采样。"""
        self._call_automation_service('resume')
        self._publish_status("sampling_resumed")

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
        mavlink_msg.sysid = 1
        mavlink_msg.compid = 191  # MAV_COMP_ID_ONBOARD_COMPUTER
        mavlink_msg.msgid = MAVLINK_MSG_ID_COMMAND_ACK
        mavlink_msg.payload64 = self._payload_to_uint64_list(payload)

        self.mavlink_to_pub.publish(mavlink_msg)
        rospy.loginfo("Sent COMMAND_ACK: cmd=%d result=%d target=%d/%d",
                      command, result, target_system, target_component)

    def handle_mavlink_command(self, cmd_id, param1=0, param2=0):
        """
        处理 MAVLink 自定义指令。

        调用来源:
        1. QGC USVPayloadPanel 按钮 (COMMAND_LONG via MAVLink)
        2. QGC MAVLink Inspector
        3. pymavlink 脚本
        4. ROS 服务 /usv/trigger_sampling

        Args:
            cmd_id: 指令 ID (31010~31014)
            param1: 参数1
            param2: 参数2

        Returns:
            bool: 命令是否被成功处理
        """
        rospy.loginfo("Processing MAVLink command: %d", cmd_id)

        if cmd_id == CMD_START_SAMPLING:
            return self._start_sampling_sequence()
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
        else:
            rospy.logwarn("Unknown command: %d", cmd_id)
            return False

    def _trigger_srv_cb(self, req):
        """
        ROS 服务回调: 手动触发采样。
        可通过 rosservice call /usv/trigger_sampling 或其他 ROS 节点调用。
        """
        success = self._start_sampling_sequence()
        return TriggerResponse(
            success=success,
            message="Sampling triggered" if success else "Sampling already in progress"
        )

    def run(self):
        """主循环。"""
        # 初始化服务
        self._init_services()

        # 等待 MAVROS (非阻塞)
        self.wait_for_mavros()

        self._publish_status("ready")
        rospy.loginfo("MAVLink Trigger Node ready")

        # ROS 服务入口: 允许通过 rosservice call 或其他 ROS 节点直接触发采样
        self._trigger_srv = rospy.Service(
            '/usv/trigger_sampling', Trigger, self._trigger_srv_cb
        )

        rate = rospy.Rate(1)
        prev_connected = False
        while not rospy.is_shutdown():
            # 状态监控
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

            rospy.logdebug_throttle(10, "Mode: %s, Armed: %s, Sampling: %s, Connected: %s",
                                    mode, armed, self.is_sampling, connected)

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
