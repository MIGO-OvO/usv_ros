#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pump Control Node (泵控制节点)
==============================
通过串口与 ESP32 下位机通信，控制微流控系统的蠕动泵电机 (X/Y/Z/A 四轴)。

支持:
- 传统开环模式 (J 指令)
- PID 闭环精确定位模式 (R 指令)
- 多步骤自动化序列执行
- PID 完成等待机制

Target: Jetson Nano
ROS: Noetic
Python: 3.8

Topics:
  - Sub: /usv/pump_command (String) - 直接指令
  - Sub: /usv/pump_step (String) - 步骤参数 (JSON)
  - Pub: /usv/pump_angles (String) - 实时角度
  - Pub: /usv/pump_status (String) - 状态信息
  - Pub: /usv/pump_pid_complete (String) - PID 完成通知

Services:
  - /usv/pump_stop (Trigger) - 紧急停止
  - /usv/automation_start (Trigger) - 启动自动化
  - /usv/automation_stop (Trigger) - 停止自动化
  - /usv/automation_pause (Trigger) - 暂停自动化
  - /usv/automation_resume (Trigger) - 恢复自动化
"""

from __future__ import print_function

import json
import os
import struct
import sys
import threading

import serial
import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger, TriggerResponse

# 添加 lib 目录到路径
script_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.join(script_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from command_generator import CommandGenerator, MOTOR_NAMES, COMMAND_TERMINATOR
from automation_engine import AutomationEngine

# 串口协议常量
HEADER1 = 0x55
HEADER2_ANGLE = 0xCC
HEADER2_PID = 0xAA
HEADER2_TEST = 0xBB
PACKET_SIZE_ANGLE = 20
PACKET_SIZE_PID = 29
PACKET_SIZE_TEST = 18
TAIL = 0x0A


class PumpSerialReader(object):
    """
    泵控制板串口读取器。
    支持文本响应和多种二进制数据包的混合协议解析。
    """

    def __init__(self, serial_conn):
        """初始化串口读取器。"""
        self.serial_conn = serial_conn
        self.running = False
        self.read_thread = None

        # 数据缓冲区
        self.binary_buffer = bytearray()
        self.text_buffer = ""

        # 回调函数
        self.on_angle_received = None   # func(dict) - 角度数据 [0xCC]
        self.on_pid_data_received = None  # func(dict) - PID 数据 [0xAA]
        self.on_test_result_received = None  # func(dict) - 测试结果 [0xBB]
        self.on_text_received = None    # func(str) - 文本响应

    def start(self, on_angle=None, on_pid=None, on_test=None, on_text=None):
        """启动后台读取线程。"""
        self.on_angle_received = on_angle
        self.on_pid_data_received = on_pid
        self.on_test_result_received = on_test
        self.on_text_received = on_text
        self.running = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()

    def stop(self):
        """停止读取线程。"""
        self.running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=2.0)
        self.binary_buffer.clear()
        self.text_buffer = ""

    def _read_loop(self):
        """后台读取循环。"""
        while self.running and not rospy.is_shutdown():
            try:
                if self.serial_conn and self.serial_conn.is_open:
                    if self.serial_conn.in_waiting > 0:
                        raw_data = self.serial_conn.read(self.serial_conn.in_waiting)
                        self._process_data(raw_data)
                rospy.sleep(0.005)
            except serial.SerialException as e:
                rospy.logerr("Serial read error: %s", str(e))
                break
            except Exception as e:
                rospy.logerr("Reader error: %s", str(e))

    def _process_data(self, raw_data):
        """处理接收到的数据。"""
        self.binary_buffer.extend(raw_data)

        while len(self.binary_buffer) > 0:
            header_info = self._find_header()

            if header_info is None:
                self._process_text(bytes(self.binary_buffer))
                self.binary_buffer.clear()
                break

            header_pos, packet_type, packet_size = header_info

            if header_pos > 0:
                self._process_text(bytes(self.binary_buffer[:header_pos]))
                del self.binary_buffer[:header_pos]

            if len(self.binary_buffer) < packet_size:
                break

            packet = bytes(self.binary_buffer[:packet_size])
            if self._validate_packet(packet, packet_type, packet_size):
                self._parse_packet(packet, packet_type)
                del self.binary_buffer[:packet_size]
            else:
                del self.binary_buffer[:1]

    def _find_header(self):
        """查找二进制帧头。"""
        for i in range(len(self.binary_buffer) - 1):
            if self.binary_buffer[i] == HEADER1:
                h2 = self.binary_buffer[i + 1]
                if h2 == HEADER2_ANGLE:
                    return (i, HEADER2_ANGLE, PACKET_SIZE_ANGLE)
                elif h2 == HEADER2_PID:
                    return (i, HEADER2_PID, PACKET_SIZE_PID)
                elif h2 == HEADER2_TEST:
                    return (i, HEADER2_TEST, PACKET_SIZE_TEST)
        return None

    def _validate_packet(self, data, packet_type, packet_size):
        """验证数据包校验和。"""
        if len(data) < packet_size:
            return False
        if data[0] != HEADER1 or data[1] != packet_type:
            return False
        if data[packet_size - 1] != TAIL:
            return False

        # 计算校验和
        if packet_type == HEADER2_ANGLE:
            checksum = 0
            for i in range(1, 18):
                checksum ^= data[i]
            return checksum == data[18]
        elif packet_type == HEADER2_PID:
            checksum = 0
            for i in range(2, 27):
                checksum ^= data[i]
            return checksum == data[27]
        elif packet_type == HEADER2_TEST:
            checksum = 0
            for i in range(2, 16):
                checksum ^= data[i]
            return checksum == data[16]

        return False

    def _parse_packet(self, data, packet_type):
        """解析数据包。"""
        try:
            if packet_type == HEADER2_ANGLE:
                self._parse_angle_packet(data)
            elif packet_type == HEADER2_PID:
                self._parse_pid_packet(data)
            elif packet_type == HEADER2_TEST:
                self._parse_test_packet(data)
        except Exception as e:
            rospy.logwarn("Packet parse error: %s", str(e))

    def _parse_angle_packet(self, data):
        """解析角度数据包 [0x55][0xCC]。"""
        angles = struct.unpack("<4f", data[2:18])
        angle_dict = {
            "X": round(angles[0], 3),
            "Y": round(angles[1], 3),
            "Z": round(angles[2], 3),
            "A": round(angles[3], 3)
        }
        if self.on_angle_received:
            self.on_angle_received(angle_dict)

    def _parse_pid_packet(self, data):
        """解析 PID 数据包 [0x55][0xAA]。"""
        motor_id = data[2]
        motor_names = ["X", "Y", "Z", "A"]
        packet = {
            "motor": motor_names[motor_id] if motor_id < 4 else "X",
            "motor_id": motor_id,
            "timestamp": struct.unpack("<I", data[3:7])[0],
            "target_angle": struct.unpack("<f", data[7:11])[0],
            "actual_angle": struct.unpack("<f", data[11:15])[0],
            "theo_angle": struct.unpack("<f", data[15:19])[0],
            "pid_out": struct.unpack("<f", data[19:23])[0],
            "error": struct.unpack("<f", data[23:27])[0],
        }
        if self.on_pid_data_received:
            self.on_pid_data_received(packet)

    def _parse_test_packet(self, data):
        """解析 PID 测试结果包 [0x55][0xBB]。"""
        motor_id = data[2]
        motor_names = ["X", "Y", "Z", "A"]
        result = {
            "motor": motor_names[motor_id] if motor_id < 4 else "X",
            "motor_id": motor_id,
            "run_index": data[3],
            "total_runs": data[4],
            "convergence_time_ms": struct.unpack("<H", data[5:7])[0],
            "max_overshoot": struct.unpack("<h", data[7:9])[0] / 100.0,
            "final_error": struct.unpack("<h", data[9:11])[0] / 100.0,
            "oscillation_count": data[11],
            "smoothness_score": data[12],
            "startup_jerk": struct.unpack("<H", data[13:15])[0] / 100.0,
            "total_score": data[15],
        }
        if self.on_test_result_received:
            self.on_test_result_received(result)

    def _process_text(self, data):
        """处理文本数据。"""
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("latin-1", errors="replace")

        self.text_buffer += text

        while "\n" in self.text_buffer:
            line, self.text_buffer = self.text_buffer.split("\n", 1)
            line = line.strip()
            if line and self.on_text_received:
                self.on_text_received(line)


class PumpControlNode(object):
    """
    泵控制 ROS 节点。
    集成指令生成器和自动化引擎。
    """

    INJECTION_PUMP_PREFIX = "PUMP:"

    def __init__(self):
        """初始化节点。"""
        rospy.init_node('pump_control_node', anonymous=False)

        # 参数
        self.serial_port = rospy.get_param('~serial_port', '/dev/ttyUSB0')
        self.baudrate = rospy.get_param('~baudrate', 115200)
        self.timeout = rospy.get_param('~timeout', 1.0)
        self.pid_mode = rospy.get_param('~pid_mode', True)
        self.pid_precision = rospy.get_param('~pid_precision', 0.1)

        # 串口连接
        self.serial_conn = None
        self.serial_reader = None
        self.serial_lock = threading.Lock()

        # 指令生成器
        self.command_generator = CommandGenerator()
        self.command_generator.set_pid_mode(self.pid_mode, self.pid_precision)

        # 自动化引擎
        self.automation_engine = AutomationEngine(
            command_generator=self.command_generator,
            send_command_func=self.send_command,
            log_func=lambda msg: rospy.loginfo("[Automation] %s", msg)
        )
        self.automation_engine.on_status_update = self._on_automation_status
        self.automation_engine.on_error = self._on_automation_error
        self.automation_engine.on_step_command = self._send_automation_step

        # 当前角度状态
        self.current_angles = {m: 0.0 for m in MOTOR_NAMES}
        self.angles_lock = threading.Lock()

        # PID 完成检测
        self.pid_target_angles = {}
        self.pid_precision_threshold = self.pid_precision

        # 进样泵状态
        self.inject_pump_enabled = False
        self.inject_pump_speed = 0
        self.inject_pump_last_response = ""
        self.inject_pump_last_error = ""

        # Publishers
        self.angles_pub = rospy.Publisher('/usv/pump_angles', String, queue_size=10)
        self.status_pub = rospy.Publisher('/usv/pump_status', String, queue_size=10)
        self.pid_complete_pub = rospy.Publisher('/usv/pump_pid_complete', String, queue_size=10)
        self.pid_error_pub = rospy.Publisher('/usv/pump_pid_error', String, queue_size=50)
        self.injection_status_pub = rospy.Publisher('/usv/injection_pump_status', String, queue_size=10)

        # Subscribers
        self.cmd_sub = rospy.Subscriber('/usv/pump_command', String, self._cmd_callback)
        self.step_sub = rospy.Subscriber('/usv/pump_step', String, self._step_callback)
        self.steps_sub = rospy.Subscriber('/usv/automation_steps', String, self._steps_callback)

        # Services
        self.stop_srv = rospy.Service('/usv/pump_stop', Trigger, self._stop_callback)
        self.auto_start_srv = rospy.Service('/usv/automation_start', Trigger, self._auto_start_callback)
        self.auto_stop_srv = rospy.Service('/usv/automation_stop', Trigger, self._auto_stop_callback)
        self.auto_pause_srv = rospy.Service('/usv/automation_pause', Trigger, self._auto_pause_callback)
        self.auto_resume_srv = rospy.Service('/usv/automation_resume', Trigger, self._auto_resume_callback)
        self.injection_on_srv = rospy.Service('/usv/injection_pump_on', Trigger, self._injection_on_callback)
        self.injection_off_srv = rospy.Service('/usv/injection_pump_off', Trigger, self._injection_off_callback)
        self.injection_status_srv = rospy.Service('/usv/injection_pump_get_status', Trigger, self._injection_status_callback)

        rospy.loginfo("Pump Control Node initialized")
        rospy.loginfo("  Serial: %s @ %d", self.serial_port, self.baudrate)
        rospy.loginfo("  PID Mode: %s (precision: %.2f)", self.pid_mode, self.pid_precision)

    def connect(self):
        """连接串口。"""
        try:
            self.serial_conn = serial.Serial(
                port=self.serial_port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            self._publish_injection_pump_status()

            # 启动读取器
            self.serial_reader = PumpSerialReader(self.serial_conn)
            self.serial_reader.start(
                on_angle=self._on_angle_received,
                on_pid=self._on_pid_data_received,
                on_test=self._on_test_result_received,
                on_text=self._on_text_received
            )

            rospy.loginfo("Connected to pump controller: %s", self.serial_port)
            self._publish_status("connected")
            return True

        except serial.SerialException as e:
            rospy.logerr("Failed to connect: %s", str(e))
            self._publish_status("error: " + str(e))
            return False

    def disconnect(self):
        """断开连接。"""
        if self.serial_reader:
            self.serial_reader.stop()
            self.serial_reader = None

        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception as e:
                rospy.logwarn("Error closing serial: %s", str(e))

        rospy.loginfo("Disconnected from pump controller")

    def send_command(self, command):
        """
        发送指令到电机控制板。

        Args:
            command: 指令字符串

        Returns:
            bool: 是否成功
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            rospy.logwarn("Serial not connected")
            return False

        try:
            if self._is_injection_pump_command(command):
                command = command.upper()

            with self.serial_lock:
                if not command.endswith(COMMAND_TERMINATOR):
                    command += COMMAND_TERMINATOR
                self.serial_conn.write(command.encode('utf-8'))
                self.serial_conn.flush()
            rospy.logdebug("Sent: %s", command.strip())
            return True

        except (serial.SerialException, OSError) as e:
            rospy.logerr("Send failed: %s", str(e))
            return False

    def _is_injection_pump_command(self, command):
        """判断是否为进样泵直通指令。"""
        return command.upper().startswith(self.INJECTION_PUMP_PREFIX)

    def _build_injection_pump_command(self, enabled, speed=None):
        """构建进样泵控制指令。"""
        if speed is not None:
            speed = max(0, min(100, int(speed)))
            return "PUMP:SET:{}".format(speed)
        return "PUMP:ON" if enabled else "PUMP:OFF"

    def _publish_injection_pump_status(self):
        """发布进样泵状态。"""
        msg = String()
        msg.data = json.dumps({
            "enabled": self.inject_pump_enabled,
            "speed": self.inject_pump_speed,
            "last_response": self.inject_pump_last_response,
            "last_error": self.inject_pump_last_error,
        })
        self.injection_status_pub.publish(msg)

    def _update_injection_pump_state(self, enabled=None, speed=None, response=None, error=None):
        """更新进样泵状态缓存并发布。"""
        if enabled is not None:
            self.inject_pump_enabled = enabled
        if speed is not None:
            self.inject_pump_speed = max(0, min(100, int(speed)))
        if response is not None:
            self.inject_pump_last_response = response
        if error is not None:
            self.inject_pump_last_error = error
        self._publish_injection_pump_status()

    def _send_injection_pump_command(self, enabled=None, speed=None):
        """发送进样泵控制指令。"""
        command = self._build_injection_pump_command(enabled=enabled, speed=speed)
        success = self.send_command(command)
        if success:
            if speed is not None:
                self._update_injection_pump_state(
                    enabled=speed > 0,
                    speed=speed,
                    response=command,
                    error=""
                )
            elif enabled is not None:
                self._update_injection_pump_state(
                    enabled=enabled,
                    response=command,
                    error=""
                )
        return success

    def _handle_injection_pump_step(self, step, mode):
        """处理步骤中的进样泵配置。"""
        pump_cfg = step.get("pump", {}) or {}
        if not isinstance(pump_cfg, dict) or "enable" not in pump_cfg:
            return True

        pump_enabled = bool(pump_cfg.get("enable", False))
        pump_speed = pump_cfg.get("speed", 0)

        try:
            pump_speed = int(pump_speed)
        except (TypeError, ValueError):
            rospy.logerr("Invalid injection pump speed: %s", str(pump_speed))
            return False

        if pump_enabled and pump_speed > 0:
            success = self._send_injection_pump_command(speed=pump_speed)
            if success and mode == "auto":
                rospy.loginfo("[Automation] Injection pump set to %s%%", pump_speed)
            return success

        if not pump_enabled and mode == "auto":
            return self._send_injection_pump_command(enabled=False)

        return True

    def _send_automation_step(self, step):
        """自动化引擎步骤发送钩子。"""
        command = self.command_generator.generate_command(step, mode="auto")
        if command and not self.send_command(command):
            return False
        if command:
            rospy.loginfo("[Automation] 指令已发送: %s", command.strip())
        return self._handle_injection_pump_step(step, mode="auto")

    def _parse_injection_pump_text(self, text):
        """解析进样泵文本响应。"""
        if text.startswith("PUMP_OK:SET="):
            payload = text[len("PUMP_OK:SET="):]
            parts = payload.split(",", 1)
            speed = int(parts[0]) if parts and parts[0].isdigit() else self.inject_pump_speed
            enabled = len(parts) > 1 and parts[1].upper() == "ON"
            self._update_injection_pump_state(enabled=enabled, speed=speed, response=text, error="")
            return True

        if text.startswith("PUMP_OK:SPD="):
            speed_text = text[len("PUMP_OK:SPD="):]
            speed = int(speed_text) if speed_text.isdigit() else self.inject_pump_speed
            self._update_injection_pump_state(speed=speed, response=text, error="")
            return True

        if text.startswith("PUMP_OK:ON"):
            self._update_injection_pump_state(enabled=True, response=text, error="")
            return True

        if text.startswith("PUMP_OK:OFF"):
            self._update_injection_pump_state(enabled=False, speed=0, response=text, error="")
            return True

        if text.startswith("PUMP_STATUS:"):
            payload = text[len("PUMP_STATUS:"):]
            enabled = payload.upper().startswith("ON")
            speed = self.inject_pump_speed
            if "SPD=" in payload:
                speed_text = payload.split("SPD=", 1)[1]
                speed = int(speed_text) if speed_text.isdigit() else speed
            self._update_injection_pump_state(enabled=enabled, speed=speed, response=text, error="")
            return True

        if text.startswith("PUMP_ERR:"):
            self._update_injection_pump_state(response=text, error=text)
            return True

        return False

    def stop_all_pumps(self):
        """紧急停止所有泵。"""
        # 先停止 PID
        self.send_command(self.command_generator.generate_pid_stop_command())
        # 再停止电机
        success = self.send_command(self.command_generator.generate_stop_command())
        injection_success = self._send_injection_pump_command(enabled=False)
        if success:
            rospy.logwarn("All pumps stopped!")
            self._publish_status("stopped")
        return success and injection_success

    def _on_angle_received(self, angles):
        """角度数据回调。"""
        with self.angles_lock:
            self.current_angles.update(angles)

        # 更新指令生成器
        self.command_generator.set_current_angles(angles)

        # 发布角度话题
        msg = String()
        parts = ["{}:{:.3f}".format(k, v) for k, v in angles.items()]
        msg.data = ",".join(parts)
        self.angles_pub.publish(msg)

        # 检查 PID 完成
        self._check_pid_complete(angles)

    def _on_pid_data_received(self, data):
        """PID 数据回调。"""
        rospy.logdebug("PID data: %s", data)
        # Publish PID error
        try:
            msg = String()
            msg.data = json.dumps(data)
            self.pid_error_pub.publish(msg)
        except Exception as e:
            rospy.logwarn("Failed to publish PID error: %s", str(e))

    def _on_test_result_received(self, result):
        """测试结果回调。"""
        rospy.loginfo("PID test result: %s", result)

    def _on_text_received(self, text):
        """文本响应回调。"""
        rospy.logdebug("MCU: %s", text)

        if self._parse_injection_pump_text(text):
            return

        # 检查 PID 完成消息
        if text.startswith("PID_DONE:"):
            motor = text.split(":")[1] if ":" in text else ""
            if motor in MOTOR_NAMES:
                self._notify_pid_complete(motor)

    def _check_pid_complete(self, angles):
        """检查 PID 是否完成 (基于角度误差)。"""
        for motor, target in list(self.pid_target_angles.items()):
            if target is None:
                continue
            current = angles.get(motor, 0.0)
            error = abs(current - target)
            # 处理 360° 边界
            if error > 180:
                error = 360 - error
            if error <= self.pid_precision_threshold:
                self._notify_pid_complete(motor)
                del self.pid_target_angles[motor]

    def _notify_pid_complete(self, motor):
        """通知 PID 完成。"""
        # 通知自动化引擎
        self.automation_engine.notify_pid_complete(motor)

        # 发布完成消息
        msg = String()
        msg.data = motor
        self.pid_complete_pub.publish(msg)
        rospy.loginfo("PID complete: %s", motor)

    def _cmd_callback(self, msg):
        """直接指令回调。"""
        cmd = msg.data.strip()
        cmd_upper = cmd.upper()

        if cmd_upper == "STOP":
            self.stop_all_pumps()
            return

        if self._is_injection_pump_command(cmd_upper):
            self.send_command(cmd_upper)
            return

        self.send_command(cmd_upper)

    def _step_callback(self, msg):
        """
        单步骤执行回调。
        接收 JSON 格式的步骤参数。
        """
        try:
            step = json.loads(msg.data)
            command = self.command_generator.generate_command(step, mode="manual")
            if command:
                # 记录 PID 目标
                if self.pid_mode:
                    targets = self.command_generator.get_pending_targets()
                    for motor, target in targets.items():
                        if target is not None:
                            self.pid_target_angles[motor] = target

                if not self.send_command(command):
                    return

            if not self._handle_injection_pump_step(step, mode="manual"):
                rospy.logerr("Manual step injection pump command failed")
        except json.JSONDecodeError as e:
            rospy.logerr("Invalid step JSON: %s", str(e))

    def _steps_callback(self, msg):
        """
        设置自动化步骤回调。
        接收 JSON 格式的步骤列表。
        """
        try:
            data = json.loads(msg.data)
            steps = data.get("steps", [])
            loop_count = data.get("loop_count", 1)

            self.automation_engine.set_steps(steps)
            self.automation_engine.set_loop_count(loop_count)
            self.automation_engine.set_pid_mode(self.pid_mode)

            rospy.loginfo("Automation steps loaded: %d steps, %s loops",
                          len(steps), "∞" if loop_count == 0 else str(loop_count))

        except json.JSONDecodeError as e:
            rospy.logerr("Invalid steps JSON: %s", str(e))

    def _stop_callback(self, req):
        """停止服务回调。"""
        # 停止自动化
        if self.automation_engine.is_running():
            self.automation_engine.stop()

        success = self.stop_all_pumps()
        return TriggerResponse(
            success=success,
            message="All pumps stopped" if success else "Stop failed"
        )

    def _auto_start_callback(self, req):
        """启动自动化服务回调。"""
        success = self.automation_engine.start()
        return TriggerResponse(
            success=success,
            message="Automation started" if success else "Start failed"
        )

    def _auto_stop_callback(self, req):
        """停止自动化服务回调。"""
        self.automation_engine.stop()
        return TriggerResponse(success=True, message="Automation stopped")

    def _auto_pause_callback(self, req):
        """暂停自动化服务回调。"""
        self.automation_engine.pause()
        return TriggerResponse(success=True, message="Automation paused")

    def _injection_on_callback(self, req):
        """开启进样泵服务。"""
        success = self._send_injection_pump_command(enabled=True)
        return TriggerResponse(success=success, message="Injection pump on" if success else "Injection pump on failed")

    def _injection_off_callback(self, req):
        """关闭进样泵服务。"""
        success = self._send_injection_pump_command(enabled=False)
        return TriggerResponse(success=success, message="Injection pump off" if success else "Injection pump off failed")

    def _injection_status_callback(self, req):
        """获取进样泵状态服务。"""
        success = self.send_command("PUMP:STATUS")
        message = json.dumps({
            "enabled": self.inject_pump_enabled,
            "speed": self.inject_pump_speed,
            "last_response": self.inject_pump_last_response,
            "last_error": self.inject_pump_last_error,
        })
        return TriggerResponse(success=success, message=message)

    def _auto_resume_callback(self, req):
        """恢复自动化服务回调。"""
        self.automation_engine.resume()
        return TriggerResponse(success=True, message="Automation resumed")

    def _on_automation_status(self, status):
        """自动化状态更新回调。"""
        self._publish_status("automation: " + status)

    def _on_automation_error(self, error):
        """自动化错误回调。"""
        rospy.logerr("Automation error: %s", error)
        self._publish_status("error: " + error)

    def _publish_status(self, status):
        """发布状态消息。"""
        msg = String()
        msg.data = status
        self.status_pub.publish(msg)

    def get_current_angles(self):
        """获取当前角度。"""
        with self.angles_lock:
            return self.current_angles.copy()

    def run(self):
        """主循环。"""
        if not self.connect():
            rospy.logerr("Failed to connect, exiting")
            return

        rate = rospy.Rate(10)

        while not rospy.is_shutdown():
            # 周期性状态日志
            angles = self.get_current_angles()
            rospy.logdebug_throttle(5, "Angles: %s", angles)
            rate.sleep()

        # 清理
        rospy.loginfo("Shutting down, stopping pumps...")
        if self.automation_engine.is_running():
            self.automation_engine.stop()
        self.stop_all_pumps()
        self.disconnect()


def main():
    """主入口。"""
    try:
        node = PumpControlNode()
        node.run()
    except rospy.ROSInterruptException:
        pass
    except Exception as e:
        rospy.logerr("Pump Control Node error: %s", str(e))


if __name__ == '__main__':
    main()
