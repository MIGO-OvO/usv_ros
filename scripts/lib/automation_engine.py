#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automation Engine (自动化执行引擎)
==================================
负责自动化流程的执行、暂停、恢复和停止。
支持等待 PID 完成后再开始计时间隔。

功能:
- 多步骤序列执行
- 循环执行 (有限/无限)
- 暂停/恢复
- PID 完成等待
- 高精度时间间隔

Python 3.8 兼容 (无 QThread，使用 threading)
"""

from __future__ import print_function

import copy
import json
import threading
import time

try:
    import rospy
    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False


class AutomationEngine(object):
    """
    自动化执行引擎。
    在后台线程中执行多步骤序列。
    """

    # PID 等待超时时间 (秒)
    PID_WAIT_TIMEOUT = 60.0

    def __init__(self, command_generator, send_command_func, log_func=None):
        """
        初始化自动化引擎。

        Args:
            command_generator: CommandGenerator 实例
            send_command_func: 发送指令的函数 func(str) -> bool
            log_func: 日志函数 func(str) (可选)
        """
        self.command_generator = command_generator
        self.send_command = send_command_func
        self.log = log_func or self._default_log
        self.on_step_command = None  # func(step) -> bool，可选的步骤级发送钩子
        self.on_step_wait = None     # func(step) -> bool，可选的步骤完成等待钩子

        # 步骤和循环配置
        self.steps = []
        self.loop_count = 1  # 0 表示无限循环

        # 线程控制
        self._thread = None
        self._running = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()

        # 状态跟踪
        self._current_step = 0
        self._current_loop = 1

        # PID 完成等待机制
        self._pid_complete_event = threading.Event()
        self._pending_pid_motors = set()
        self._pid_mode_enabled = False

        # 回调函数
        self.on_status_update = None   # func(str)
        self.on_progress_update = None  # func(int) 0-100
        self.on_error = None           # func(str)
        self.on_finished = None        # func()
        self.on_step_complete = None   # func(step_idx, step_data)

    def _default_log(self, message):
        """默认日志函数。"""
        if ROS_AVAILABLE:
            rospy.loginfo("[Automation] %s", message)
        else:
            print("[Automation] {}".format(message))

    def set_steps(self, steps):
        """
        设置执行步骤。

        Args:
            steps: 步骤列表
                [
                    {
                        "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
                        "Y": {"enable": "D", ...},
                        "interval": 1000  # 间隔时间 (ms)
                    },
                    ...
                ]
        """
        # 深拷贝步骤数据
        try:
            self.steps = json.loads(json.dumps(steps))
        except Exception:
            self.steps = copy.deepcopy(steps)

    def set_loop_count(self, count):
        """
        设置循环次数。

        Args:
            count: 循环次数 (0 表示无限循环)
        """
        self.loop_count = count

    def set_pid_mode(self, enabled):
        """
        设置 PID 模式。

        Args:
            enabled: 是否启用 PID 模式
        """
        self._pid_mode_enabled = enabled

    def start(self):
        """
        开始执行自动化流程。

        Returns:
            bool: 是否成功启动
        """
        if self._thread and self._thread.is_alive():
            self.log("自动化流程已在运行中")
            return False

        if not self.steps:
            self.log("没有配置执行步骤")
            return False

        # 重置状态
        self._current_step = 0
        self._current_loop = 1
        self._running.set()
        self._paused.clear()

        # 重置指令生成器
        self.command_generator.reset_for_auto_mode()

        # 启动执行线程
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self.log("自动化流程已启动")
        return True

    def stop(self):
        """停止执行。"""
        self._running.clear()
        self._paused.clear()

        # 发送停止指令
        try:
            # 先停止 PID
            self.send_command(self.command_generator.generate_pid_stop_command())
            # 再停止所有电机
            self.send_command(self.command_generator.generate_stop_command())
        except Exception:
            pass

        # 等待线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self.log("自动化流程已停止")

    def pause(self):
        """暂停执行。"""
        self._paused.set()
        self.log("自动化流程已暂停")
        self._update_status("paused")

    def resume(self):
        """恢复执行。"""
        self._paused.clear()
        self.log("自动化流程已恢复")
        self._update_status("running")

    def is_running(self):
        """检查是否正在运行。"""
        return self._running.is_set() and self._thread and self._thread.is_alive()

    def is_paused(self):
        """检查是否已暂停。"""
        return self._paused.is_set()

    def notify_pid_complete(self, motor):
        """
        通知 PID 完成 (由外部调用)。

        Args:
            motor: 完成的电机名称
        """
        if motor in self._pending_pid_motors:
            self._pending_pid_motors.discard(motor)
            self._pid_complete_event.set()

    def get_status(self):
        """
        获取当前状态。

        Returns:
            dict: 状态信息
        """
        return {
            "running": self.is_running(),
            "paused": self.is_paused(),
            "current_step": self._current_step,
            "total_steps": len(self.steps),
            "current_loop": self._current_loop,
            "total_loops": self.loop_count,
            "pending_motors": list(self._pending_pid_motors)
        }

    def _run_loop(self):
        """执行线程主循环。"""
        try:
            while self._running.is_set() and self._should_continue():
                if not self._running.is_set():
                    break

                self._execute_loop()

        except Exception as e:
            self._handle_error("执行错误: {}".format(str(e)))
        finally:
            self._cleanup()
            if self.on_finished:
                self.on_finished()

    def _should_continue(self):
        """判断是否应该继续执行。"""
        if self.loop_count == 0:
            return True  # 无限循环
        return self._current_loop <= self.loop_count

    def _execute_loop(self):
        """执行单个循环。"""
        loop_info = "∞" if self.loop_count == 0 else str(self.loop_count)
        self._update_status("running (loop {}/{})".format(self._current_loop, loop_info))
        self._update_progress(0)

        for step_idx, step in enumerate(self.steps):
            if not self._running.is_set():
                break

            # 处理暂停
            self._wait_if_paused()

            self._current_step = step_idx
            progress = int((step_idx + 1) / len(self.steps) * 100)
            self._update_progress(progress)

            # 发送步骤指令
            if not self._send_step_command(step):
                break

            # 步骤执行完成等待
            if not self._wait_for_step_execution(step):
                break

            # 步骤完成回调
            if self.on_step_complete:
                self.on_step_complete(step_idx, step)

            # 等待间隔（在步骤完成后开始计时）
            interval_ms = step.get("interval", 0)
            self._wait_interval(interval_ms)

        self._current_loop += 1

    def _wait_if_paused(self):
        """如果暂停则等待。"""
        while self._paused.is_set() and self._running.is_set():
            time.sleep(0.1)

    def _send_step_command(self, step):
        """
        发送步骤指令。

        Args:
            step: 步骤参数

        Returns:
            bool: 是否成功
        """
        if not self._running.is_set():
            return False

        try:
            if self.on_step_command:
                return self.on_step_command(step)

            # 生成指令
            command = self.command_generator.generate_command(step, mode="auto")

            if not command:
                return True  # 空指令视为成功

            if not self._running.is_set():
                return False

            # 发送指令
            success = self.send_command(command)
            if success:
                self.log("指令已发送: {}".format(command.strip()))
            else:
                self._handle_error("指令发送失败")
                return False

            return True

        except Exception as e:
            self._handle_error("发送指令异常: {}".format(str(e)))
            return False

    def _get_step_active_motors(self, step):
        """
        获取步骤中启用的非连续模式电机。

        Args:
            step: 步骤参数

        Returns:
            set: 启用的电机集合
        """
        active_motors = set()
        for motor in ["X", "Y", "Z", "A"]:
            motor_cfg = step.get(motor, {})
            if motor_cfg.get("enable") == "E" and not motor_cfg.get("continuous", False):
                active_motors.add(motor)
        return active_motors

    def _wait_for_pid_complete(self):
        """
        等待所有 PID 电机完成。

        Returns:
            bool: True 表示完成，False 表示超时或中断
        """
        start_time = time.time()

        while self._running.is_set() and self._pending_pid_motors:
            # 检查暂停
            self._wait_if_paused()

            # 检查超时
            if time.time() - start_time > self.PID_WAIT_TIMEOUT:
                self._handle_error(
                    "PID 等待超时 ({:.0f}s)，未完成电机: {}".format(
                        self.PID_WAIT_TIMEOUT, self._pending_pid_motors
                    )
                )
                return False

            # 等待 PID 完成事件
            self._pid_complete_event.wait(timeout=0.1)
            self._pid_complete_event.clear()

        return self._running.is_set()

    def _wait_for_step_execution(self, step):
        """等待步骤执行完成，再进入 interval 计时。"""
        if self._pid_mode_enabled:
            pid_motors = self._get_step_active_motors(step)
            if pid_motors:
                self._pending_pid_motors = pid_motors.copy()
                self._pid_complete_event.clear()
                if not self._wait_for_pid_complete():
                    if self._running.is_set():
                        self._update_status("PID wait timeout")
                    return False

        if self.on_step_wait:
            try:
                return self.on_step_wait(step)
            except Exception as e:
                self._handle_error("步骤完成等待异常: {}".format(str(e)))
                return False

        return self._running.is_set()


    def _wait_interval(self, interval_ms):
        """
        高精度间隔等待。

        Args:
            interval_ms: 间隔时间 (毫秒)
        """
        interval = interval_ms / 1000.0
        if interval <= 0:
            return

        get_time = time.perf_counter
        deadline = get_time() + interval
        error_correction = 0.0

        while get_time() < deadline and self._running.is_set():
            # 检查暂停
            if self._paused.is_set():
                pause_start = get_time()
                self._wait_if_paused()
                # 暂停时间不计入间隔
                deadline += get_time() - pause_start
                continue

            remaining = deadline - get_time() - error_correction

            if remaining <= 0.002:
                break

            # 动态睡眠策略
            if remaining > 0.01:
                sleep_time = remaining * 0.75
                sleep_time = max(sleep_time, 0.005)
                t1 = get_time()
                time.sleep(sleep_time)
                t2 = get_time()
                error_correction += (t2 - t1) - sleep_time
            else:
                # 短间隔使用忙等待
                while get_time() + error_correction < deadline:
                    if deadline - get_time() > 0.002:
                        time.sleep(0.001)

    def _cleanup(self):
        """清理资源。"""
        try:
            # 发送停止指令
            self.send_command(self.command_generator.generate_pid_stop_command())
            self.send_command(self.command_generator.generate_stop_command())
        except Exception:
            pass

        self._update_status("finished")

    def _update_status(self, status):
        """更新状态。"""
        if self.on_status_update:
            self.on_status_update(status)

    def _update_progress(self, progress):
        """更新进度。"""
        if self.on_progress_update:
            self.on_progress_update(progress)

    def _handle_error(self, message):
        """处理错误。"""
        self.log("错误: {}".format(message))
        if self.on_error:
            self.on_error(message)
