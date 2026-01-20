#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
import threading

import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger
from mavros_msgs.msg import State, WaypointReached, CommandCode
from mavros_msgs.srv import SetMode, SetModeRequest, CommandLong, CommandLongRequest

# 自定义 MAVLink 指令 ID
CMD_START_SAMPLING = 31010
CMD_STOP_SAMPLING = 31011
CMD_PAUSE_SAMPLING = 31012
CMD_RESUME_SAMPLING = 31013

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

        # Subscribers
        self.state_sub = rospy.Subscriber('/mavros/state', State, self._state_cb)
        self.waypoint_sub = rospy.Subscriber('/mavros/mission/reached', WaypointReached, self._waypoint_cb)

        # 监听 MAVROS 的命令话题 (用于接收自定义指令)
        # 注意: 实际实现可能需要使用 mavros/cmd/command 服务或自定义插件
        self.cmd_sub = rospy.Subscriber('/mavros/cmd/command', CommandCode, self._cmd_cb, queue_size=10)

        rospy.loginfo("MAVLink Trigger Node initialized")
        rospy.loginfo("  Auto trigger on waypoint: %s", self.auto_trigger_on_waypoint)

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

    def _cmd_cb(self, msg):
        """
        MAVLink 命令回调。
        注意: 这是一个简化实现，实际可能需要使用 mavros 插件或 pymavlink
        """
        # 这里的实现取决于 MAVROS 的具体配置
        # 通常需要通过 mavros/cmd/command 服务或自定义话题
        pass

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

    def handle_mavlink_command(self, cmd_id, param1=0, param2=0):
        """
        处理 MAVLink 自定义指令。
        
        可以通过以下方式调用:
        1. QGC 的 MAVLink Inspector
        2. pymavlink 脚本
        3. ROS 话题/服务
        
        Args:
            cmd_id: 指令 ID
            param1: 参数1
            param2: 参数2
        """
        rospy.loginfo("Received MAVLink command: %d", cmd_id)

        if cmd_id == CMD_START_SAMPLING:
            self._start_sampling_sequence()
        elif cmd_id == CMD_STOP_SAMPLING:
            self._stop_sampling_sequence()
        elif cmd_id == CMD_PAUSE_SAMPLING:
            self._pause_sampling()
        elif cmd_id == CMD_RESUME_SAMPLING:
            self._resume_sampling()
        else:
            rospy.logwarn("Unknown command: %d", cmd_id)

    def run(self):
        """主循环。"""
        # 初始化服务
        self._init_services()

        # 等待 MAVROS (非阻塞)
        self.wait_for_mavros()

        self._publish_status("ready")
        rospy.loginfo("MAVLink Trigger Node ready")

        # 创建一个简单的 ROS 服务来接收触发指令
        # 这允许通过 rosservice call 或 Web 接口触发
        def trigger_cb(req):
            from std_srvs.srv import TriggerResponse
            self._start_sampling_sequence()
            return TriggerResponse(success=True, message="Sampling triggered")

        trigger_srv = rospy.Service('/usv/trigger_sampling', Trigger, trigger_cb)

        rate = rospy.Rate(1)
        while not rospy.is_shutdown():
            # 状态监控
            with self.state_lock:
                mode = self.mavros_state.mode
                armed = self.mavros_state.armed

            rospy.logdebug_throttle(10, "Mode: %s, Armed: %s, Sampling: %s",
                                    mode, armed, self.is_sampling)

            # 检查采样完成 (通过监听泵状态)
            # 这里可以添加更复杂的逻辑

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
