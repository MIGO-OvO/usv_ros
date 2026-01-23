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
    from flask import Flask, request, jsonify, send_from_directory
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

# ==================== 指令生成器 (移植自上位机) ====================
class CommandGenerator:
    """电机控制指令生成器 (Web版)"""
    
    MOTOR_NAMES = ['X', 'Y', 'Z', 'A']
    DIRECTION_MAP = {'F': 1, 'B': -1}
    COMMAND_TERMINATOR = '\r\n'

    def __init__(self):
        self.current_angles = {m: 0.0 for m in self.MOTOR_NAMES}
        
        # 自动模式状态
        self.initial_angle_base = {m: None for m in self.MOTOR_NAMES}
        self.accumulated_rotation = {m: 0.0 for m in self.MOTOR_NAMES}
        self.expected_angles = {m: 0.0 for m in self.MOTOR_NAMES}
        self.is_first_command = True
        
        # 校准参数
        self.calibration_enabled = False
        self.calibration_amplitude = 1.0
        self.theoretical_deviations = {m: None for m in self.MOTOR_NAMES}

    def set_current_angles(self, angles):
        self.current_angles.update(angles)

    def reset_for_auto_mode(self):
        self.is_first_command = True
        for motor in self.MOTOR_NAMES:
            self.initial_angle_base[motor] = self.current_angles.get(motor)
            self.accumulated_rotation[motor] = 0.0

    def generate_command(self, step_params, mode="manual", pid_mode=False, pid_precision=0.1):
        """生成电机控制指令 (支持 PID/R指令)"""
        command = ""
        
        # 自动模式初始基准
        if mode == "auto" and self.is_first_command:
            for motor in self.MOTOR_NAMES:
                if self.current_angles.get(motor) is not None:
                    self.initial_angle_base[motor] = self.current_angles[motor]
                else:
                    self.initial_angle_base[motor] = 0.0

        for motor in self.MOTOR_NAMES:
            config = step_params.get(motor, {})
            enable = config.get("enable", "D")

            if enable != "E":
                continue

            direction = config.get("direction", "F")
            speed = config.get("speed", "5")
            raw_angle = config.get("angle", 0)
            is_continuous = config.get("continuous", False)
            dir_factor = self.DIRECTION_MAP.get(direction, 1)

            try:
                # 1. 连续模式 (G指令)
                if is_continuous:
                    command += f"{motor}EFV{speed}JG"
                    continue

                raw_rotation = float(raw_angle)

                if mode == "auto":
                    # 自动模式：基于增量计算绝对位置
                    if self.initial_angle_base[motor] is None:
                        self.initial_angle_base[motor] = self.current_angles.get(motor, 0.0)

                    raw_rotation_signed = raw_rotation * dir_factor
                    self.accumulated_rotation[motor] += raw_rotation_signed
                    
                    # 简单的自动模式逻辑：累积角度
                    # 注意：这里简化了校准逻辑，直接使用累积角度
                    target_rotation = abs(raw_rotation_signed)
                    
                    if raw_rotation_signed < 0:
                        direction = "B"
                    else:
                        direction = "F"
                else:
                    # 手动模式：直接使用输入角度
                    target_rotation = raw_rotation

                # 2. 统一使用 PID 模式 (R指令) - 除非明确指定开环且非 continuous
                # 只有 continuous 模式使用旧版 G 指令
                # 非 continuous 模式统一强制使用 R 指令
                
                # 格式: {motor}E{dir}R{angle}P{precision}
                # 示例: XEFR90.0P0.1
                # 即使前端未传 pid_precision，也使用默认值 0.1
                actual_precision = pid_precision if pid_precision is not None else 0.1
                command += f"{motor}E{direction}R{target_rotation:.1f}P{actual_precision}"

            except (ValueError, TypeError) as e:
                print(f"电机{motor}参数错误: {e}")
                continue

        self.is_first_command = False
        if command:
            return command + self.COMMAND_TERMINATOR
        return ""

# 配置文件路径
CONFIG_DIR = os.path.expanduser("~/usv_ws/config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "sampling_config.json")

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
                "interval": 2000
            },
            {
                "name": "进样混合",
                "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "180"},
                "Y": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
                "Z": {"enable": "D"},
                "A": {"enable": "E", "direction": "F", "speed": "3", "angle": "180"},
                "interval": 5000
            },
            {
                "name": "停止",
                "X": {"enable": "D"},
                "Y": {"enable": "D"},
                "Z": {"enable": "D"},
                "A": {"enable": "D"},
                "interval": 1000
            }
        ]
    },
    "detection_settings": {
        "duration": 5.0,
        "sample_rate": 100
    }
}

