#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Command Generator (指令生成器)
==============================
根据参数生成电机控制指令，支持:
- 传统开环模式 (J 指令)
- PID 闭环精确定位模式 (R 指令)
- 多电机协调控制
- 自动模式角度累积
- 校准补偿

指令格式:
- 开环: "{motor}E{dir}V{speed}J{angle}"  例: XEFV5J90.0
- PID:  "{motor}E{dir}R{angle}P{precision}"  例: XEFR90.0P0.1
- 连续: "{motor}E{dir}V{speed}JG"  例: XEFV5JG
- 停止: "{motor}DFV0J0"

Python 3.8 兼容
"""

from __future__ import print_function

# 电机名称
MOTOR_NAMES = ["X", "Y", "Z", "A"]

# 方向映射
DIRECTION_MAP = {"F": 1, "B": -1}

# 指令终止符
COMMAND_TERMINATOR = "\r\n"


class CommandGenerator(object):
    """
    电机控制指令生成器。
    支持开环和 PID 闭环两种控制模式。
    """

    def __init__(self):
        """初始化指令生成器。"""
        # 活动电机集合
        self.active_motors = set(MOTOR_NAMES)
        
        # 目标位置跟踪
        self.pending_targets = {m: None for m in MOTOR_NAMES}
        self.expected_rotation = {m: 0.0 for m in MOTOR_NAMES}
        self.current_angles = {m: 0.0 for m in MOTOR_NAMES}
        
        # 自动模式状态
        self.initial_angle_base = {m: None for m in MOTOR_NAMES}
        self.accumulated_rotation = {m: 0.0 for m in MOTOR_NAMES}
        self.expected_angles = {m: 0.0 for m in MOTOR_NAMES}
        self.is_first_command = True
        
        # 校准参数
        self.theoretical_deviations = {m: None for m in MOTOR_NAMES}
        self.calibration_enabled = False
        self.calibration_amplitude = 1.0
        
        # PID 模式参数
        self.pid_mode_enabled = False
        self.pid_precision = 0.1  # 默认精度 0.1°

    def set_current_angles(self, angles):
        """
        设置当前角度。

        Args:
            angles: 角度字典 {"X": 0.0, "Y": 0.0, ...}
        """
        for motor, angle in angles.items():
            if motor in MOTOR_NAMES:
                self.current_angles[motor] = angle

    def set_pid_mode(self, enabled, precision=0.1):
        """
        设置 PID 模式。

        Args:
            enabled: 是否启用 PID 闭环模式
            precision: PID 定位精度 (度)
        """
        self.pid_mode_enabled = enabled
        self.pid_precision = precision

    def set_calibration(self, enabled, amplitude=1.0):
        """
        设置校准参数。

        Args:
            enabled: 是否启用校准补偿
            amplitude: 校准幅值
        """
        self.calibration_enabled = enabled
        self.calibration_amplitude = amplitude

    def set_theoretical_deviations(self, deviations):
        """
        设置理论偏差。

        Args:
            deviations: 偏差字典 {"X": 0.5, "Y": -0.3, ...}
        """
        for motor, dev in deviations.items():
            if motor in MOTOR_NAMES:
                self.theoretical_deviations[motor] = dev

    def reset_for_auto_mode(self):
        """重置自动模式状态。"""
        self.is_first_command = True
        for motor in MOTOR_NAMES:
            self.initial_angle_base[motor] = self.current_angles.get(motor)
            self.accumulated_rotation[motor] = 0.0

    def generate_command(self, step_params, mode="manual"):
        """
        生成电机控制指令。

        Args:
            step_params: 步骤参数字典
                {
                    "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "90", "continuous": False},
                    "Y": {"enable": "D", ...},
                    ...
                    "interval": 1000  # 可选，间隔时间 (ms)
                }
            mode: 运行模式 ("manual" 或 "auto")

        Returns:
            str: 生成的指令字符串 (含终止符)
        """
        command = ""
        command_active_motors = set()
        self.pending_targets = {m: None for m in MOTOR_NAMES}
        self.expected_rotation = {m: 0.0 for m in MOTOR_NAMES}

        # 自动模式初始基准设置
        if mode == "auto" and self.is_first_command:
            for motor in self.active_motors:
                if self.current_angles.get(motor) is not None:
                    self.initial_angle_base[motor] = self.current_angles[motor]
                else:
                    self.initial_angle_base[motor] = None

        for motor in MOTOR_NAMES:
            config = step_params.get(motor, {})
            enable = config.get("enable", "D")

            # 如果电机未启用，跳过
            if enable != "E":
                continue

            command_active_motors.add(motor)

            direction = config.get("direction", "F")
            speed = config.get("speed", "0")
            raw_angle = str(config.get("angle", "0")).upper()
            is_continuous = config.get("continuous", False)
            dir_factor = DIRECTION_MAP.get(direction, 1)

            try:
                # ===== 连续转动模式 =====
                if is_continuous:
                    command += "{}E{}V{}JG".format(motor, direction, speed)
                    self.pending_targets[motor] = None
                    continue

                raw_rotation = float(raw_angle)
                self.expected_rotation[motor] = raw_rotation

                # ===== PID 精确控制模式 =====
                if self.pid_mode_enabled and not is_continuous:
                    precision = self.pid_precision

                    # 累积旋转量跟踪
                    if mode == "auto":
                        if self.initial_angle_base[motor] is None:
                            current = self.current_angles.get(motor, 0.0)
                            self.initial_angle_base[motor] = current
                        self.accumulated_rotation[motor] += raw_rotation * dir_factor
                        self.expected_angles[motor] = (
                            self.initial_angle_base[motor] + self.accumulated_rotation[motor]
                        ) % 360
                    else:
                        current = self.current_angles.get(motor, 0.0)
                        self.pending_targets[motor] = (current + raw_rotation * dir_factor) % 360

                    # 生成 R 指令: XEFR90.0P0.1
                    command += "{}E{}R{:.1f}P{}".format(motor, direction, raw_rotation, precision)
                    continue

                # ===== 传统开环模式 =====
                if mode == "auto":
                    if self.initial_angle_base[motor] is None:
                        base = self.current_angles.get(motor, 0.0)
                        self.initial_angle_base[motor] = base

                    raw_rotation_signed = float(raw_angle) * dir_factor
                    self.accumulated_rotation[motor] += raw_rotation_signed

                    # 校准补偿
                    if self.calibration_enabled:
                        compensation = (self.theoretical_deviations.get(motor) or 0.0) * self.calibration_amplitude
                        calibrated_rotation = raw_rotation_signed - compensation
                    else:
                        calibrated_rotation = raw_rotation_signed

                    actual_rotation = abs(calibrated_rotation)
                    self.expected_angles[motor] = (
                        self.initial_angle_base[motor] + self.accumulated_rotation[motor]
                    ) % 360

                    # 更新方向
                    if calibrated_rotation < 0:
                        direction = "B"
                    else:
                        direction = "F"
                else:
                    # 手动模式
                    actual_rotation = float(raw_angle)
                    current = self.current_angles.get(motor, 0.0)
                    self.pending_targets[motor] = (current + actual_rotation * dir_factor) % 360

                # 生成 J 指令: XEFV5J90.000
                command += "{}E{}V{}J{:.3f}".format(motor, direction, speed, actual_rotation)

            except (ValueError, TypeError) as e:
                # 参数解析错误，跳过此电机
                continue

        # 更新活动电机
        self.active_motors = command_active_motors
        self.is_first_command = False

        # 添加终止符
        if command:
            return command + COMMAND_TERMINATOR
        return ""

    def generate_stop_command(self):
        """
        生成停止所有电机指令。

        Returns:
            str: 停止指令字符串
        """
        command = "".join(["{}DFV0J0".format(m) for m in MOTOR_NAMES])
        return command + COMMAND_TERMINATOR

    def generate_pid_stop_command(self):
        """
        生成 PID 停止指令。

        Returns:
            str: PID 停止指令
        """
        return "PIDSTOP" + COMMAND_TERMINATOR

    def generate_calibration_command(self, selected_motors):
        """
        生成校准指令 (归零)。

        Args:
            selected_motors: 需要校准的电机集合 {"X", "Y"}

        Returns:
            str: 校准指令字符串
        """
        active_commands = []

        for motor in MOTOR_NAMES:
            if motor not in selected_motors:
                continue

            current_angle = (self.current_angles.get(motor, 0.0) or 0.0) % 360

            # 计算最短路径归零
            if current_angle > 180:
                target_angle = 360 - current_angle
                direction = "EF"  # 正转
            else:
                target_angle = current_angle
                direction = "EB"  # 反转

            # 生成校准指令
            command_part = "{}{}V5J{:.3f}".format(motor, direction, target_angle)
            active_commands.append(command_part)

        if not active_commands:
            return ""

        return "".join(active_commands) + COMMAND_TERMINATOR

    def generate_pid_config_command(self, kp=0.14, ki=0.015, kd=0.06, out_min=1.0, out_max=8.0):
        """
        生成 PID 参数配置指令。

        Args:
            kp: 比例系数
            ki: 积分系数
            kd: 微分系数
            out_min: 输出下限
            out_max: 输出上限

        Returns:
            str: PID 配置指令
        """
        return "PIDCFG:{:.4f},{:.5f},{:.4f},{:.1f},{:.1f}{}".format(
            kp, ki, kd, out_min, out_max, COMMAND_TERMINATOR
        )

    def get_active_motors(self):
        """获取当前活动电机集合。"""
        return self.active_motors.copy()

    def get_expected_angles(self):
        """获取预期角度。"""
        return self.expected_angles.copy()

    def get_pending_targets(self):
        """获取待定目标位置。"""
        return self.pending_targets.copy()
