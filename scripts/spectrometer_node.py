#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spectrometer Node (分光检测器节点)
==================================
通过 NI DAQmx 采集分光光度检测器的电压信号。

Target: Jetson Nano
ROS: Noetic
Python: 3.8

硬件连接:
  - NI DAQ 采集卡 (USB) -> 分光检测器光电二极管

Topics:
  - Publishes: /usv/spectrometer_voltage (std_msgs/Float64) - 实时电压
  - Publishes: /usv/spectrometer_status (std_msgs/String) - 设备状态

Services:
  - /usv/spectrometer_start (std_srvs/Trigger) - 开始采集
  - /usv/spectrometer_stop (std_srvs/Trigger) - 停止采集

注意:
  - Jetson Nano 上需要安装 NI-DAQmx Linux 驱动
  - 如果 nidaqmx 不可用，节点将以模拟模式运行
"""

from __future__ import print_function

import threading
import time
import rospy
from std_msgs.msg import Float64, String
from std_srvs.srv import Trigger, TriggerResponse

# 尝试导入 nidaqmx
try:
    import nidaqmx
    from nidaqmx.constants import TerminalConfiguration
    NIDAQMX_AVAILABLE = True
except ImportError:
    NIDAQMX_AVAILABLE = False
    nidaqmx = None
    TerminalConfiguration = None
    rospy.logwarn("nidaqmx not available, running in simulation mode")


class DAQReader(object):
    """
    NI DAQmx 数据采集读取器。
    在后台线程中持续采集电压数据。
    """

    def __init__(self, channel_name, sample_rate=100):
        """
        初始化 DAQ 读取器。

        Args:
            channel_name: DAQ 通道名称 (如 "Dev1/ai0")
            sample_rate: 采样率 (Hz)
        """
        self.channel_name = channel_name
        self.sample_rate = sample_rate
        self.running = False
        self.task = None
        self.read_thread = None

        # 回调
        self.on_data = None  # func(float) - 电压值

        # 模拟模式
        self.simulation_mode = not NIDAQMX_AVAILABLE

    def start(self, callback=None):
        """开始采集。"""
        if self.running:
            return True

        self.on_data = callback
        self.running = True

        if self.simulation_mode:
            rospy.logwarn("DAQ running in SIMULATION mode")
            self.read_thread = threading.Thread(target=self._sim_loop, daemon=True)
        else:
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)

        self.read_thread.start()
        return True

    def stop(self):
        """停止采集。"""
        self.running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=2.0)

        if self.task:
            try:
                self.task.close()
            except Exception:
                pass
            self.task = None

    def _read_loop(self):
        """实际 DAQ 读取循环。"""
        try:
            self.task = nidaqmx.Task()
            self.task.ai_channels.add_ai_voltage_chan(
                self.channel_name,
                terminal_config=TerminalConfiguration.RSE
            )

            interval = 1.0 / self.sample_rate

            while self.running and not rospy.is_shutdown():
                start_time = time.time()

                try:
                    voltage = self.task.read()
                    if self.on_data:
                        self.on_data(voltage)
                except Exception as e:
                    rospy.logerr("DAQ read error: %s", str(e))
                    break

                # 控制采样率
                elapsed = time.time() - start_time
                sleep_time = max(0, interval - elapsed)
                time.sleep(sleep_time)

        except Exception as e:
            rospy.logerr("DAQ task error: %s", str(e))
        finally:
            if self.task:
                self.task.close()
                self.task = None

    def _sim_loop(self):
        """模拟数据循环 (用于测试)。"""
        import math
        t = 0.0
        interval = 1.0 / self.sample_rate

        while self.running and not rospy.is_shutdown():
            # 生成模拟电压信号 (带噪声的正弦波)
            import random
            voltage = 2.5 + 0.5 * math.sin(t * 0.5) + random.uniform(-0.05, 0.05)

            if self.on_data:
                self.on_data(voltage)

            t += interval
            time.sleep(interval)


class SpectrometerNode(object):
    """
    分光检测器 ROS 节点。
    管理 NI DAQ 采集卡，发布电压数据到 ROS 话题。
    """

    def __init__(self):
        """初始化节点。"""
        rospy.init_node('spectrometer_node', anonymous=False)

        # 参数
        self.device_name = rospy.get_param('~device_name', 'Dev1')
        self.channel = rospy.get_param('~channel', 'ai0')
        self.sample_rate = rospy.get_param('~sample_rate', 100)
        self.auto_start = rospy.get_param('~auto_start', False)

        # 完整通道名
        self.channel_name = "{}/{}".format(self.device_name, self.channel)

        # DAQ 读取器
        self.daq_reader = DAQReader(self.channel_name, self.sample_rate)

        # 状态
        self.is_acquiring = False
        self.latest_voltage = 0.0
        self.voltage_lock = threading.Lock()

        # Publishers
        self.voltage_pub = rospy.Publisher(
            '/usv/spectrometer_voltage', Float64, queue_size=10
        )
        self.status_pub = rospy.Publisher(
            '/usv/spectrometer_status', String, queue_size=10
        )

        # Services
        self.start_srv = rospy.Service(
            '/usv/spectrometer_start', Trigger, self._start_callback
        )
        self.stop_srv = rospy.Service(
            '/usv/spectrometer_stop', Trigger, self._stop_callback
        )
        self.reconfigure_srv = rospy.Service(
            '/usv/spectrometer_reconfigure', Trigger, self._reconfigure_callback
        )

        rospy.loginfo("Spectrometer Node initialized")
        rospy.loginfo("  Channel: %s", self.channel_name)
        rospy.loginfo("  Sample rate: %d Hz", self.sample_rate)
        rospy.loginfo("  DAQmx available: %s", NIDAQMX_AVAILABLE)

    def _on_voltage(self, voltage):
        """电压数据回调。"""
        with self.voltage_lock:
            self.latest_voltage = voltage

        # 发布电压
        msg = Float64()
        msg.data = voltage
        self.voltage_pub.publish(msg)

    def start_acquisition(self):
        """开始数据采集。"""
        if self.is_acquiring:
            return True

        success = self.daq_reader.start(callback=self._on_voltage)
        if success:
            self.is_acquiring = True
            self._publish_status("acquiring")
            rospy.loginfo("Spectrometer acquisition started")
        return success

    def stop_acquisition(self):
        """停止数据采集。"""
        if not self.is_acquiring:
            return True

        self.daq_reader.stop()
        self.is_acquiring = False
        self._publish_status("stopped")
        rospy.loginfo("Spectrometer acquisition stopped")
        return True

    def _start_callback(self, req):
        """开始采集服务回调。"""
        success = self.start_acquisition()
        return TriggerResponse(
            success=success,
            message="Acquisition started" if success else "Start failed"
        )

    def _stop_callback(self, req):
        """停止采集服务回调。"""
        success = self.stop_acquisition()
        return TriggerResponse(
            success=success,
            message="Acquisition stopped" if success else "Stop failed"
        )

    def _reconfigure_callback(self, req):
        """运行时重建 DAQ 设备服务回调。从 ROS 参数读取最新配置并重建。"""
        try:
            new_device = rospy.get_param('~device_name', self.device_name)
            new_channel = rospy.get_param('~channel', self.channel)
            new_rate = rospy.get_param('~sample_rate', self.sample_rate)

            rospy.loginfo("Reconfiguring DAQ: %s/%s @ %d Hz (was %s/%s @ %d Hz)",
                          new_device, new_channel, new_rate,
                          self.device_name, self.channel, self.sample_rate)

            was_acquiring = self.is_acquiring
            if self.is_acquiring:
                self.stop_acquisition()

            self.device_name = new_device
            self.channel = new_channel
            self.sample_rate = new_rate
            self.channel_name = "{}/{}".format(self.device_name, self.channel)

            self.daq_reader = DAQReader(self.channel_name, self.sample_rate)

            if was_acquiring:
                self.start_acquisition()

            msg = "Reconfigured to %s @ %d Hz" % (self.channel_name, self.sample_rate)
            rospy.loginfo(msg)
            self._publish_status("reconfigured")
            return TriggerResponse(success=True, message=msg)

        except Exception as e:
            msg = "Reconfigure error: %s" % str(e)
            rospy.logerr(msg)
            self._publish_status("error: " + str(e))
            return TriggerResponse(success=False, message=msg)

    def _publish_status(self, status):
        """发布状态消息。"""
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def get_latest_voltage(self):
        """获取最新电压值。"""
        with self.voltage_lock:
            return self.latest_voltage

    def run(self):
        """主循环。"""
        # 自动开始采集
        if self.auto_start:
            self.start_acquisition()

        rate = rospy.Rate(1)  # 1Hz 状态检查

        while not rospy.is_shutdown():
            if self.is_acquiring:
                voltage = self.get_latest_voltage()
                rospy.loginfo_throttle(10, "Voltage: %.4f V", voltage)
            rate.sleep()

        # 清理
        rospy.loginfo("Shutting down spectrometer...")
        self.stop_acquisition()


def main():
    """主入口。"""
    try:
        node = SpectrometerNode()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("Spectrometer Node error: %s", str(e))


if __name__ == '__main__':
    main()