class ConfigManager(object):
    """配置文件管理器。"""

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

    def update(self, data):
        """更新配置。"""
        self._merge_config(data)
        return self.save()

    def reset(self):
        """重置为默认配置。"""
        self.config = DEFAULT_CONFIG.copy()
        return self.save()

    def get(self):
        """获取当前配置。"""
        return self.config.copy()


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
        self.command_generator = CommandGenerator() # 新增指令生成器

        # 日志缓冲
        self.log_buffer = []
        self.log_lock = threading.Lock()
        self.max_logs = 100

        # 状态缓存
        self.pump_connected = False
        self.automation_running = False
        self.automation_paused = False
        self.current_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
        self.pid_errors = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}

        # 自动化运行状态
        self.auto_thread = None
        self.auto_stop_event = threading.Event()
        self.auto_pause_event = threading.Event()
        self.auto_current_step = 0
        self.auto_current_loop = 0
        self.auto_total_steps = 0
        self.auto_total_loops = 1
        self.auto_pid_mode = False
        self.auto_pid_precision = 0.1

        # 无人船遥测数据缓存
        self.usv_telemetry = {
            "connected": False,
            "armed": False,
            "mode": "UNKNOWN",
            "battery_voltage": 0.0,
            "battery_percent": 0,
            "gps_fix": 0,
            "satellites": 0,
            "latitude": 0.0,
            "longitude": 0.0,
            "heading": 0.0,
            "speed": 0.0,
            "altitude": 0.0
        }

        # ROS 订阅 (仅在非独立模式)
        if not self.standalone:
            self.status_sub = rospy.Subscriber('/usv/pump_status', String, self._status_cb)
            self.angles_sub = rospy.Subscriber('/usv/pump_angles', String, self._angles_cb)
            self.steps_pub = rospy.Publisher('/usv/automation_steps', String, queue_size=1)
            
            # 电机控制指令发布器
            self.command_pub = rospy.Publisher('/usv/motor_command', String, queue_size=10)

            # MAVROS 遥测订阅
            self._setup_mavros_subscribers()
        else:
            self.status_sub = None
            self.angles_sub = None
            self.steps_pub = None
            self.command_pub = None

        # Flask & SocketIO
        self.app = None
        self.socketio = None
        self.server_thread = None

        if FLASK_AVAILABLE:
            self._setup_flask()
        else:
            rospy.logerr("Flask/SocketIO not available!")

        mode_str = "独立模式 (无 ROS)" if self.standalone else "ROS 模式"
        rospy.loginfo("Web Config Server initialized (%s)", mode_str)
        rospy.loginfo("  URL: http://%s:%d", self.host, self.port)

    def _status_cb(self, msg):
        """泵状态回调。"""
        status = msg.data.lower()
        self.pump_connected = 'connected' in status
        self.automation_running = 'automation' in status and 'running' not in status.replace('running', '')
        if 'automation' in status:
            self.automation_running = 'running' in status or '运行' in status
        
        # 只记录关键状态变化，避免刷屏
        # self._add_log("[状态] " + msg.data)

    def _angles_cb(self, msg):
        """角度数据回调。"""
        try:
            for pair in msg.data.split(','):
                if ':' in pair:
                    key, val = pair.split(':')
                    if key in self.current_angles:
                        val_float = float(val)
                        self.current_angles[key] = val_float
            
            # 同步更新生成器的当前角度
            if hasattr(self, 'command_generator'):
                self.command_generator.set_current_angles(self.current_angles)
            
            # 推送PID误差数据 (如果有)
            if hasattr(self, 'pid_errors') and self.socketio:
                self.socketio.emit('pid_data', self.pid_errors)
        except Exception:
            pass

    def _setup_mavros_subscribers(self):
        """设置 MAVROS 遥测订阅。"""
        try:
            from mavros_msgs.msg import State, BatteryStatus
            from sensor_msgs.msg import NavSatFix, Imu
            from geometry_msgs.msg import TwistStamped
            from std_msgs.msg import Float64

            # 飞控状态
            rospy.Subscriber('/mavros/state', State, self._mavros_state_cb)
            # 电池状态
            rospy.Subscriber('/mavros/battery', BatteryStatus, self._battery_cb)
            # GPS 位置
            rospy.Subscriber('/mavros/global_position/global', NavSatFix, self._gps_cb)
            # 航向
            rospy.Subscriber('/mavros/global_position/compass_hdg', Float64, self._heading_cb)
            # 速度
            rospy.Subscriber('/mavros/local_position/velocity_local', TwistStamped, self._velocity_cb)

            rospy.loginfo("MAVROS 遥测订阅已设置")
        except ImportError as e:
            rospy.logwarn("MAVROS 消息类型未安装: %s", str(e))
        except Exception as e:
            rospy.logwarn("MAVROS 订阅设置失败: %s", str(e))

    def _mavros_state_cb(self, msg):
        """MAVROS 状态回调。"""
        self.usv_telemetry["connected"] = msg.connected
        self.usv_telemetry["armed"] = msg.armed
        self.usv_telemetry["mode"] = msg.mode

        # 推送到 WebSocket
        if self.socketio:
            self.socketio.emit('telemetry', self.usv_telemetry)

    def _battery_cb(self, msg):
        """电池状态回调。"""
        self.usv_telemetry["battery_voltage"] = msg.voltage
        self.usv_telemetry["battery_percent"] = int(msg.percentage * 100) if msg.percentage <= 1.0 else int(msg.percentage)

    def _gps_cb(self, msg):
        """GPS 位置回调。"""
        self.usv_telemetry["latitude"] = msg.latitude
        self.usv_telemetry["longitude"] = msg.longitude
        self.usv_telemetry["altitude"] = msg.altitude
        # GPS fix 类型: 0=无, 1=单点, 2=2D, 3=3D
        self.usv_telemetry["gps_fix"] = msg.status.status + 1 if msg.status.status >= 0 else 0
        self.usv_telemetry["satellites"] = getattr(msg.status, 'satellites_visible', 0) if hasattr(msg.status, 'satellites_visible') else 0

    def _heading_cb(self, msg):
        """航向回调。"""
        self.usv_telemetry["heading"] = msg.data

    def _velocity_cb(self, msg):
        """速度回调。"""
        import math
        vx = msg.twist.linear.x
        vy = msg.twist.linear.y
        self.usv_telemetry["speed"] = math.sqrt(vx*vx + vy*vy)

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

    def _setup_flask(self):
        """设置 Flask 和 SocketIO 应用。"""
        # 设置静态文件目录
        static_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '../static'))
        self.app = Flask(__name__, static_folder=static_folder, static_url_path='/static')
        
        # 禁用浏览器缓存 (开发模式)
        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        
        CORS(self.app)
        
        # 使用 threading 模式以兼容 ROS
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')

        # ================= HTTP 路由 =================
        @self.app.route('/', defaults={'path': ''})
        @self.app.route('/<path:path>')
        def index(path):
            if path.startswith('api/') or path.startswith('static/'):
                return jsonify({"success": False, "message": "Not Found"}), 404
            return send_from_directory(static_folder, 'index.html')

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
                return jsonify({"success": True, "data": data})
            return jsonify({"success": False, "message": "预设不存在"}), 404

        @self.app.route('/api/preset/auto/<name>', methods=['POST'])
        def save_auto_preset(name):
            data = request.get_json()
            if self.preset_manager.save_auto_preset(name, data['steps'], data['loop_count']):
                self._add_log(f"预设 '{name}' 已保存", "success")
                return jsonify({"success": True, "message": "预设已保存"})
            return jsonify({"success": False, "message": "保存失败"}), 500
            
        @self.app.route('/api/preset/auto/<name>', methods=['DELETE'])
        def delete_auto_preset(name):
            if self.preset_manager.delete_preset('auto', name):
                self._add_log(f"预设 '{name}' 已删除", "warning")
                return jsonify({"success": True, "message": "预设已删除"})
            return jsonify({"success": False, "message": "删除失败"}), 500

        # ================= 电机控制 API =================
        @self.app.route('/api/motor/command', methods=['POST'])
        def send_motor_command():
            """发送电机控制指令 (R指令格式: XEFV5J90.0 或 XEFR90.0P0.1)"""
            data = request.get_json()
            command = data.get('command', '')
            if not command:
                return jsonify({"success": False, "message": "指令为空"})

            # 确保指令以 \r\n 结尾
            if not command.endswith('\r\n'):
                command = command.rstrip() + '\r\n'

            if self.standalone:
                self._add_log(f"[模拟] 发送指令: {command.strip()}", "info")
                return jsonify({"success": True, "message": "指令已发送 (模拟模式)"})

            try:
                if self.command_pub:
                    msg = String()
                    msg.data = command
                    self.command_pub.publish(msg)
                    self._add_log(f"指令已发送: {command.strip()}", "success")
                    return jsonify({"success": True, "message": "指令已发送"})
                else:
                    return jsonify({"success": False, "message": "ROS Publisher 未初始化"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/motor/generate', methods=['POST'])
        def generate_motor_command():
            """根据参数生成电机控制指令 (使用 CommandGenerator)"""
            data = request.get_json()
            motor = data.get('motor', 'X')
            
            # 构建单电机步骤参数
            step_params = {
                motor: {
                    "enable": data.get('enable', 'E'),
                    "direction": data.get('direction', 'F'),
                    "speed": data.get('speed', 5),
                    "angle": data.get('angle', 0.0),
                    "continuous": data.get('continuous', False)
                }
            }
            
            use_pid = data.get('use_pid', False)
            pid_precision = data.get('pid_precision', 0.1)

            if hasattr(self, 'command_generator'):
                cmd = self.command_generator.generate_command(
                    step_params, 
                    mode="manual", 
                    pid_mode=use_pid, 
                    pid_precision=pid_precision
                )
            else:
                cmd = ""

            if self.standalone:
                self._add_log(f"[模拟] 生成指令: {cmd.strip()}", "info")
                return jsonify({"success": True, "command": cmd, "message": "指令已生成"})

            try:
                if self.command_pub:
                    msg = String()
                    msg.data = cmd
                    self.command_pub.publish(msg)
                    self._add_log(f"指令已发送: {cmd.strip()}", "success")
                    return jsonify({"success": True, "command": cmd, "message": "指令已发送"})
                return jsonify({"success": False, "message": "ROS Publisher 未初始化"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/motor/stop', methods=['POST'])
        def stop_all_motors():
            """紧急停止所有电机 (R指令格式)"""
            stop_command = "XDFV0J0YDFV0J0ZDFV0J0ADFV0J0\r\n"
            if self.standalone:
                self._add_log("[模拟] 紧急停止所有电机", "warning")
                return jsonify({"success": True, "message": "已停止 (模拟模式)"})

            try:
                if self.command_pub:
                    msg = String()
                    msg.data = stop_command
                    self.command_pub.publish(msg)
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
            
            if not self.standalone and self.command_pub:
                msg = String()
                msg.data = cmd
                self.command_pub.publish(msg)
            
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
            
            if self.command_pub:
                msg = String()
                msg.data = cmd
                self.command_pub.publish(msg)
                self._add_log(f"PID 测试已启动: {motor}轴 {angle}°", "info")
                return jsonify({"success": True, "message": "测试已启动"})
            
            return jsonify({"success": False, "message": "ROS Publisher 未初始化"})

        # ================= 零点标定 API =================
        @self.app.route('/api/calibration/zero', methods=['POST'])
        def set_zero_point():
            """设置零点"""
            data = request.get_json()
            motor = data.get('motor', '')
            
            if motor not in ['X', 'Y', 'Z', 'A']:
                return jsonify({"success": False, "message": "无效的电机标识"})
            
            # 保存零点偏移
            if not hasattr(self, 'zero_offsets'):
                self.zero_offsets = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
            
            current_angle = self.current_angles.get(motor, 0.0)
            self.zero_offsets[motor] = current_angle
            
            self._add_log(f"电机 {motor} 零点已设置: {current_angle:.2f}°", "success")
            return jsonify({
                "success": True, 
                "message": f"电机 {motor} 零点已设置",
                "offset": current_angle
            })

        @self.app.route('/api/calibration/reset', methods=['POST'])
        def reset_zero_points():
            """重置所有零点"""
            self.zero_offsets = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}
            self._add_log("所有零点已重置", "warning")
            return jsonify({"success": True, "message": "所有零点已重置"})

        @self.app.route('/api/calibration/offsets', methods=['GET'])
        def get_zero_offsets():
            """获取零点偏移"""
            offsets = getattr(self, 'zero_offsets', {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0})
            return jsonify({"success": True, "data": offsets})

        # ================= 无人船遥测 API =================
        @self.app.route('/api/telemetry', methods=['GET'])
        def get_telemetry():
            """获取无人船遥测数据 (飞控状态)"""
            telemetry = getattr(self, 'usv_telemetry', {
                "connected": False,
                "armed": False,
                "mode": "UNKNOWN",
                "battery_voltage": 0.0,
                "battery_percent": 0,
                "gps_fix": 0,
                "satellites": 0,
                "latitude": 0.0,
                "longitude": 0.0,
                "heading": 0.0,
                "speed": 0.0,
                "altitude": 0.0
            })
            return jsonify({"success": True, "data": telemetry})

        @self.app.route('/api/calibration/start', methods=['POST'])
        def start_calibration():
            """启动电机校准"""
            data = request.get_json()
            motors = data.get('motors', 'XYZA')
            
            cmd = f"CAL{motors}\r\n"
            
            if self.standalone:
                self._add_log(f"[模拟] 校准: {motors}", "info")
                return jsonify({"success": True, "message": "校准已启动 (模拟模式)"})
            
            if self.command_pub:
                msg = String()
                msg.data = cmd
                self.command_pub.publish(msg)
                self._add_log(f"校准已启动: {motors}", "info")
                return jsonify({"success": True, "message": "校准已启动"})
            
            return jsonify({"success": False, "message": "ROS Publisher 未初始化"})

        # ================= 数据文件管理 API =================
        DATA_DIR = os.path.expanduser("~/usv_ws/data")

        @self.app.route('/api/records/list', methods=['GET'])
        def list_records():
            """列出所有数据文件"""
            try:
                if not os.path.exists(DATA_DIR):
                    os.makedirs(DATA_DIR)
                    return jsonify({"success": True, "data": []})

                files = []
                for fname in os.listdir(DATA_DIR):
                    fpath = os.path.join(DATA_DIR, fname)
                    if os.path.isfile(fpath) and fname.endswith(('.csv', '.json', '.log')):
                        stat = os.stat(fpath)
                        files.append({
                            "name": fname,
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                        })

                # 按修改时间降序排序
                files.sort(key=lambda x: x['modified'], reverse=True)
                return jsonify({"success": True, "data": files})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/records/file/<filename>', methods=['GET'])
        def get_record_file(filename):
            """读取指定数据文件内容"""
            try:
                fpath = os.path.join(DATA_DIR, filename)
                if not os.path.exists(fpath):
                    return jsonify({"success": False, "message": "文件不存在"}), 404

                # 安全检查：防止路径遍历
                if not os.path.abspath(fpath).startswith(os.path.abspath(DATA_DIR)):
                    return jsonify({"success": False, "message": "非法路径"}), 403

                data = []
                if filename.endswith('.csv'):
                    import csv
                    with open(fpath, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        data = list(reader)[:500]  # 限制返回行数
                elif filename.endswith('.json'):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = json.load(f)
                        data = content if isinstance(content, list) else [content]
                else:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        lines = f.readlines()[:500]
                        data = [{"line": l.strip()} for l in lines]

                return jsonify({"success": True, "data": data})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/records/file/<filename>', methods=['DELETE'])
        def delete_record_file(filename):
            """删除指定数据文件"""
            try:
                fpath = os.path.join(DATA_DIR, filename)
                if not os.path.exists(fpath):
                    return jsonify({"success": False, "message": "文件不存在"}), 404

                # 安全检查
                if not os.path.abspath(fpath).startswith(os.path.abspath(DATA_DIR)):
                    return jsonify({"success": False, "message": "非法路径"}), 403

                os.remove(fpath)
                self._add_log(f"文件已删除: {filename}", "warning")
                return jsonify({"success": True, "message": "文件已删除"})
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        @self.app.route('/api/records/download/<filename>', methods=['GET'])
        def download_record_file(filename):
            """下载数据文件"""
            try:
                fpath = os.path.join(DATA_DIR, filename)
                if not os.path.exists(fpath):
                    return jsonify({"success": False, "message": "文件不存在"}), 404

                # 安全检查
                if not os.path.abspath(fpath).startswith(os.path.abspath(DATA_DIR)):
                    return jsonify({"success": False, "message": "非法路径"}), 403

                return send_from_directory(DATA_DIR, filename, as_attachment=True)
            except Exception as e:
                return jsonify({"success": False, "message": str(e)}), 500

        # ================= WebSocket 事件 =================
        @self.socketio.on('connect')
        def handle_connect():
            emit('status', {
                "pump_connected": self.pump_connected,
                "automation_running": self.automation_running
            })
            emit('angles', self.current_angles)

    def _trigger_mission(self, action):
        """触发任务动作 - 支持独立模式本地执行。"""
        if action == 'start':
            return self._start_automation()
        elif action == 'stop':
            return self._stop_automation()
        elif action == 'pause':
            return self._pause_automation()
        elif action == 'resume':
            return self._resume_automation()
        else:
            return jsonify({"success": False, "message": f"未知动作: {action}"})

    def _start_automation(self):
        """启动自动化流程"""
        if self.automation_running:
            return jsonify({"success": False, "message": "自动化已在运行中"})

        config = self.config_manager.get()
        steps = config.get('sampling_sequence', {}).get('steps', [])
        loop_count = config.get('sampling_sequence', {}).get('loop_count', 1)
        self.auto_pid_mode = config.get('pump_settings', {}).get('pid_mode', True)
        self.auto_pid_precision = config.get('pump_settings', {}).get('pid_precision', 0.1)

        if not steps:
            return jsonify({"success": False, "message": "没有配置采样步骤"})

        self.auto_total_steps = len(steps)
        self.auto_total_loops = loop_count
        self.auto_current_step = 0
        self.auto_current_loop = 1
        self.auto_stop_event.clear()
        self.auto_pause_event.clear()
        self.automation_running = True
        self.automation_paused = False

        # 启动自动化线程
        self.auto_thread = threading.Thread(
            target=self._automation_loop,
            args=(steps, loop_count),
            daemon=True
        )
        self.auto_thread.start()

        # 重置生成器状态
        if hasattr(self, 'command_generator'):
            self.command_generator.reset_for_auto_mode()

        self._add_log(f"自动化启动: {len(steps)} 步骤, {loop_count} 循环", "success")
        self._emit_automation_status()
        return jsonify({"success": True, "message": "自动化已启动"})

    def _stop_automation(self):
        """停止自动化流程"""
        if not self.automation_running:
            return jsonify({"success": False, "message": "自动化未在运行"})

        self.auto_stop_event.set()
        self.auto_pause_event.clear()
        self.automation_running = False
        self.automation_paused = False

        # 发送停止指令
        stop_cmd = "XDFV0J0YDFV0J0ZDFV0J0ADFV0J0\r\n"
        if not self.standalone and self.command_pub:
            msg = String()
            msg.data = stop_cmd
            self.command_pub.publish(msg)

        self._add_log("自动化已停止", "warning")
        self._emit_automation_status()
        return jsonify({"success": True, "message": "自动化已停止"})

    def _pause_automation(self):
        """暂停自动化流程"""
        if not self.automation_running:
            return jsonify({"success": False, "message": "自动化未在运行"})
        if self.automation_paused:
            return jsonify({"success": False, "message": "自动化已暂停"})

        self.auto_pause_event.set()
        self.automation_paused = True
        self._add_log("自动化已暂停", "info")
        self._emit_automation_status()
        return jsonify({"success": True, "message": "自动化已暂停"})

    def _resume_automation(self):
        """恢复自动化流程"""
        if not self.automation_running:
            return jsonify({"success": False, "message": "自动化未在运行"})
        if not self.automation_paused:
            return jsonify({"success": False, "message": "自动化未暂停"})

        self.auto_pause_event.clear()
        self.automation_paused = False
        self._add_log("自动化已恢复", "info")
        self._emit_automation_status()
        return jsonify({"success": True, "message": "自动化已恢复"})

    def _automation_loop(self, steps, loop_count):
        """自动化执行主循环"""
        try:
            infinite_loop = (loop_count == 0)
            current_loop = 1

            while not self.auto_stop_event.is_set():
                if not infinite_loop and current_loop > loop_count:
                    break

                self.auto_current_loop = current_loop
                loop_str = "∞" if infinite_loop else str(loop_count)
                self._add_log(f"开始循环 {current_loop}/{loop_str}", "info")

                for step_idx, step in enumerate(steps):
                    if self.auto_stop_event.is_set():
                        break

                    # 处理暂停
                    while self.auto_pause_event.is_set() and not self.auto_stop_event.is_set():
                        time.sleep(0.1)

                    if self.auto_stop_event.is_set():
                        break

                    self.auto_current_step = step_idx + 1
                    step_name = step.get('name', f'步骤 {step_idx + 1}')
                    self._add_log(f"执行: {step_name}", "info")

                    # 生成并发送指令
                    cmd = self._generate_step_command(step)
                    if cmd:
                        if self.standalone:
                            self._add_log(f"[模拟] {cmd.strip()}", "info")
                        elif self.command_pub:
                            msg = String()
                            msg.data = cmd
                            self.command_pub.publish(msg)

                    # 更新进度
                    self._emit_automation_status()

                    # 如果启用PID模式，等待电机到位
                    if self.auto_pid_mode:
                        self._wait_for_pid_complete(step)

                    # 等待间隔时间
                    interval_ms = step.get('interval', 1000)
                    self._wait_interval(interval_ms)

                current_loop += 1

            # 完成
            self.automation_running = False
            self.automation_paused = False
            self._add_log("自动化流程完成", "success")
            self._emit_automation_status()

        except Exception as e:
            self.automation_running = False
            self._add_log(f"自动化错误: {str(e)}", "error")
            self._emit_automation_status()

    def _generate_step_command(self, step):
        """根据步骤参数生成电机控制指令 (使用 CommandGenerator)"""
        if hasattr(self, 'command_generator'):
            return self.command_generator.generate_command(
                step, 
                mode="auto", 
                pid_mode=self.auto_pid_mode, 
                pid_precision=self.auto_pid_precision
            )
        return ''

    def _wait_for_pid_complete(self, step, timeout=60.0):
        """等待PID电机到位"""
        active_motors = []
        for motor in ['X', 'Y', 'Z', 'A']:
            config = step.get(motor, {})
            if config.get('enable') == 'E' and not config.get('continuous', False):
                active_motors.append(motor)

        if not active_motors:
            return True

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.auto_stop_event.is_set():
                return False

            # 检查暂停
            while self.auto_pause_event.is_set() and not self.auto_stop_event.is_set():
                time.sleep(0.1)

            # 简化：等待固定时间让电机到位
            # 实际应检查角度误差是否小于精度阈值
            time.sleep(0.5)
            return True

        self._add_log(f"PID等待超时: {active_motors}", "warning")
        return False

    def _wait_interval(self, interval_ms):
        """高精度间隔等待"""
        interval = interval_ms / 1000.0
        if interval <= 0:
            return

        deadline = time.perf_counter() + interval
        while time.perf_counter() < deadline:
            if self.auto_stop_event.is_set():
                break

            # 处理暂停
            if self.auto_pause_event.is_set():
                pause_start = time.perf_counter()
                while self.auto_pause_event.is_set() and not self.auto_stop_event.is_set():
                    time.sleep(0.1)
                # 暂停时间不计入间隔
                deadline += time.perf_counter() - pause_start

            remaining = deadline - time.perf_counter()
            if remaining > 0.01:
                time.sleep(min(remaining * 0.75, 0.1))
            elif remaining > 0:
                time.sleep(0.001)

    def _emit_automation_status(self):
        """推送自动化状态到WebSocket"""
        if self.socketio:
            progress = 0
            if self.auto_total_steps > 0:
                progress = int((self.auto_current_step / self.auto_total_steps) * 100)

            self.socketio.emit('automation_status', {
                'running': self.automation_running,
                'paused': self.automation_paused,
                'current_step': self.auto_current_step,
                'total_steps': self.auto_total_steps,
                'current_loop': self.auto_current_loop,
                'total_loops': self.auto_total_loops,
                'progress': progress
            })

    def _publish_steps(self):
        """发布采样步骤到 ROS。"""
        if self.standalone or not self.steps_pub:
            return
            
        config = self.config_manager.get()
        steps_data = {
            "steps": config.get('sampling_sequence', {}).get('steps', []),
            "loop_count": config.get('sampling_sequence', {}).get('loop_count', 1),
            "pid_mode": config.get('pump_settings', {}).get('pid_mode', True),
            "pid_precision": config.get('pump_settings', {}).get('pid_precision', 0.1)
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
                    "automation_running": self.automation_running
                })
                self.socketio.emit('angles', self.current_angles)
            
            if self.standalone:
                # 独立模式模拟数据变化 (测试用)
                if self.automation_running:
                    for k in self.current_angles:
                        self.current_angles[k] = (self.current_angles[k] + 1) % 360
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
            self.socketio.run(self.app, host=self.host, port=self.port, use_reloader=False)
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
