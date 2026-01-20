#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mission Coordinator Node (任务协调节点)
=======================================
协调无人船航行与水质监测任务，与 Pixhawk 通过 MAVROS 通信。

Target: Jetson Nano
ROS: Noetic
Python: 3.8

职责:
  1. 监听 MAVROS 航点到达事件
  2. 到达采样点时切换为 HOLD 模式，触发水质检测
  3. 检测完成后恢复 AUTO 模式继续航行
  4. 汇总检测结果发送回地面站

Topics:
  - Subscribes: /mavros/state - 飞控状态
  - Subscribes: /mavros/mission/reached - 航点到达
  - Subscribes: /usv/spectrometer_voltage - 分光检测器数据
  - Subscribes: /usv/pump_status - 泵状态
  - Publishes:  /usv/mission_status - 任务状态
  - Publishes:  /usv/detection_result - 检测结果

Services:
  - Calls: /mavros/set_mode - 设置飞行模式
  - Calls: /usv/pump_command - 控制泵
  - Calls: /usv/spectrometer_start/stop - 控制采集
"""

from __future__ import print_function

import threading
import rospy
from std_msgs.msg import String, Float64, UInt16
from mavros_msgs.msg import State, WaypointReached
from mavros_msgs.srv import SetMode, SetModeRequest


class MissionState:
    """任务状态枚举。"""
    IDLE = "IDLE"                    # 空闲
    NAVIGATING = "NAVIGATING"        # 航行中
    HOLDING = "HOLDING"              # 悬停等待
    SAMPLING = "SAMPLING"            # 采样中
    DETECTING = "DETECTING"          # 检测中
    COMPLETED = "COMPLETED"          # 检测完成
    ERROR = "ERROR"                  # 错误


class MissionCoordinatorNode(object):
    """
    任务协调 ROS 节点。
    协调无人船航行与水质监测流程。
    """

    def __init__(self):
        """初始化节点。"""
        rospy.init_node('mission_coordinator_node', anonymous=False)

        # 参数
        self.mavros_timeout = rospy.get_param('~mavros_timeout', 30.0)
        self.sampling_duration = rospy.get_param('~sampling_duration', 10.0)
        self.detection_duration = rospy.get_param('~detection_duration', 5.0)

        # 状态
        self.mission_state = MissionState.IDLE
        self.mavros_state = State()
        self.mavros_connected = False
        self.current_waypoint = 0
        self.state_lock = threading.Lock()

        # 检测数据
        self.voltage_samples = []
        self.voltage_lock = threading.Lock()

        # Publishers
        self.mission_status_pub = rospy.Publisher(
            '/usv/mission_status', String, queue_size=10
        )
        self.detection_result_pub = rospy.Publisher(
            '/usv/detection_result', String, queue_size=10
        )
        self.pump_cmd_pub = rospy.Publisher(
            '/usv/pump_command', String, queue_size=10
        )

        # Subscribers
        self.state_sub = rospy.Subscriber(
            '/mavros/state', State, self._mavros_state_cb
        )
        self.waypoint_sub = rospy.Subscriber(
            '/mavros/mission/reached', WaypointReached, self._waypoint_reached_cb
        )
        self.voltage_sub = rospy.Subscriber(
            '/usv/spectrometer_voltage', Float64, self._voltage_cb
        )

        # Service clients (延迟初始化)
        self.set_mode_client = None

        rospy.loginfo("Mission Coordinator Node initialized")

    def _mavros_state_cb(self, msg):
        """MAVROS 状态回调。"""
        with self.state_lock:
            self.mavros_state = msg
            self.mavros_connected = msg.connected

    def _waypoint_reached_cb(self, msg):
        """航点到达回调。"""
        self.current_waypoint = msg.wp_seq
        rospy.loginfo("Waypoint %d reached!", msg.wp_seq)

        # 触发采样流程
        if self.mission_state == MissionState.NAVIGATING:
            self._start_sampling_sequence()

    def _voltage_cb(self, msg):
        """电压数据回调。"""
        with self.voltage_lock:
            self.voltage_samples.append(msg.data)
            # 限制缓冲区大小
            if len(self.voltage_samples) > 1000:
                self.voltage_samples = self.voltage_samples[-500:]

    def _init_services(self):
        """初始化 MAVROS 服务客户端。"""
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
                rospy.logerr("MAVROS connection timeout")
                return False

            rate.sleep()

        return False

    def set_mode(self, mode):
        """
        设置飞行模式。

        Args:
            mode: 模式字符串 (如 "HOLD", "AUTO", "GUIDED")

        Returns:
            bool: 是否成功
        """
        if not self.set_mode_client:
            rospy.logerr("Set mode service not initialized")
            return False

        try:
            req = SetModeRequest()
            req.custom_mode = mode
            resp = self.set_mode_client(req)

            if resp.mode_sent:
                rospy.loginfo("Mode set to: %s", mode)
                return True
            else:
                rospy.logwarn("Failed to set mode: %s", mode)
                return False

        except rospy.ServiceException as e:
            rospy.logerr("Set mode service error: %s", str(e))
            return False

    def _set_mission_state(self, state):
        """设置任务状态并发布。"""
        self.mission_state = state
        msg = String()
        msg.data = state
        self.mission_status_pub.publish(msg)
        rospy.loginfo("Mission state: %s", state)

    def _start_sampling_sequence(self):
        """开始采样序列。"""
        rospy.loginfo("Starting sampling sequence at waypoint %d", self.current_waypoint)

        # 1. 切换到 HOLD 模式
        self._set_mission_state(MissionState.HOLDING)
        if not self.set_mode("HOLD"):
            self._set_mission_state(MissionState.ERROR)
            return

        rospy.sleep(1.0)  # 等待稳定

        # 2. 启动泵进行采样
        self._set_mission_state(MissionState.SAMPLING)
        self._start_pumps()
        rospy.sleep(self.sampling_duration)
        self._stop_pumps()

        # 3. 进行光学检测
        self._set_mission_state(MissionState.DETECTING)
        self._clear_voltage_samples()
        rospy.sleep(self.detection_duration)

        # 4. 计算结果
        result = self._calculate_result()
        self._publish_result(result)

        # 5. 恢复航行
        self._set_mission_state(MissionState.COMPLETED)
        rospy.sleep(1.0)

        if self.set_mode("AUTO"):
            self._set_mission_state(MissionState.NAVIGATING)
        else:
            self._set_mission_state(MissionState.ERROR)

    def _start_pumps(self):
        """启动泵。"""
        # 示例: 启动 X 和 Y 泵进行进样
        cmd = String()
        cmd.data = "XEFV5JG"  # X轴连续转动
        self.pump_cmd_pub.publish(cmd)
        rospy.loginfo("Pumps started")

    def _stop_pumps(self):
        """停止泵。"""
        cmd = String()
        cmd.data = "STOP"
        self.pump_cmd_pub.publish(cmd)
        rospy.loginfo("Pumps stopped")

    def _clear_voltage_samples(self):
        """清空电压样本。"""
        with self.voltage_lock:
            self.voltage_samples = []

    def _calculate_result(self):
        """计算检测结果。"""
        with self.voltage_lock:
            samples = self.voltage_samples.copy()

        if not samples:
            return {"error": "No samples collected"}

        # 简单统计
        avg_voltage = sum(samples) / len(samples)
        max_voltage = max(samples)
        min_voltage = min(samples)

        # TODO: 实现实际的吸光度计算和浓度换算
        # 基于朗伯-比尔定律: A = log10(I0/I) = εcl

        result = {
            "waypoint": self.current_waypoint,
            "samples": len(samples),
            "avg_voltage": round(avg_voltage, 4),
            "max_voltage": round(max_voltage, 4),
            "min_voltage": round(min_voltage, 4),
            # "concentration": calculated_concentration
        }

        rospy.loginfo("Detection result: %s", result)
        return result

    def _publish_result(self, result):
        """发布检测结果。"""
        msg = String()
        parts = ["{}:{}".format(k, v) for k, v in result.items()]
        msg.data = ",".join(parts)
        self.detection_result_pub.publish(msg)

    def run(self):
        """主循环。"""
        # 初始化服务
        if not self._init_services():
            rospy.logerr("Failed to initialize services")
            return

        # 等待 MAVROS 连接
        if not self.wait_for_mavros():
            rospy.logerr("MAVROS not available")
            return

        self._set_mission_state(MissionState.NAVIGATING)

        rate = rospy.Rate(1)
        while not rospy.is_shutdown():
            # 状态监控
            with self.state_lock:
                mode = self.mavros_state.mode
                armed = self.mavros_state.armed

            rospy.logdebug_throttle(10, "Mode: %s, Armed: %s, State: %s",
                                    mode, armed, self.mission_state)
            rate.sleep()

        rospy.loginfo("Mission coordinator shutting down")


def main():
    """主入口。"""
    try:
        node = MissionCoordinatorNode()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("Mission Coordinator error: %s", str(e))


if __name__ == '__main__':
    main()
