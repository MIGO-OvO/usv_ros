#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web Configuration Server (Web 配置服务器)
==========================================
提供 HTTP 接口和 WebSocket 实时通信用于配置自动化采样参数。
架构: 分离式前端 (static/) + Flask-SocketIO 后端

功能:
  - 提供 Web UI 配置采样参数
  - 实时转子角度监控 (WebSocket)
  - 预设管理 (PresetManager)
  - 保存/加载配置文件 (JSON)
  - 提供 REST API 供其他节点调用

Target: Jetson Nano
Python: 3.8

访问地址: http://10.42.0.1:5000 (Nano 热点 IP)

依赖:
  pip3 install flask flask-cors flask-socketio eventlet
"""

from __future__ import print_function

import json
import os
import sys
import threading
import time
from datetime import datetime

try:
    from flask import Flask, request, jsonify, send_from_directory, abort, Response
    from flask_cors import CORS
    from flask_socketio import SocketIO, emit
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("警告: Flask/SocketIO 未安装，请运行: pip3 install flask flask-cors flask-socketio eventlet")

# 尝试导入 ROS，如果失败则以独立模式运行
try:
    import rospy
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False
    print("警告: ROS 未导入，以独立模式运行 (无 ROS 集成)")
    # 创建 mock rospy 用于独立模式
    class MockRospy:
        def init_node(self, *args, **kwargs): pass
        def get_param(self, name, default): return default
        def loginfo(self, msg, *args): print("[INFO]", msg % args if args else msg)
        def logwarn(self, msg, *args): print("[WARN]", msg % args if args else msg)
        def logerr(self, msg, *args): print("[ERROR]", msg % args if args else msg)
        def Subscriber(self, *args, **kwargs): return None
        def Publisher(self, *args, **kwargs):
            class MockPub:
                def publish(self, msg): pass
            return MockPub()
        def ServiceProxy(self, *args, **kwargs): return None
        def wait_for_service(self, *args, **kwargs): raise Exception("ROS not available")
        def is_shutdown(self): return False
        def spin(self):
            while True: time.sleep(1)
        def Rate(self, hz):
            class MockRate:
                def sleep(self): time.sleep(1.0/hz)
            return MockRate()

    rospy = MockRospy()

    class String:
        def __init__(self): self.data = ""

    class Trigger:
        pass

# 确保脚本目录在路径中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from preset_manager import PresetManager

# 配置文件路径
CONFIG_DIR = os.path.expanduser("~/usv_ws/config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "sampling_config.json")
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibration.json")
DATA_DIR = os.path.expanduser("~/usv_ws/data/missions")

class CalibrationManager(object):
    """零点校准管理器"""
    def __init__(self, file_path=CALIBRATION_FILE):
        self.file_path = file_path
        self.offsets = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    data = json.load(f)
                    self.offsets.update(data.get('offsets', {}))
            except Exception as e:
                print(f"Error loading calibration: {e}")

    def save(self):
        try:
            with open(self.file_path, 'w') as f:
                json.dump({'offsets': self.offsets}, f, indent=2)
        except Exception as e:
            print(f"Error saving calibration: {e}")

    def set_zero(self, axis, raw_angle):
        """设置当前角度为零点 (Offset = Raw)"""
        if axis in self.offsets:
            self.offsets[axis] = raw_angle
            self.save()

    def reset(self, axis=None):
        if axis:
            if axis in self.offsets:
                self.offsets[axis] = 0.0
        else:
            self.offsets = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self.save()

    def get_corrected_angle(self, axis, raw_angle):
        """获取校准后的角度"""
        offset = self.offsets.get(axis, 0.0)
        corrected = raw_angle - offset
        # Normalize to 0-360 if needed, or keep linear.
        # Usually angle is 0-360.
        corrected = corrected % 360.0
        if corrected < 0:
            corrected += 360.0
        return corrected

class MissionDataManager(object):
    """任务数据管理器。"""

    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.current_mission_file = None
        self.current_mission_data = []
        self._ensure_dir()

    def _ensure_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

    def start_mission(self, mission_name=""):
        """开始新任务记录。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mission_{timestamp}.json"
        self.current_mission_file = os.path.join(self.data_dir, filename)
        self.current_mission_data = {
            "mission_id": timestamp,
            "name": mission_name or f"Mission {timestamp}",
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "data_points": []
        }
        self._save_current()
        return filename

    def stop_mission(self):
        """停止任务记录。"""
        if self.current_mission_file:
            self.current_mission_data["end_time"] = datetime.now().isoformat()
            self._save_current()
            self.current_mission_file = None
            self.current_mission_data = []

    def add_data_point(self, voltage, absorbance=0.0):
        """添加数据点（含电压和吸光度）。"""
        if self.current_mission_file:
            point = {
                "timestamp": datetime.now().isoformat(),
                "voltage": voltage,
                "absorbance": absorbance
            }
            self.current_mission_data["data_points"].append(point)
            # 每 10 个点保存一次，防止数据丢失
            if len(self.current_mission_data["data_points"]) % 10 == 0:
                self._save_current()

    def _save_current(self):
        """保存当前任务数据到文件。"""
        if self.current_mission_file:
            with open(self.current_mission_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_mission_data, f, ensure_ascii=False, indent=2)

    def list_missions(self):
        """列出所有任务。"""
        missions = []
        if not os.path.exists(self.data_dir):
            return []

        for f in os.listdir(self.data_dir):
            if f.endswith(".json") and f.startswith("mission_"):
                path = os.path.join(self.data_dir, f)
                try:
                    with open(path, 'r', encoding='utf-8') as file:
                        # 只读取元数据，不读取所有数据点
                        # 为了效率，这里假设文件较小，或者只读前几行
                        # 简单起见，这里读整个文件，但在生产环境中应该优化
                        data = json.load(file)
                        missions.append({
                            "id": data.get("mission_id", f),
                            "name": data.get("name", f),
                            "start_time": data.get("start_time"),
                            "end_time": data.get("end_time"),
                            "point_count": len(data.get("data_points", []))
                        })
                except Exception:
                    continue
        # 按时间倒序
        return sorted(missions, key=lambda x: x["start_time"] or "", reverse=True)

    def get_mission(self, mission_id):
        """获取指定任务详情。"""
        filename = f"mission_{mission_id}.json"
        path = os.path.join(self.data_dir, filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def delete_mission(self, mission_id):
        """删除任务。"""
        filename = f"mission_{mission_id}.json"
        path = os.path.join(self.data_dir, filename)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

# 默认配置
DEFAULT_CONFIG = {
    "version": "1.0",
    "updated_at": "",
    "mission": {
        "name": "默认采样任务",
        "description": ""
    },
    "pump_settings": {
        "pid_mode": True,
        "pid_precision": 0.1,
        "default_speed": 5
    },
    "sampling_sequence": {
        "loop_count": 1,
        "steps": [
            {
                "name": "启动油相泵",
                "X": {"enable": "D"},
                "Y": {"enable": "D"},
                "Z": {"enable": "D"},
                "A": {"enable": "E", "direction": "F", "speed": "3", "angle": "360"},
                "pump": {"enable": False, "speed": 0, "duration_ms": 0},
                "interval": 2000
            },
            {
                "name": "进样混合",
                "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "180"},
                "Y": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
                "Z": {"enable": "D"},
                "A": {"enable": "E", "direction": "F", "speed": "3", "angle": "180"},
                "pump": {"enable": True, "speed": 60, "duration_ms": 3000},
                "interval": 5000
            },
            {
                "name": "停止",
                "X": {"enable": "D"},
                "Y": {"enable": "D"},
                "Z": {"enable": "D"},
                "A": {"enable": "D"},
                "pump": {"enable": False, "speed": 0, "duration_ms": 0},
                "interval": 1000
            }
        ]
    },
    "waypoint_sampling": {
        "0": {
            "enabled": True,
            "loop_count": 1,
            "retry_count": 0,
            "hold_before_sampling_s": 3.0,
            "on_fail": "HOLD"
        }
    },
    "detection_settings": {
        "duration": 5.0,
        "sample_rate": 100
    },
    "hardware": {
        "pump_serial_port": "/dev/ttyUSB0",
        "pump_baudrate": 115200,
        "pump_timeout": 1.0,
        "ads_address": "0x40",
        "spectro_channel": 2,
        "mux": "AIN0_AVSS",
        "gain": 1,
        "vref_mode": "AVDD",
        "adc_rate": 90,
        "publish_rate": 20,
        "continuous_mode": True,
        "auto_start": False,
        "i2c_mapping": {"X": 0, "Y": 3, "Z": 4, "A": 7}
    }
}


class ConfigManager(object):
    """配置文件管理器。"""

    @staticmethod
    def _normalize_sampling_sequence(sequence):
        """标准化自动化步骤，补齐旧配置缺失的进样泵字段。"""
        sequence = sequence or {}
        raw_steps = sequence.get('steps', [])
        normalized_steps = []
        for raw_step in raw_steps if isinstance(raw_steps, list) else []:
            step = dict(raw_step or {})
            pump = step.get('pump') or {}
            try:
                pump_speed = int(float(pump.get('speed', 0) or 0))
            except (TypeError, ValueError):
                pump_speed = 0
            try:
                pump_duration_ms = int(float(pump.get('duration_ms', 0) or 0))
            except (TypeError, ValueError):
                pump_duration_ms = 0
            pump_speed = max(0, min(100, pump_speed))
            pump_duration_ms = max(0, pump_duration_ms)
            step['pump'] = {
                'enable': bool(pump.get('enable', False)),
                'speed': pump_speed,
                'duration_ms': pump_duration_ms,
            }
            normalized_steps.append(step)

        normalized = dict(sequence)
        normalized['steps'] = normalized_steps
        try:
            normalized['loop_count'] = int(sequence.get('loop_count', 1) or 1)
        except (TypeError, ValueError):
            normalized['loop_count'] = 1
        return normalized

    @staticmethod
    def _normalize_waypoint_sampling(data):
        normalized = {}
        raw = data if isinstance(data, dict) else {}
        for key, value in raw.items():
            try:
                seq_key = str(int(key))
            except (TypeError, ValueError):
                continue
            item = dict(value or {}) if isinstance(value, dict) else {}
            try:
                loop_count = int(item.get('loop_count', 1) or 1)
            except (TypeError, ValueError):
                loop_count = 1
            try:
                retry_count = int(item.get('retry_count', 0) or 0)
            except (TypeError, ValueError):
                retry_count = 0
            try:
                hold_before_sampling_s = float(item.get('hold_before_sampling_s', 3.0) or 3.0)
            except (TypeError, ValueError):
                hold_before_sampling_s = 3.0
            on_fail = str(item.get('on_fail', 'HOLD') or 'HOLD').strip().upper()
            normalized[seq_key] = {
                'enabled': bool(item.get('enabled', True)),
                'loop_count': max(0, loop_count),
                'retry_count': max(0, retry_count),
                'hold_before_sampling_s': max(0.0, hold_before_sampling_s),
                'on_fail': on_fail if on_fail in ('HOLD', 'SKIP', 'ABORT') else 'HOLD',
            }
        return normalized

    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.config = DEFAULT_CONFIG.copy()
        self._ensure_dir()

    def _ensure_dir(self):
        """确保配置目录存在。"""
        config_dir = os.path.dirname(self.config_file)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)

    def load(self):
        """加载配置文件。"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self._merge_config(loaded)
                return True
        except Exception as e:
            rospy.logwarn("Failed to load config: %s", str(e))
        return False

    def save(self):
        """保存配置文件。"""
        try:
            self.config['sampling_sequence'] = self._normalize_sampling_sequence(
                self.config.get('sampling_sequence', {})
            )
            self.config['waypoint_sampling'] = self._normalize_waypoint_sampling(
                self.config.get('waypoint_sampling', {})
            )
            self.config['updated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            rospy.logerr("Failed to save config: %s", str(e))
            return False

    def _merge_config(self, loaded):
        """合并加载的配置到默认配置。"""
        def merge(base, update):
            for key, value in update.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    merge(base[key], value)
                else:
                    base[key] = value
        merge(self.config, loaded)
        self.config['sampling_sequence'] = self._normalize_sampling_sequence(
            self.config.get('sampling_sequence', {})
        )
        self.config['waypoint_sampling'] = self._normalize_waypoint_sampling(
            self.config.get('waypoint_sampling', {})
        )

    def update(self, data):
        """更新配置。"""
        self._merge_config(data)
        return self.save()

    def reset(self):
        """重置为默认配置。"""
        self.config = DEFAULT_CONFIG.copy()
        self.config['sampling_sequence'] = self._normalize_sampling_sequence(
            self.config.get('sampling_sequence', {})
        )
        self.config['waypoint_sampling'] = self._normalize_waypoint_sampling(
            self.config.get('waypoint_sampling', {})
        )
        return self.save()

    def get(self):
        """获取当前配置。"""
        config = self.config.copy()
        config['sampling_sequence'] = self._normalize_sampling_sequence(
            config.get('sampling_sequence', {})
        )
        config['waypoint_sampling'] = self._normalize_waypoint_sampling(
            config.get('waypoint_sampling', {})
        )
        return config


class WebConfigServer(object):
    """Web 配置服务器 ROS 节点。"""

    def __init__(self, standalone=False):
        """
        初始化 Web 配置服务器。

        Args:
            standalone: 是否以独立模式运行 (不依赖 ROS)
        """
        self.standalone = standalone or not ROS_AVAILABLE

        if not self.standalone:
            try:
                rospy.init_node('web_config_server', anonymous=False)
            except Exception as e:
                print("警告: 无法初始化 ROS 节点，切换到独立模式")
                print("  错误:", str(e))
                self.standalone = True

        # 参数
        if not self.standalone:
            self.host = rospy.get_param('~host', '0.0.0.0')
            self.port = rospy.get_param('~port', 5000)
        else:
            self.host = '0.0.0.0'
            self.port = 5000

        # 管理器
        self.config_manager = ConfigManager()
        self.config_manager.load()
        self.preset_manager = PresetManager()
        self.data_manager = MissionDataManager()
        self.calibration_manager = CalibrationManager()

        # 日志缓冲
        self.log_buffer = []
        self.log_lock = threading.Lock()
        self.max_logs = 100

        # 状态缓存
        self.pump_connected = False
        self.automation_running = False
        self.current_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self.current_voltage = 0.0
        self.current_absorbance = 0.0
        self.spectrometer_status = "idle"
        self.latest_spectrometer_payload = {}
        self.mission_status = "IDLE"
        self.injection_pump_status = {
            "enabled": False,
            "speed": 0,
            "last_response": "",
            "last_error": "",
        }
        self.voltage_history = []  # List of {timestamp, voltage, absorbance, raw}

        # ROS 订阅 (仅在非独立模式)
        if not self.standalone:
            self.status_sub = rospy.Subscriber('/usv/pump_status', String, self._status_cb)
            self.angles_sub = rospy.Subscriber('/usv/pump_angles', String, self._angles_cb)
            self.injection_status_sub = rospy.Subscriber('/usv/injection_pump_status', String, self._injection_status_cb)
            self.voltage_sub = rospy.Subscriber('/usv/spectrometer_voltage', String, self._voltage_cb)
            self.mission_sub = rospy.Subscriber('/usv/mission_status', String, self._mission_status_cb)
            self.pid_error_sub = rospy.Subscriber('/usv/pump_pid_error', String, self._pid_error_cb)
            self.steps_pub = rospy.Publisher('/usv/automation_steps', String, queue_size=1)
            self.command_pub = rospy.Publisher('/usv/pump_command', String, queue_size=10)
        else:
            self.status_sub = None
            self.angles_sub = None
            self.injection_status_sub = None
            self.voltage_sub = None
            self.mission_sub = None
            self.pid_error_sub = None
            self.steps_pub = None
            self.command_pub = None

        # Flask & SocketIO
        self.app = None
        self.socketio = None
        self.server_thread = None

        # ========== 通信诊断状态 ==========
        self._mavros_state = {"connected": False, "armed": False, "mode": ""}
        self._bridge_diag = {}           # 最新的桥接诊断数据
        self._radio_status = {}          # 最新的电台链路状态
        self._diag_history = []          # 诊断历史 (最近 200 条)
        self._diag_history_max = 200
        self._link_events = []           # 链路事件日志 (最近 500 条)
        self._link_events_max = 500

        # 通信诊断 ROS 订阅
        if not self.standalone:
            try:
                from mavros_msgs.msg import State as MavrosState
                self._mavros_state_sub = rospy.Subscriber(
                    '/mavros/state', MavrosState, self._web_mavros_state_cb)
            except ImportError:
                self._mavros_state_sub = None
                rospy.logwarn("mavros_msgs not available, MAVROS state monitoring disabled")
            self._bridge_diag_sub = rospy.Subscriber(
                '/usv/bridge_diagnostics', String, self._bridge_diag_cb)
            self._radio_status_sub = rospy.Subscriber(
                '/usv/radio_status', String, self._radio_status_cb)
        else:
            self._mavros_state_sub = None
            self._bridge_diag_sub = None
            self._radio_status_sub = None

        if FLASK_AVAILABLE:
            self._setup_flask()
        else:
            rospy.logerr("Flask/SocketIO not available!")

        mode_str = "独立模式 (无 ROS)" if self.standalone else "ROS 模式"
        rospy.loginfo("Web Config Server initialized (%s)", mode_str)
        rospy.loginfo("  URL: http://%s:%d", self.host, self.port)

    def _status_cb(self, msg):
        """泵状态回调。"""
        status_raw = msg.data or ""
        status = status_raw.lower()

        # pump_connected 只在明确收到 connected/disconnected/error 时变更
        if 'connected' in status and 'disconnected' not in status:
            self.pump_connected = True
        elif 'disconnected' in status or status.startswith('error:'):
            self.pump_connected = False

        # automation_running 从 automation: 前缀中提取
        # 注意：mission_status 已由 /usv/mission_status（mavlink_trigger_node）统一驱动，
        # 此处仅更新 automation_running 标志，不再覆盖 mission_status 以避免与新阶段状态冲突。
        if 'automation:' in status:
            automation_status = status.split('automation:', 1)[1].strip()
            if '运行' in automation_status or 'running' in automation_status:
                self.automation_running = True
            elif '已完成' in automation_status or 'finished' in automation_status or '完成' in automation_status:
                self.automation_running = False
            elif '已停止' in automation_status or 'stopped' in automation_status:
                self.automation_running = False
        elif status == 'stopped':
            self.automation_running = False

    def _injection_status_cb(self, msg):
        """进样泵状态回调。"""
        try:
            data = json.loads(msg.data)
            self.injection_pump_status = {
                "enabled": bool(data.get("enabled", False)),
                "speed": int(data.get("speed", 0)),
                "last_response": data.get("last_response", ""),
                "last_error": data.get("last_error", ""),
            }
            if self.socketio:
                self.socketio.emit('injection_pump_status', self.injection_pump_status)
        except Exception as e:
            rospy.logerr("Error parsing injection pump status: %s", str(e))

    def _angles_cb(self, msg):
        """电机位置回调"""
        try:
            # 尝试解析 JSON，兼容旧格式
            data = {}
            try:
                data = json.loads(msg.data)
            except ValueError:
                for pair in msg.data.split(','):
                    if ':' in pair:
                        key, val = pair.split(':')
                        data[key] = float(val)

            # 更新原始角度 (self.raw_angles 需要在 __init__ 中初始化，但这里直接用局部变量也行，或者加上)
            if not hasattr(self, 'raw_angles'):
                self.raw_angles = {}
            self.raw_angles.update(data)

            # 应用校准偏移
            corrected_angles = {}
            for axis, angle in data.items():
                # 确保只处理 X, Y, Z, A
                if axis in ['X', 'Y', 'Z', 'A']:
                    corrected_angles[axis] = self.calibration_manager.get_corrected_angle(axis, angle)

            self.current_angles.update(corrected_angles)

            if self.socketio:
                self.socketio.emit('pump_angles', self.current_angles)
                # 也可以选择推送 raw_angles 供前端调试
                self.socketio.emit('raw_angles', self.raw_angles)
        except Exception as e:
            rospy.logerr(f"Error parsing angles: {e}")

    def _voltage_cb(self, msg):
        """分光数据回调，兼容新的 JSON String 格式。"""
        try:
            data = json.loads(msg.data)
        except Exception:
            data = {"voltage": 0.0, "raw": msg.data}

        self.current_voltage = float(data.get('voltage', data.get('sample_voltage', 0.0)) or 0.0)
        self.current_absorbance = float(data.get('absorbance', 0.0) or 0.0)
        self.spectrometer_status = str(data.get('status', self.spectrometer_status))
        self.latest_spectrometer_payload = data

        if self.automation_running:
            self.data_manager.add_data_point(self.current_voltage, self.current_absorbance)
            self.voltage_history.append({
                "timestamp": datetime.now().isoformat(),
                "voltage": self.current_voltage,
                "absorbance": self.current_absorbance,
                "raw": data,
            })

        if self.socketio:
            self.socketio.emit('voltage', {
                "value": self.current_voltage,
                "absorbance": self.current_absorbance,
                "status": self.spectrometer_status,
                "raw": data,
            })

    def _mission_status_cb(self, msg):
        """任务状态回调"""
        self.mission_status = msg.data

    def _pid_error_cb(self, msg):
        """PID 误差回调"""
        if self.socketio:
            try:
                # 透传 JSON 数据
                data = json.loads(msg.data)
                self.socketio.emit('pid_error', data)
            except Exception:
                pass

    def _add_log(self, message, level='info'):
        """添加日志并推送到 WebSocket"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = "[{}] {}".format(timestamp, message)

        with self.log_lock:
            self.log_buffer.append(log_entry)
            if len(self.log_buffer) > self.max_logs:
                self.log_buffer = self.log_buffer[-self.max_logs:]

        # 推送实时日志
        if self.socketio:
            self.socketio.emit('log', {
                'message': message,
                'level': level,
                'timestamp': timestamp
            })

    # ========== 通信诊断回调 ==========

    def _web_mavros_state_cb(self, msg):
        """MAVROS State 回调 - 用于通信诊断"""
        prev_connected = self._mavros_state.get("connected", False)
        self._mavros_state = {
            "connected": msg.connected,
            "armed": msg.armed,
            "mode": msg.mode,
        }
        if prev_connected and not msg.connected:
            self._add_link_event("mavros_disconnect", "MAVROS 与飞控断开连接")
        elif not prev_connected and msg.connected:
            self._add_link_event("mavros_connect", "MAVROS 已连接飞控 mode=%s" % msg.mode)
        if self.socketio:
            self.socketio.emit('mavros_state', self._mavros_state)

    def _bridge_diag_cb(self, msg):
        """桥接节点诊断数据回调"""
        try:
            data = json.loads(msg.data)
            self._bridge_diag = data
            self._diag_history.append(data)
            if len(self._diag_history) > self._diag_history_max:
                self._diag_history = self._diag_history[-self._diag_history_max:]
            if self.socketio:
                self.socketio.emit('bridge_diagnostics', data)
        except Exception:
            pass

    def _radio_status_cb(self, msg):
        """电台链路状态回调"""
        try:
            data = json.loads(msg.data)
            self._radio_status = data
            if self.socketio:
                self.socketio.emit('radio_status', data)
        except Exception:
            pass

    def _add_link_event(self, event_type, detail):
        """记录链路事件"""
        entry = {
            "ts": datetime.now().isoformat(),
            "type": event_type,
            "detail": detail,
        }
        self._link_events.append(entry)
        if len(self._link_events) > self._link_events_max:
            self._link_events = self._link_events[-self._link_events_max:]

    def _resolve_static_folder(self):
        candidates = []
        if ROS_AVAILABLE:
            try:
                import rospkg
                pkg_path = rospkg.RosPack().get_path('usv_ros')
                candidates.append(os.path.join(pkg_path, 'static'))
            except Exception:
                pass

        candidates.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../static')))

        for folder in candidates:
            if os.path.isdir(folder):
                return folder
        return candidates[-1]

    def _setup_flask(self):
        """设置 Flask 和 SocketIO 应用。"""
        # 设置静态文件目录
        static_folder = self._resolve_static_folder()
        dist_folder = os.path.join(static_folder, 'dist')
        dist_index = os.path.join(dist_folder, 'index.html')

        self.app = Flask(__name__, static_folder=static_folder, static_url_path='/static')

        # 禁用浏览器缓存 (开发模式)
        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

        CORS(self.app)

        # 使用 threading 模式以兼容 ROS
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')

        # ================= HTTP 路由 =================
        def get_ui_mode():
            ui_mode = None
            try:
                ui_mode = rospy.get_param('~web_ui', None)
            except Exception:
                ui_mode = None
            if ui_mode:
                return str(ui_mode).strip().lower()
            env_mode = os.environ.get('USV_WEB_UI', '').strip().lower()
            if env_mode in ('dist', 'vite'):
                return env_mode
            return 'auto'

        @self.app.route('/')
        def index():
            ui_mode = get_ui_mode()
            if ui_mode != 'static' and os.path.isfile(dist_index):
                return send_from_directory(dist_folder, 'index.html')
            return send_from_directory(static_folder, 'index.html')

        @self.app.route('/api/ui/debug', methods=['GET'])
        def ui_debug():
            ui_mode = get_ui_mode()
            return jsonify({
                "ui_mode": ui_mode,
                "static_folder": static_folder,
                "dist_index": dist_index,
                "dist_index_exists": os.path.isfile(dist_index)
            })

        @self.app.route('/assets/<path:filename>')
        def dist_assets(filename):
            ui_mode = get_ui_mode()
            if ui_mode == 'static':
                abort(404)
            assets_dir = os.path.join(dist_folder, 'assets')
            if not os.path.isfile(os.path.join(assets_dir, filename)):
                abort(404)
            return send_from_directory(assets_dir, filename)

        @self.app.route('/api/config', methods=['GET'])
        def get_config():
            return jsonify(self.config_manager.get())

        @self.app.route('/api/config', methods=['POST'])
        def save_config():
            data = request.get_json()
            if self.config_manager.update(data):
                self._add_log("配置已保存", "success")
                return jsonify({"success": True, "message": "配置已保存"})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/config/reset', methods=['POST'])
        def reset_config():
            self.config_manager.reset()
            self._add_log("配置已重置", "warning")
            return jsonify({"success": True, "message": "已重置为默认配置"})

        # 任务控制 API
        @self.app.route('/api/mission/start', methods=['POST'])
        def start_mission(): return self._trigger_mission('start')

        @self.app.route('/api/mission/stop', methods=['POST'])
        def stop_mission(): return self._trigger_mission('stop')

        @self.app.route('/api/mission/pause', methods=['POST'])
        def pause_mission(): return self._trigger_mission('pause')

        @self.app.route('/api/mission/resume', methods=['POST'])
        def resume_mission(): return self._trigger_mission('resume')

        # 预设管理 API (新)
        @self.app.route('/api/presets/auto', methods=['GET'])
        def get_auto_presets():
            return jsonify({"success": True, "data": self.preset_manager.get_auto_preset_names()})

        @self.app.route('/api/preset/auto/<name>', methods=['GET'])
        def load_auto_preset(name):
            data = self.preset_manager.load_auto_preset(name)
            if data:
                normalized = ConfigManager._normalize_sampling_sequence({
                    'steps': data.get('steps', []),
                    'loop_count': data.get('loop_count', 1),
                })
                data['steps'] = normalized['steps']
                data['loop_count'] = normalized['loop_count']
                return jsonify({"success": True, "data": data})
            return jsonify({"success": False, "message": "预设不存在"}), 404

        @self.app.route('/api/preset/auto/<name>', methods=['POST'])
        def save_auto_preset(name):
            data = request.get_json()
            normalized = ConfigManager._normalize_sampling_sequence({
                'steps': data.get('steps', []),
                'loop_count': data.get('loop_count', 1),
            })
            if self.preset_manager.save_auto_preset(name, normalized['steps'], normalized['loop_count']):
                self._add_log(f"预设 '{name}' 已保存", "success")
                return jsonify({"success": True, "message": "预设已保存"})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/preset/auto/<name>', methods=['DELETE'])
        def delete_auto_preset(name):
            if self.preset_manager.delete_preset('auto', name):
                self._add_log(f"预设 '{name}' 已删除", "warning")
                return jsonify({"success": True, "message": "预设已删除"})
            return jsonify({"success": False, "message": "删除失败"}), 500

        # ================= 航点采样配置 API =================
        @self.app.route('/api/waypoint-sampling', methods=['GET'])
        def get_waypoint_sampling():
            """获取全部航点采样配置。"""
            config = self.config_manager.get()
            return jsonify({"success": True, "data": config.get('waypoint_sampling', {})})

        @self.app.route('/api/waypoint-sampling', methods=['POST'])
        def save_waypoint_sampling():
            """保存全部航点采样配置（整体覆盖）。"""
            data = request.get_json()
            if not isinstance(data, dict):
                return jsonify({"success": False, "message": "请求体应为对象"}), 400
            normalized = ConfigManager._normalize_waypoint_sampling(data)
            if self.config_manager.update({'waypoint_sampling': normalized}):
                self._add_log("航点采样配置已保存", "success")
                return jsonify({"success": True, "message": "航点采样配置已保存", "data": normalized})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/waypoint-sampling/<seq>', methods=['GET'])
        def get_waypoint_sampling_item(seq):
            """获取单个航点的采样配置。"""
            config = self.config_manager.get()
            wp_cfg = config.get('waypoint_sampling', {})
            try:
                seq_key = str(int(seq))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "航点编号无效"}), 400
            item = wp_cfg.get(seq_key)
            if item is None:
                return jsonify({"success": True, "data": None, "message": "该航点未配置，将使用全局默认值"})
            return jsonify({"success": True, "data": item})

        @self.app.route('/api/waypoint-sampling/<seq>', methods=['POST'])
        def save_waypoint_sampling_item(seq):
            """保存单个航点的采样配置。"""
            try:
                seq_key = str(int(seq))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "航点编号无效"}), 400
            data = request.get_json() or {}
            config = self.config_manager.get()
            wp_cfg = dict(config.get('waypoint_sampling', {}))
            wp_cfg[seq_key] = data
            normalized = ConfigManager._normalize_waypoint_sampling(wp_cfg)
            if self.config_manager.update({'waypoint_sampling': normalized}):
                self._add_log(f"航点 {seq_key} 采样配置已保存", "success")
                return jsonify({"success": True, "message": "已保存", "data": normalized.get(seq_key)})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/waypoint-sampling/<seq>', methods=['DELETE'])
        def delete_waypoint_sampling_item(seq):
            """删除单个航点的采样配置。"""
            try:
                seq_key = str(int(seq))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "航点编号无效"}), 400
            config = self.config_manager.get()
            wp_cfg = dict(config.get('waypoint_sampling', {}))
            if seq_key in wp_cfg:
                del wp_cfg[seq_key]
                if self.config_manager.update({'waypoint_sampling': wp_cfg}):
                    self._add_log(f"航点 {seq_key} 采样配置已删除", "warning")
                    return jsonify({"success": True, "message": "已删除"})
                return jsonify({"success": False, "message": "保存失败"}), 500
            return jsonify({"success": True, "message": "该航点本无配置"})

        @self.app.route('/api/waypoint-sampling/sync', methods=['POST'])
        def sync_waypoint_sampling_from_mavros():
            """从 MAVROS 拉取飞控 Mission 航点数量，自动补充默认采样配置。"""
            if self.standalone:
                return jsonify({"success": False, "message": "独立模式不支持飞控同步"}), 400
            try:
                from mavros_msgs.msg import WaypointList
                from mavros_msgs.srv import WaypointPull

                nav_seqs = []
                pull_error = None
                rospy.wait_for_service('/mavros/mission/pull', timeout=5.0)
                pull_srv = rospy.ServiceProxy('/mavros/mission/pull', WaypointPull)
                resp = pull_srv()
                if resp.success and resp.wp_received > 0:
                    nav_seqs = list(range(resp.wp_received))
                else:
                    pull_error = "pull success=%s wp_received=%s" % (resp.success, resp.wp_received)

                if not nav_seqs:
                    try:
                        waypoint_list = rospy.wait_for_message('/mavros/mission/waypoints', WaypointList, timeout=3.0)
                        nav_seqs = list(range(len(waypoint_list.waypoints or [])))
                    except Exception as cache_exc:
                        if pull_error is None:
                            pull_error = str(cache_exc)
                        else:
                            pull_error = "%s; cache=%s" % (pull_error, cache_exc)

                if not nav_seqs:
                    return jsonify({"success": False, "message": "拉取航点失败: %s" % (pull_error or "mission empty")}), 500

                config = self.config_manager.get()
                wp_cfg = dict(config.get('waypoint_sampling', {}))
                default_item = {
                    'enabled': True,
                    'loop_count': int(config.get('sampling_sequence', {}).get('loop_count', 1) or 1),
                    'retry_count': 0,
                    'hold_before_sampling_s': 3.0,
                    'on_fail': 'HOLD',
                }
                synced = 0
                for seq in nav_seqs:
                    seq_key = str(seq)
                    if seq_key not in wp_cfg:
                        wp_cfg[seq_key] = dict(default_item)
                        synced += 1

                valid_keys = set(str(s) for s in nav_seqs)
                removed_keys = [k for k in list(wp_cfg.keys()) if k not in valid_keys]
                for k in removed_keys:
                    del wp_cfg[k]

                normalized = ConfigManager._normalize_waypoint_sampling(wp_cfg)
                self.config_manager.update({'waypoint_sampling': normalized})
                self._add_log("已从飞控同步 %d 个航点采样配置" % len(nav_seqs), "success")
                return jsonify({
                    "success": True,
                    "message": "已同步 %d 个航点（新增 %d，移除 %d）" % (len(nav_seqs), synced, len(removed_keys)),
                    "data": normalized,
                    "synced": synced,
                    "total": len(nav_seqs),
                    "removed": len(removed_keys),
                })
            except Exception as e:
                return jsonify({"success": False, "message": "同步失败: %s" % str(e)}), 500

        # ================= 任务配置导入导出 API =================
        @self.app.route('/api/mission-config/export', methods=['GET'])
        def export_mission_config():
            """导出完整任务配置为 JSON 文件。"""
            config = self.config_manager.get()
            export_data = {
                "version": config.get("version", "1.0"),
                "exported_at": datetime.now().isoformat(),
                "mission": config.get("mission", {}),
                "pump_settings": config.get("pump_settings", {}),
                "sampling_sequence": config.get("sampling_sequence", {}),
                "waypoint_sampling": config.get("waypoint_sampling", {}),
            }
            return Response(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=usv_mission_config_%s.json" %
                         datetime.now().strftime("%Y%m%d_%H%M%S")}
            )

        @self.app.route('/api/mission-config/import', methods=['POST'])
        def import_mission_config():
            """导入任务配置 JSON。"""
            data = request.get_json()
            if not isinstance(data, dict):
                return jsonify({"success": False, "message": "请求体应为 JSON 对象"}), 400

            patch = {}
            if 'mission' in data and isinstance(data['mission'], dict):
                patch['mission'] = data['mission']
            if 'pump_settings' in data and isinstance(data['pump_settings'], dict):
                patch['pump_settings'] = data['pump_settings']
            if 'sampling_sequence' in data and isinstance(data['sampling_sequence'], dict):
                patch['sampling_sequence'] = ConfigManager._normalize_sampling_sequence(data['sampling_sequence'])
            if 'waypoint_sampling' in data and isinstance(data['waypoint_sampling'], dict):
                patch['waypoint_sampling'] = ConfigManager._normalize_waypoint_sampling(data['waypoint_sampling'])

            if not patch:
                return jsonify({"success": False, "message": "未找到可导入的配置字段"}), 400

            if self.config_manager.update(patch):
                self._add_log("任务配置已导入 (%d 个字段)" % len(patch), "success")
                return jsonify({"success": True, "message": "任务配置已导入", "fields": list(patch.keys())})
            return jsonify({"success": False, "message": "保存失败"}), 500

        # ================= 电机控制 API =================
        @self.app.route('/api/motor/command', methods=['POST'])
        def send_motor_command():
            """发送电机控制指令"""
            data = request.get_json()
            command = data.get('command', '')
            if not command:
                return jsonify({"success": False, "message": "指令为空"})

            if self.standalone:
                self._add_log(f"[模拟] 发送指令: {command}", "info")
                return jsonify({"success": True, "message": "指令已发送 (模拟模式)"})

            try:
                # 通过 ROS 服务发送指令
                if hasattr(self, 'command_pub') and self.command_pub:
                    self.command_pub.publish(command)
                    self._add_log(f"指令已发送: {command}", "success")
                    return jsonify({"success": True, "message": "指令已发送"})
                else:
                    return jsonify({"success": False, "message": "ROS Publisher 未初始化"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/motor/stop', methods=['POST'])
        def stop_all_motors():
            """紧急停止所有电机"""
            stop_command = "XDFV0J0YDFV0J0ZDFV0J0ADFV0J0\r\n"
            if self.standalone:
                self._add_log("[模拟] 紧急停止所有电机", "warning")
                return jsonify({"success": True, "message": "已停止 (模拟模式)"})

            try:
                if hasattr(self, 'command_pub') and self.command_pub:
                    self.command_pub.publish(stop_command)
                    self._add_log("紧急停止所有电机", "warning")
                    return jsonify({"success": True, "message": "已停止"})
                return jsonify({"success": False, "message": "ROS Publisher 未初始化"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        # ================= PID 配置 API =================
        @self.app.route('/api/pid/config', methods=['GET'])
        def get_pid_config():
            """获取当前 PID 参数"""
            pid_config = getattr(self, 'pid_config', {
                "Kp": 0.14, "Ki": 0.015, "Kd": 0.06,
                "output_min": 1.0, "output_max": 8.0
            })
            return jsonify({"success": True, "data": pid_config})

        @self.app.route('/api/pid/config', methods=['POST'])
        def set_pid_config():
            """设置 PID 参数"""
            data = request.get_json()
            self.pid_config = {
                "Kp": data.get("Kp", 0.14),
                "Ki": data.get("Ki", 0.015),
                "Kd": data.get("Kd", 0.06),
                "output_min": data.get("output_min", 1.0),
                "output_max": data.get("output_max", 8.0)
            }

            # 生成 PIDCFG 指令
            cmd = "PIDCFG:{Kp},{Ki},{Kd},{output_min},{output_max}\r\n".format(**self.pid_config)

            if not self.standalone and hasattr(self, 'command_pub') and self.command_pub:
                self.command_pub.publish(cmd)

            self._add_log(f"PID 参数已更新: Kp={self.pid_config['Kp']}", "success")
            return jsonify({"success": True, "message": "PID 参数已更新"})

        @self.app.route('/api/pid/test', methods=['POST'])
        def start_pid_test():
            """启动 PID 测试"""
            data = request.get_json()
            motor = data.get('motor', 'X')
            direction = data.get('direction', 'F')
            angle = data.get('angle', 90.0)
            runs = data.get('runs', 5)

            cmd = f"PIDTEST:{motor},{direction},{angle},{runs}\r\n"

            if self.standalone:
                self._add_log(f"[模拟] PID 测试: {cmd.strip()}", "info")
                return jsonify({"success": True, "message": "测试已启动 (模拟模式)"})

            if hasattr(self, 'command_pub') and self.command_pub:
                self.command_pub.publish(cmd)
                self._add_log(f"PID 测试已启动: {motor}轴 {angle}°", "info")
                return jsonify({"success": True, "message": "测试已启动"})

            return jsonify({"success": False, "message": "ROS Publisher 未初始化"})

        # ================= 校准启动 API =================
        @self.app.route('/api/calibration/start', methods=['POST'])
        def start_calibration():
            """启动电机校准"""
            data = request.get_json()
            motors = data.get('motors', 'XYZA')

            cmd = f"CAL{motors}\r\n"

            if self.standalone:
                self._add_log(f"[模拟] 校准: {motors}", "info")
                return jsonify({"success": True, "message": "校准已启动 (模拟模式)"})

            if hasattr(self, 'command_pub') and self.command_pub:
                self.command_pub.publish(cmd)
                self._add_log(f"校准已启动: {motors}", "info")
                return jsonify({"success": True, "message": "校准已启动"})

            return jsonify({"success": False, "message": "ROS Publisher 未初始化"})

        # ================= WebSocket 事件 =================
        @self.socketio.on('connect')
        def handle_connect():
            emit('status', {
                "pump_connected": self.pump_connected,
                "automation_running": self.automation_running,
                "mission_status": self.mission_status,
                "spectrometer_status": self.spectrometer_status,
            })
            emit('angles', self.current_angles)
            emit('voltage', {
                "value": self.current_voltage,
                "absorbance": self.current_absorbance,
                "status": self.spectrometer_status,
                "raw": self.latest_spectrometer_payload,
            })
            emit('injection_pump_status', self.injection_pump_status)

        # ================= 数据 API =================
        @self.app.route('/api/data/voltage', methods=['GET'])
        def get_voltage_history():
            return jsonify({"success": True, "data": self.voltage_history})

        @self.app.route('/api/data/voltage/clear', methods=['POST'])
        def clear_voltage_history():
            self.voltage_history = []
            return jsonify({"success": True, "message": "历史数据已清除"})

        # ================= 任务数据 API =================
        @self.app.route('/api/data/missions', methods=['GET'])
        def list_missions():
            return jsonify({"success": True, "data": self.data_manager.list_missions()})

        @self.app.route('/api/data/mission/<mission_id>', methods=['GET'])
        def get_mission_data(mission_id):
            data = self.data_manager.get_mission(mission_id)
            if data:
                return jsonify({"success": True, "data": data})
            return jsonify({"success": False, "message": "任务不存在"}), 404

        @self.app.route('/api/data/mission/<mission_id>', methods=['DELETE'])
        def delete_mission_data(mission_id):
            if self.data_manager.delete_mission(mission_id):
                return jsonify({"success": True, "message": "任务已删除"})
            return jsonify({"success": False, "message": "删除失败"}), 500

        @self.app.route('/api/data/mission/<mission_id>/csv', methods=['GET'])
        def download_mission_csv(mission_id):
            """下载任务数据为 CSV 文件。"""
            data = self.data_manager.get_mission(mission_id)
            if not data:
                return jsonify({"success": False, "message": "任务不存在"}), 404
            import io, csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["timestamp", "voltage", "absorbance"])
            for pt in data.get("data_points", []):
                writer.writerow([pt.get("timestamp", ""), pt.get("voltage", ""), pt.get("absorbance", "")])
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=mission_{mission_id}.csv"}
            )

        # ================= 系统日志 API =================
        _LOG_WHITELIST = {"usv_system.log", "roscore.log", "mavlink_router.log"}

        def _safe_log_path(filename):
            """安全检查日志文件名（防止路径穿越）。"""
            if not filename or '/' in filename or '\\' in filename or '..' in filename:
                return None
            if filename not in _LOG_WHITELIST:
                return None
            path = os.path.join(LOG_DIR, filename)
            if not os.path.isfile(path):
                return None
            return path

        @self.app.route('/api/logs/files', methods=['GET'])
        def list_log_files():
            """列出可查看的日志文件。"""
            files = []
            if os.path.isdir(LOG_DIR):
                for name in sorted(os.listdir(LOG_DIR)):
                    if name in _LOG_WHITELIST:
                        path = os.path.join(LOG_DIR, name)
                        stat = os.stat(path)
                        files.append({
                            "name": name,
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        })
            return jsonify({"success": True, "data": files})

        @self.app.route('/api/logs/<filename>', methods=['GET'])
        def get_log_content(filename):
            """获取日志文件最后 N 行。"""
            path = _safe_log_path(filename)
            if not path:
                return jsonify({"success": False, "message": "文件不存在或不允许访问"}), 404
            lines_count = min(int(request.args.get('lines', 100)), 2000)
            try:
                import collections
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    tail = collections.deque(f, maxlen=lines_count)
                return jsonify({"success": True, "data": list(tail)})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        # ================= 零点校准 API =================
        @self.app.route('/api/calibration/offsets', methods=['GET'])
        def get_offsets():
            return jsonify({"success": True, "data": self.calibration_manager.offsets})

        @self.app.route('/api/calibration/zero', methods=['POST'])
        def set_zero():
            data = request.get_json()
            axis = data.get('axis')

            if not axis:
                if hasattr(self, 'raw_angles'):
                    for ax in ['X', 'Y', 'Z', 'A']:
                        if ax in self.raw_angles:
                            self.calibration_manager.set_zero(ax, self.raw_angles[ax])
                return jsonify({"success": True, "message": "所有轴零点已设置"})

            if hasattr(self, 'raw_angles') and axis in self.raw_angles:
                self.calibration_manager.set_zero(axis, self.raw_angles[axis])
                return jsonify({"success": True, "message": f"{axis} 轴零点已设置"})

            return jsonify({"success": False, "message": "无法获取当前原始角度"}), 400

        @self.app.route('/api/calibration/reset', methods=['POST'])
        def reset_zero():
            data = request.get_json()
            axis = data.get('axis')
            self.calibration_manager.reset(axis)
            return jsonify({"success": True, "message": "零点已重置"})

        # ================= 进样泵控制 API =================
        @self.app.route('/api/injection-pump/status', methods=['POST'])
        def get_injection_pump_status():
            if self.standalone:
                return jsonify({"success": True, "data": self.injection_pump_status, "message": "模拟模式"})

            try:
                service = rospy.ServiceProxy('/usv/injection_pump_get_status', Trigger)
                resp = service()
                if resp.success:
                    data = json.loads(resp.message)
                    self.injection_pump_status = {
                        "enabled": bool(data.get("enabled", False)),
                        "speed": int(data.get("speed", 0)),
                        "last_response": data.get("last_response", ""),
                        "last_error": data.get("last_error", ""),
                    }
                return jsonify({"success": resp.success, "data": self.injection_pump_status, "message": resp.message})
            except Exception as e:
                return jsonify({"success": False, "message": str(e), "data": self.injection_pump_status}), 500

        @self.app.route('/api/injection-pump/on', methods=['POST'])
        def turn_injection_pump_on():
            if self.standalone:
                self.injection_pump_status["enabled"] = True
                return jsonify({"success": True, "data": self.injection_pump_status, "message": "模拟模式已开启"})

            try:
                service = rospy.ServiceProxy('/usv/injection_pump_on', Trigger)
                resp = service()
                return jsonify({"success": resp.success, "data": self.injection_pump_status, "message": resp.message})
            except Exception as e:
                return jsonify({"success": False, "message": str(e), "data": self.injection_pump_status}), 500

        @self.app.route('/api/injection-pump/off', methods=['POST'])
        def turn_injection_pump_off():
            if self.standalone:
                self.injection_pump_status["enabled"] = False
                self.injection_pump_status["speed"] = 0
                return jsonify({"success": True, "data": self.injection_pump_status, "message": "模拟模式已关闭"})

            try:
                service = rospy.ServiceProxy('/usv/injection_pump_off', Trigger)
                resp = service()
                return jsonify({"success": resp.success, "data": self.injection_pump_status, "message": resp.message})
            except Exception as e:
                return jsonify({"success": False, "message": str(e), "data": self.injection_pump_status}), 500

        @self.app.route('/api/injection-pump/set', methods=['POST'])
        def set_injection_pump_speed():
            data = request.get_json() or {}
            speed = int(data.get('speed', 0))
            speed = max(0, min(100, speed))
            command = f'PUMP:SET:{speed}'

            if self.standalone:
                self.injection_pump_status["enabled"] = speed > 0
                self.injection_pump_status["speed"] = speed
                self.injection_pump_status["last_response"] = command
                self.injection_pump_status["last_error"] = ""
                return jsonify({"success": True, "data": self.injection_pump_status, "message": "模拟模式已设置"})

            if self.command_pub:
                self.command_pub.publish(command)
                self.injection_pump_status["enabled"] = speed > 0
                self.injection_pump_status["speed"] = speed
                self.injection_pump_status["last_response"] = command
                self.injection_pump_status["last_error"] = ""
                return jsonify({"success": True, "data": self.injection_pump_status, "message": "进样泵转速已发送"})

            return jsonify({"success": False, "message": "ROS Publisher 未初始化", "data": self.injection_pump_status}), 500

        # ================= 硬件配置 API =================
        @self.app.route('/api/hardware/config', methods=['GET'])
        def get_hardware_config():
            hw = self.config_manager.get().get('hardware', DEFAULT_CONFIG['hardware'])
            return jsonify({"success": True, "data": hw})

        @self.app.route('/api/hardware/config', methods=['POST'])
        def save_hardware_config():
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "message": "请求体为空"}), 400
            current = self.config_manager.get()
            hw = current.get('hardware', dict(DEFAULT_CONFIG['hardware']))
            for key in DEFAULT_CONFIG['hardware']:
                if key in data:
                    hw[key] = data[key]
            current['hardware'] = hw
            if self.config_manager.update(current):
                self._add_log("硬件配置已保存", "success")
                return jsonify({"success": True, "message": "硬件配置已保存", "data": hw})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/hardware/serial-ports', methods=['GET'])
        def list_serial_ports():
            ports = []
            try:
                import serial.tools.list_ports as lp
                for p in lp.comports():
                    ports.append({
                        "path": p.device,
                        "description": p.description or "",
                        "hwid": p.hwid or ""
                    })
            except Exception:
                pass
            # 补充 /dev/serial/by-id 映射
            by_id_dir = "/dev/serial/by-id"
            try:
                import glob
                for link in glob.glob(os.path.join(by_id_dir, "*")):
                    real = os.path.realpath(link)
                    found = False
                    for p in ports:
                        if p["path"] == real:
                            p["by_id"] = link
                            found = True
                            break
                    if not found:
                        ports.append({"path": real, "description": os.path.basename(link), "hwid": "", "by_id": link})
            except Exception:
                pass
            return jsonify({"success": True, "ports": ports})



        @self.app.route('/api/hardware/test-pump-port', methods=['POST'])
        def test_pump_port():
            data = request.get_json() or {}
            port = data.get('serial_port', '/dev/ttyUSB0')
            baud = int(data.get('baudrate', 115200))
            tout = float(data.get('timeout', 1.0))
            try:
                import serial as pyserial
                conn = pyserial.Serial(port=port, baudrate=baud, timeout=tout)
                conn.close()
                return jsonify({"success": True, "message": f"串口 {port} 可打开"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)})



        @self.app.route('/api/hardware/apply', methods=['POST'])
        def apply_hardware_config():
            """保存硬件配置并通知节点运行时重连。"""
            data = request.get_json() or {}
            # 先保存
            current = self.config_manager.get()
            hw = current.get('hardware', dict(DEFAULT_CONFIG['hardware']))
            for key in DEFAULT_CONFIG['hardware']:
                if key in data:
                    hw[key] = data[key]
            current['hardware'] = hw
            if not self.config_manager.update(current):
                return jsonify({"success": False, "message": "保存失败"}), 500

            results = {"pump": None}

            # 通知 pump 节点重连
            if not self.standalone:
                try:
                    rospy.set_param('/pump_control_node/serial_port', hw['pump_serial_port'])
                    rospy.set_param('/pump_control_node/baudrate', int(hw['pump_baudrate']))
                    rospy.set_param('/pump_control_node/timeout', float(hw['pump_timeout']))
                    svc = rospy.ServiceProxy('/usv/pump_reconnect', Trigger)
                    svc.wait_for_service(timeout=3.0)
                    resp = svc()
                    results["pump"] = {"success": resp.success, "message": resp.message}
                except Exception as e:
                    results["pump"] = {"success": False, "message": str(e)}

            else:
                results["pump"] = {"success": True, "message": "独立模式，跳过"}

            self._add_log("硬件配置已应用", "info")
            return jsonify({"success": True, "message": "硬件配置已应用", "data": hw, "results": results})

        # ================= 通信诊断 API =================
        @self.app.route('/api/diagnostics/link', methods=['GET'])
        def get_link_diagnostics():
            """获取完整通信链路诊断报告"""
            # 检查 ROS 节点状态
            nodes = []
            if ROS_AVAILABLE and not self.standalone:
                try:
                    import rosnode
                    node_list = rosnode.get_node_names()
                    for n in ['/mavros', '/usv_mavlink_bridge', '/mavlink_trigger_node',
                              '/pump_control_node', '/web_config_server']:
                        nodes.append({"name": n, "alive": n in node_list})
                except Exception:
                    nodes = [{"name": "unknown", "alive": False, "error": "rosnode API unavailable"}]

            # 检查 mavlink-routerd 进程状态
            router_alive = False
            try:
                import subprocess
                # Check using pgrep since process might not be started from the exact pid file or pid file could be stale
                result = subprocess.run(["pgrep", "-f", "mavlink-routerd"], capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    router_alive = True
            except Exception:
                pass
            nodes.append({"name": "mavlink-routerd", "alive": router_alive})

            return jsonify({
                "success": True,
                "data": {
                    "mavros": self._mavros_state,
                    "bridge": self._bridge_diag,
                    "nodes": nodes,
                    "link_events_recent": self._link_events[-20:],
                }
            })

        @self.app.route('/api/diagnostics/history', methods=['GET'])
        def get_diag_history():
            """获取桥接节点诊断历史 (用于趋势分析)"""
            return jsonify({"success": True, "data": self._diag_history})

        @self.app.route('/api/diagnostics/events', methods=['GET'])
        def get_link_events():
            """获取链路事件日志"""
            return jsonify({"success": True, "data": self._link_events})

        @self.app.route('/api/diagnostics/export', methods=['GET'])
        def export_diagnostics():
            """导出结构化诊断报告 (JSON)"""
            report = {
                "exported_at": datetime.now().isoformat(),
                "mavros_state": self._mavros_state,
                "bridge_latest": self._bridge_diag,
                "bridge_history": self._diag_history,
                "link_events": self._link_events,
                "config": {
                    "fcu_url": rospy.get_param('/mavros/fcu_url', 'unknown') if ROS_AVAILABLE and not self.standalone else 'standalone',
                    "gcs_url": rospy.get_param('/mavros/gcs_url', '') if ROS_AVAILABLE and not self.standalone else '',
                },
            }
            return Response(
                json.dumps(report, indent=2, ensure_ascii=False),
                mimetype='application/json',
                headers={"Content-Disposition": "attachment; filename=usv_diagnostics_%s.json" %
                         datetime.now().strftime("%Y%m%d_%H%M%S")}
            )

    def _trigger_mission(self, action):
        """触发任务动作。"""
        if self.standalone:
            msg = "独立模式下无法触发任务 (需要 ROS 集成)"
            self._add_log(msg, "warning")
            return jsonify({"success": False, "message": msg})

        service_map = {
            'start': '/usv/automation_start',
            'stop': '/usv/automation_stop',
            'pause': '/usv/automation_pause',
            'resume': '/usv/automation_resume'
        }

        service_name = service_map.get(action)
        if not service_name:
            msg = f"不支持的任务动作: {action}"
            self._add_log(msg, "warning")
            return jsonify({"success": False, "message": msg}), 400

        try:
            request_data = request.get_json(silent=True) or {}

            # 如果是启动，优先使用请求中携带的最新配置
            if action == 'start':
                sampling_sequence = request_data.get('sampling_sequence')
                waypoint_sampling = request_data.get('waypoint_sampling')
                config_patch = {}
                if isinstance(sampling_sequence, dict):
                    normalized_sequence = ConfigManager._normalize_sampling_sequence(sampling_sequence)
                    config_patch['sampling_sequence'] = normalized_sequence
                if isinstance(waypoint_sampling, dict):
                    config_patch['waypoint_sampling'] = ConfigManager._normalize_waypoint_sampling(waypoint_sampling)
                if config_patch:
                    self.config_manager.update(config_patch)
                self._publish_steps()
                # 开始记录数据
                self.data_manager.start_mission(self.config_manager.get().get('mission', {}).get('name', ''))

            # 如果是停止，停止记录
            if action == 'stop':
                self.data_manager.stop_mission()

            # 调用服务
            rospy.wait_for_service(service_name, timeout=2.0)
            service = rospy.ServiceProxy(service_name, Trigger)
            resp = service()

            self._add_log(f"任务 {action}: {resp.message}", "success" if resp.success else "error")
            return jsonify({"success": resp.success, "message": resp.message})

        except Exception as e:
            msg = f"服务调用失败: {str(e)}"
            self._add_log(msg, "error")
            return jsonify({"success": False, "message": msg})

    def _publish_steps(self):
        """发布采样步骤到 ROS。"""
        if self.standalone or not self.steps_pub:
            return

        config = self.config_manager.get()
        steps_data = {
            "steps": config.get('sampling_sequence', {}).get('steps', []),
            "loop_count": config.get('sampling_sequence', {}).get('loop_count', 1),
            "pid_mode": config.get('pump_settings', {}).get('pid_mode', True),
            "pid_precision": config.get('pump_settings', {}).get('pid_precision', 0.1),
            "waypoint_sampling": config.get('waypoint_sampling', {}),
        }
        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)
        self._add_log("配置已发送到控制节点")

    def _data_push_loop(self):
        """后台线程：定时推送实时数据"""
        rate = 20 # Hz
        while not rospy.is_shutdown():
            if self.socketio:
                self.socketio.emit('status', {
                    "pump_connected": self.pump_connected,
                    "automation_running": self.automation_running,
                    "mission_status": self.mission_status
                })
                self.socketio.emit('angles', self.current_angles)
                self.socketio.emit('voltage', {"value": self.current_voltage})

            if self.standalone:
                # 独立模式模拟数据变化 (测试用)
                if self.automation_running:
                    for k in self.current_angles:
                        self.current_angles[k] = (self.current_angles[k] + 1) % 360
                    # 模拟电压变化
                    self.current_voltage = (self.current_voltage + 0.1) % 5.0
                    if self.automation_running:
                         self.voltage_history.append({
                            "timestamp": datetime.now().isoformat(),
                            "voltage": self.current_voltage
                        })
                time.sleep(1.0/rate)
            else:
                threading.Event().wait(1.0/rate)

    def run(self):
        """运行服务器。"""
        if not FLASK_AVAILABLE:
            rospy.logerr("Flask/SocketIO not available, cannot start web server")
            if not self.standalone:
                rospy.spin()
            return

        rospy.loginfo("Web server starting at http://%s:%d", self.host, self.port)
        self._add_log("Web 服务器启动中...")

        # 启动数据推送线程
        data_thread = threading.Thread(target=self._data_push_loop)
        data_thread.daemon = True
        data_thread.start()

        # 启动 SocketIO 服务器
        try:
            self.socketio.run(
                self.app,
                host=self.host,
                port=self.port,
                use_reloader=False,
                allow_unsafe_werkzeug=True
            )
        except Exception as e:
            rospy.logerr(f"Server error: {e}")
        except KeyboardInterrupt:
            rospy.loginfo("Server stopped by user")

def main():
    """主函数"""
    standalone = not ROS_AVAILABLE

    if not standalone:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', 11311))
            sock.close()
            if result != 0:
                print("ROS MASTER 未运行，切换到独立模式")
                standalone = True
        except Exception:
            standalone = True

    try:
        server = WebConfigServer(standalone=standalone)
        server.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
