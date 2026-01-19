#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web Configuration Server (Web 配置服务器)
==========================================
提供 HTTP 接口用于配置自动化采样参数。

功能:
  - 提供 Web UI 配置采样参数
  - 保存/加载配置文件 (JSON)
  - 提供 REST API 供其他节点调用
  - 实时状态查询

Target: Jetson Nano
Python: 3.8

访问地址: http://10.42.0.1:5000 (Nano 热点 IP)

依赖:
  pip3 install flask flask-cors

运行模式:
  1. ROS 模式: rosrun usv_ros web_config_server.py (需要 roscore)
  2. 独立模式: python3 web_config_server.py (不需要 roscore)
"""

from __future__ import print_function

import json
import os
import sys
import threading
from datetime import datetime

try:
    from flask import Flask, request, jsonify, render_template_string
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("警告: Flask 未安装，请运行: pip3 install flask flask-cors")

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
        def init_node(self, *args, **kwargs):
            pass
        def get_param(self, name, default):
            return default
        def loginfo(self, msg, *args):
            print("[INFO]", msg % args if args else msg)
        def logwarn(self, msg, *args):
            print("[WARN]", msg % args if args else msg)
        def logerr(self, msg, *args):
            print("[ERROR]", msg % args if args else msg)
        def Subscriber(self, *args, **kwargs):
            return None
        def Publisher(self, *args, **kwargs):
            class MockPub:
                def publish(self, msg):
                    pass
            return MockPub()
        def ServiceProxy(self, *args, **kwargs):
            return None
        def wait_for_service(self, *args, **kwargs):
            raise Exception("ROS not available")
        def ROSException(self):
            return Exception
    rospy = MockRospy()
    
    class String:
        def __init__(self):
            self.data = ""
    
    class Trigger:
        pass

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

# HTML 模板 - 配置页面
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>USV 水质监测系统 - 配置</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5; 
            padding: 20px;
            max-width: 900px;
            margin: 0 auto;
        }
        h1 { color: #333; margin-bottom: 20px; text-align: center; }
        h2 { color: #555; margin: 20px 0 10px; border-bottom: 2px solid #007bff; padding-bottom: 5px; }
        .card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: 500; color: #333; }
        input, select, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        input:focus, select:focus { border-color: #007bff; outline: none; }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            margin-right: 10px;
            margin-top: 10px;
        }
        .btn-primary { background: #007bff; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-warning { background: #ffc107; color: #333; }
        .btn:hover { opacity: 0.9; }
        .status { padding: 10px; border-radius: 4px; margin-top: 10px; }
        .status-success { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .status-info { background: #cce5ff; color: #004085; }
        .step-item {
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            padding: 15px;
            margin-bottom: 10px;
        }
        .step-header { display: flex; justify-content: space-between; align-items: center; }
        .motor-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 10px; }
        .motor-item { background: white; padding: 10px; border-radius: 4px; }
        .motor-item label { font-size: 12px; }
        .motor-item input, .motor-item select { padding: 5px; font-size: 12px; }
        #log-output {
            background: #1e1e1e;
            color: #0f0;
            font-family: monospace;
            padding: 10px;
            height: 200px;
            overflow-y: auto;
            border-radius: 4px;
        }
        .inline-group { display: flex; gap: 10px; }
        .inline-group > div { flex: 1; }
    </style>
</head>
<body>
    <h1>🚤 USV 水质监测系统</h1>
    
    <div class="card">
        <h2>📊 系统状态</h2>
        <div id="system-status" class="status status-info">正在获取状态...</div>
        <div style="margin-top: 15px;">
            <button class="btn btn-success" onclick="startMission()">▶ 启动采样</button>
            <button class="btn btn-danger" onclick="stopMission()">⏹ 停止</button>
            <button class="btn btn-warning" onclick="pauseMission()">⏸ 暂停</button>
            <button class="btn btn-primary" onclick="resumeMission()">⏵ 恢复</button>
        </div>
    </div>

    <div class="card">
        <h2>⚙️ 任务配置</h2>
        <div class="form-group">
            <label>任务名称</label>
            <input type="text" id="mission-name" value="默认采样任务">
        </div>
        <div class="inline-group">
            <div class="form-group">
                <label>循环次数 (0=无限)</label>
                <input type="number" id="loop-count" value="1" min="0">
            </div>
            <div class="form-group">
                <label>PID 精度 (度)</label>
                <input type="number" id="pid-precision" value="0.1" step="0.01" min="0.01">
            </div>
        </div>
        <div class="form-group">
            <label><input type="checkbox" id="pid-mode" checked> 启用 PID 闭环模式</label>
        </div>
    </div>

    <div class="card">
        <h2>📋 采样步骤</h2>
        <div id="steps-container"></div>
        <button class="btn btn-primary" onclick="addStep()">+ 添加步骤</button>
    </div>

    <div class="card">
        <button class="btn btn-success" onclick="saveConfig()">💾 保存配置</button>
        <button class="btn btn-primary" onclick="loadConfig()">📂 加载配置</button>
        <button class="btn btn-warning" onclick="resetConfig()">🔄 重置默认</button>
        <div id="save-status"></div>
    </div>

    <div class="card">
        <h2>📜 实时日志</h2>
        <div id="log-output"></div>
    </div>

    <script>
        const API_BASE = '';
        let steps = [];

        // 初始化
        document.addEventListener('DOMContentLoaded', () => {
            loadConfig();
            setInterval(updateStatus, 2000);
            setInterval(updateLog, 1000);
        });

        function updateStatus() {
            fetch(API_BASE + '/api/status')
                .then(r => r.json())
                .then(data => {
                    const el = document.getElementById('system-status');
                    el.className = 'status status-' + (data.automation_running ? 'success' : 'info');
                    el.innerHTML = `
                        <b>泵控制:</b> ${data.pump_connected ? '已连接' : '未连接'} | 
                        <b>自动化:</b> ${data.automation_running ? '运行中' : '停止'} | 
                        <b>角度:</b> X:${data.angles.X.toFixed(1)}° Y:${data.angles.Y.toFixed(1)}° Z:${data.angles.Z.toFixed(1)}° A:${data.angles.A.toFixed(1)}°
                    `;
                })
                .catch(() => {
                    document.getElementById('system-status').innerHTML = '无法连接到服务器';
                    document.getElementById('system-status').className = 'status status-error';
                });
        }

        function updateLog() {
            fetch(API_BASE + '/api/log')
                .then(r => r.json())
                .then(data => {
                    const el = document.getElementById('log-output');
                    el.innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
                    el.scrollTop = el.scrollHeight;
                })
                .catch(() => {});
        }

        function loadConfig() {
            fetch(API_BASE + '/api/config')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('mission-name').value = data.mission.name || '';
                    document.getElementById('loop-count').value = data.sampling_sequence.loop_count || 1;
                    document.getElementById('pid-precision').value = data.pump_settings.pid_precision || 0.1;
                    document.getElementById('pid-mode').checked = data.pump_settings.pid_mode !== false;
                    steps = data.sampling_sequence.steps || [];
                    renderSteps();
                    showStatus('save-status', '配置已加载', 'info');
                })
                .catch(e => showStatus('save-status', '加载失败: ' + e, 'error'));
        }

        function saveConfig() {
            const config = {
                mission: { name: document.getElementById('mission-name').value },
                pump_settings: {
                    pid_mode: document.getElementById('pid-mode').checked,
                    pid_precision: parseFloat(document.getElementById('pid-precision').value)
                },
                sampling_sequence: {
                    loop_count: parseInt(document.getElementById('loop-count').value),
                    steps: collectSteps()
                }
            };
            fetch(API_BASE + '/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            })
            .then(r => r.json())
            .then(data => showStatus('save-status', data.message, 'success'))
            .catch(e => showStatus('save-status', '保存失败: ' + e, 'error'));
        }

        function resetConfig() {
            if (confirm('确定要重置为默认配置吗？')) {
                fetch(API_BASE + '/api/config/reset', {method: 'POST'})
                    .then(() => loadConfig())
                    .catch(e => showStatus('save-status', '重置失败: ' + e, 'error'));
            }
        }

        function startMission() {
            fetch(API_BASE + '/api/mission/start', {method: 'POST'})
                .then(r => r.json())
                .then(data => showStatus('save-status', data.message, data.success ? 'success' : 'error'));
        }

        function stopMission() {
            fetch(API_BASE + '/api/mission/stop', {method: 'POST'})
                .then(r => r.json())
                .then(data => showStatus('save-status', data.message, 'info'));
        }

        function pauseMission() {
            fetch(API_BASE + '/api/mission/pause', {method: 'POST'})
                .then(r => r.json())
                .then(data => showStatus('save-status', data.message, 'info'));
        }

        function resumeMission() {
            fetch(API_BASE + '/api/mission/resume', {method: 'POST'})
                .then(r => r.json())
                .then(data => showStatus('save-status', data.message, 'info'));
        }

        function renderSteps() {
            const container = document.getElementById('steps-container');
            container.innerHTML = steps.map((step, i) => `
                <div class="step-item">
                    <div class="step-header">
                        <input type="text" value="${step.name || '步骤'+(i+1)}" 
                               onchange="steps[${i}].name=this.value" style="width:200px">
                        <div>
                            <label>间隔(ms): <input type="number" value="${step.interval||1000}" 
                                   onchange="steps[${i}].interval=parseInt(this.value)" style="width:80px"></label>
                            <button class="btn btn-danger" onclick="removeStep(${i})" style="padding:5px 10px">删除</button>
                        </div>
                    </div>
                    <div class="motor-grid">
                        ${['X','Y','Z','A'].map(m => `
                            <div class="motor-item">
                                <label><b>${m}轴</b></label>
                                <label><input type="checkbox" ${step[m]?.enable==='E'?'checked':''} 
                                       onchange="steps[${i}]['${m}'].enable=this.checked?'E':'D'"> 启用</label>
                                <label>方向: <select onchange="steps[${i}]['${m}'].direction=this.value">
                                    <option value="F" ${step[m]?.direction==='F'?'selected':''}>正转</option>
                                    <option value="B" ${step[m]?.direction==='B'?'selected':''}>反转</option>
                                </select></label>
                                <label>速度: <input type="number" value="${step[m]?.speed||5}" min="1" max="10"
                                       onchange="steps[${i}]['${m}'].speed=this.value" style="width:50px"></label>
                                <label>角度: <input type="number" value="${step[m]?.angle||90}" 
                                       onchange="steps[${i}]['${m}'].angle=this.value" style="width:60px"></label>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }

        function addStep() {
            steps.push({
                name: '新步骤',
                X: {enable:'D', direction:'F', speed:'5', angle:'90'},
                Y: {enable:'D', direction:'F', speed:'5', angle:'90'},
                Z: {enable:'D', direction:'F', speed:'5', angle:'90'},
                A: {enable:'D', direction:'F', speed:'5', angle:'90'},
                interval: 1000
            });
            renderSteps();
        }

        function removeStep(i) {
            steps.splice(i, 1);
            renderSteps();
        }

        function collectSteps() {
            return steps;
        }

        function showStatus(id, msg, type) {
            const el = document.getElementById(id);
            el.className = 'status status-' + type;
            el.textContent = msg;
            setTimeout(() => el.textContent = '', 5000);
        }
    </script>
</body>
</html>
'''


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
                    # 合并默认配置
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

    def get_steps(self):
        """获取采样步骤。"""
        return self.config.get('sampling_sequence', {}).get('steps', [])

    def get_loop_count(self):
        """获取循环次数。"""
        return self.config.get('sampling_sequence', {}).get('loop_count', 1)

    def get_pid_settings(self):
        """获取 PID 设置。"""
        return self.config.get('pump_settings', {})


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

        # 配置管理器
        self.config_manager = ConfigManager()
        self.config_manager.load()

        # 日志缓冲
        self.log_buffer = []
        self.log_lock = threading.Lock()
        self.max_logs = 100

        # 状态缓存
        self.pump_connected = False
        self.automation_running = False
        self.current_angles = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}

        # ROS 订阅 (仅在非独立模式)
        if not self.standalone:
            self.status_sub = rospy.Subscriber('/usv/pump_status', String, self._status_cb)
            self.angles_sub = rospy.Subscriber('/usv/pump_angles', String, self._angles_cb)
            self.steps_pub = rospy.Publisher('/usv/automation_steps', String, queue_size=1)
        else:
            self.status_sub = None
            self.angles_sub = None
            self.steps_pub = None

        # Flask 应用
        self.app = None
        self.server_thread = None

        if FLASK_AVAILABLE:
            self._setup_flask()
        else:
            rospy.logerr("Flask not available! Install with: pip3 install flask flask-cors")

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
        self._add_log("[状态] " + msg.data)

    def _angles_cb(self, msg):
        """角度数据回调。"""
        try:
            for pair in msg.data.split(','):
                if ':' in pair:
                    key, val = pair.split(':')
                    if key in self.current_angles:
                        self.current_angles[key] = float(val)
        except Exception:
            pass

    def _add_log(self, message):
        """添加日志。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self.log_lock:
            self.log_buffer.append("[{}] {}".format(timestamp, message))
            if len(self.log_buffer) > self.max_logs:
                self.log_buffer = self.log_buffer[-self.max_logs:]

    def _setup_flask(self):
        """设置 Flask 应用。"""
        self.app = Flask(__name__)
        CORS(self.app)

        @self.app.route('/')
        def index():
            return render_template_string(HTML_TEMPLATE)

        @self.app.route('/api/config', methods=['GET'])
        def get_config():
            return jsonify(self.config_manager.get())

        @self.app.route('/api/config', methods=['POST'])
        def save_config():
            data = request.get_json()
            if self.config_manager.update(data):
                self._add_log("[配置] 配置已保存")
                return jsonify({"success": True, "message": "配置已保存"})
            return jsonify({"success": False, "message": "保存失败"}), 500

        @self.app.route('/api/config/reset', methods=['POST'])
        def reset_config():
            self.config_manager.reset()
            self._add_log("[配置] 配置已重置")
            return jsonify({"success": True, "message": "已重置为默认配置"})

        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            return jsonify({
                "pump_connected": self.pump_connected,
                "automation_running": self.automation_running,
                "angles": self.current_angles
            })

        @self.app.route('/api/log', methods=['GET'])
        def get_log():
            with self.log_lock:
                return jsonify({"logs": self.log_buffer[-50:]})

        @self.app.route('/api/mission/start', methods=['POST'])
        def start_mission():
            return self._trigger_mission('start')

        @self.app.route('/api/mission/stop', methods=['POST'])
        def stop_mission():
            return self._trigger_mission('stop')

        @self.app.route('/api/mission/pause', methods=['POST'])
        def pause_mission():
            return self._trigger_mission('pause')

        @self.app.route('/api/mission/resume', methods=['POST'])
        def resume_mission():
            return self._trigger_mission('resume')

    def _trigger_mission(self, action):
        """触发任务动作。"""
        # 独立模式下无法触发 ROS 服务
        if self.standalone:
            msg = "独立模式下无法触发任务 (需要 ROS 集成)"
            self._add_log("[警告] " + msg)
            return jsonify({"success": False, "message": msg})
        
        service_map = {
            'start': '/usv/automation_start',
            'stop': '/usv/automation_stop',
            'pause': '/usv/automation_pause',
            'resume': '/usv/automation_resume'
        }

        service_name = service_map.get(action)
        if not service_name:
            return jsonify({"success": False, "message": "未知动作"})

        try:
            # 如果是启动，先发送最新配置
            if action == 'start':
                self._publish_steps()

            # 调用服务
            rospy.wait_for_service(service_name, timeout=2.0)
            service = rospy.ServiceProxy(service_name, Trigger)
            resp = service()

            self._add_log("[任务] {} - {}".format(action, resp.message))
            return jsonify({"success": resp.success, "message": resp.message})

        except Exception as e:
            msg = "服务不可用: {}".format(str(e))
            self._add_log("[错误] " + msg)
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
            "pid_precision": config.get('pump_settings', {}).get('pid_precision', 0.1)
        }
        msg = String()
        msg.data = json.dumps(steps_data)
        self.steps_pub.publish(msg)
        self._add_log("[配置] 步骤已发送到泵控制节点")

    def run(self):
        """运行服务器。"""
        if not FLASK_AVAILABLE:
            rospy.logerr("Flask not available, cannot start web server")
            if not self.standalone:
                rospy.spin()
            return

        rospy.loginfo("Web server starting at http://%s:%d", self.host, self.port)
        self._add_log("Web 服务器启动中...")

        if self.standalone:
            # 独立模式：直接运行 Flask (阻塞)
            rospy.loginfo("Running in standalone mode (blocking)")
            try:
                self.app.run(host=self.host, port=self.port, threaded=True, use_reloader=False)
            except KeyboardInterrupt:
                rospy.loginfo("Server stopped by user")
        else:
            # ROS 模式：在后台线程运行 Flask
            def run_flask():
                self.app.run(host=self.host, port=self.port, threaded=True, use_reloader=False)

            self.server_thread = threading.Thread(target=run_flask, daemon=True)
            self.server_thread.start()

            rospy.loginfo("Web server started (background thread)")
            self._add_log("Web 服务器已启动")

            # ROS 主循环
            try:
                rospy.spin()
            except KeyboardInterrupt:
                rospy.loginfo("ROS node stopped")


def main():
    """主函数 - 自动检测运行模式。"""
    # 检测是否有 ROS 环境
    standalone = not ROS_AVAILABLE
    
    if not standalone:
        # 尝试连接 ROS master
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            # 尝试连接默认的 ROS master 端口
            result = sock.connect_ex(('localhost', 11311))
            sock.close()
            if result != 0:
                print("警告: 无法连接到 ROS master (localhost:11311)")
                print("  切换到独立模式运行")
                print("  如需 ROS 集成，请先运行: roscore")
                standalone = True
        except Exception:
            standalone = True
    
    try:
        mode_str = "独立模式" if standalone else "ROS 模式"
        print("=" * 50)
        print("USV Web 配置服务器")
        print("运行模式: {}".format(mode_str))
        print("=" * 50)
        
        server = WebConfigServer(standalone=standalone)
        server.run()
    except KeyboardInterrupt:
        print("\n服务器已停止")
    except Exception as e:
        if ROS_AVAILABLE and not standalone:
            rospy.logerr("Web Config Server error: %s", str(e))
        else:
            print("错误:", str(e))
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
