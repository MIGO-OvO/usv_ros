# USV ROS Package - 水质监测无人船系统

基于 ROS Noetic 的水质监测无人船载荷控制系统，运行于 Jetson Nano。

## 目录

- [系统概述](#系统概述)
- [系统架构](#系统架构)
- [节点说明](#节点说明)
- [与遗留代码对比](#与遗留代码对比)
- [已实现功能](#已实现功能)
- [未实现功能](#未实现功能)
- [使用方法](#使用方法)

---

## 系统概述

### 这是什么？

这是一个**基于液滴微流控技术的微型自动化化学分析实验室 (Lab-on-a-Chip)** 的控制系统，而非简单的浸入式探头传感器。

### 核心硬件

| 组件 | 说明 |
|------|------|
| **蠕动泵 (X/Y/Z/A)** | 4 个步进电机驱动的微型蠕动泵，带 MT6701 磁编码器闭环控制 |
| **ESP32 控制板** | 下位机，接收串口指令控制电机，返回角度反馈 |
| **NI DAQ 采集卡** | 采集分光光度检测器的电压信号 |
| **分光检测器** | L型流道光学检测单元，LED + 光电二极管 |
| **Pixhawk 6C** | 无人船飞控，通过 MAVROS 与 Jetson Nano 通信 |

### 工作流程

```
1. 航行 (Pixhawk 控制) 
   ↓
2. 到达采样点 → Nano 监听航点到达事件
   ↓
3. 切换 HOLD 模式 → 无人船悬停
   ↓
4. 采样 → Nano 控制蠕动泵抽取水样、注入试剂
   ↓
5. 检测 → DAQ 采集分光检测器电压，计算吸光度
   ↓
6. 恢复 AUTO 模式 → 继续航行到下一航点
```

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Jetson Nano                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ pump_control    │  │ spectrometer    │  │ mission_        │  │
│  │ _node           │  │ _node           │  │ coordinator     │  │
│  │                 │  │                 │  │ _node           │  │
│  │ 泵控制          │  │ 分光检测        │  │ 任务协调        │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │           │
└───────────┼────────────────────┼────────────────────┼───────────┘
            │                    │                    │
            │ USB/UART           │ USB                │ UART
            ▼                    ▼                    ▼
     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
     │   ESP32      │     │   NI DAQ     │     │  Pixhawk 6C  │
     │ 电机控制板   │     │   采集卡     │     │   飞控       │
     └──────┬───────┘     └──────┬───────┘     └──────────────┘
            │                    │
            ▼                    ▼
     ┌──────────────┐     ┌──────────────┐
     │ 蠕动泵 x4    │     │ 分光检测器   │
     │ (X/Y/Z/A)    │     │ (光电二极管) │
     └──────────────┘     └──────────────┘
```

### ROS 话题拓扑

```
/usv/pump_command ──────────► pump_control_node ──────► /usv/pump_angles
     (String)                       │                    (String)
                                    │
                                    └──────────────────► /usv/pump_status
                                                         (String)

spectrometer_node ──────────────────────────────────────► /usv/spectrometer_voltage
                                                          (Float64)

/mavros/state ──────────────► mission_coordinator ──────► /usv/mission_status
/mavros/mission/reached ────►      _node          ──────► /usv/detection_result
                                    │
                                    └──────────────────► /mavros/set_mode (Service)
```

---

## 节点说明

### 1. pump_control_node (泵控制节点)

**功能**: 通过串口与 ESP32 通信，控制 4 轴蠕动泵，支持 PID 闭环精确定位

**文件**: `scripts/pump_control_node.py`

**依赖库**: `scripts/lib/command_generator.py`, `scripts/lib/automation_engine.py`

#### 通信协议

**发送指令** (到 ESP32):
```
# PID 闭环模式 (R 指令) - 推荐
{motor}E{dir}R{angle}P{precision}\r\n
示例: XEFR90.0P0.1  -> X轴正转90°，精度0.1°

# 传统开环模式 (J 指令)
{motor}E{dir}V{speed}J{angle}\r\n
示例: XEFV5J90.0   -> X轴正转90°，速度5RPM

# 连续转动模式
{motor}E{dir}V{speed}JG\r\n
示例: XEFV5JG      -> X轴连续正转

# 停止
{motor}DFV0J0\r\n
示例: XDFV0J0      -> X轴停止

# PID 配置
PIDCFG:{Kp},{Ki},{Kd},{OutMin},{OutMax}\r\n
示例: PIDCFG:0.14,0.015,0.06,1.0,8.0

# PID 停止
PIDSTOP\r\n
```

**接收数据** (从 ESP32):
```
角度数据包: [0x55][0xCC][X:4B][Y:4B][Z:4B][A:4B][checksum][0x0A]
- 4 个 float (little-endian) 表示四轴实时角度
```

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `~serial_port` | string | /dev/ttyUSB0 | ESP32 串口 |
| `~baudrate` | int | 115200 | 波特率 |
| `~timeout` | float | 1.0 | 超时 (秒) |
| `~pid_mode` | bool | true | 启用 PID 闭环模式 |
| `~pid_precision` | float | 0.1 | PID 定位精度 (度) |

#### 话题与服务

| 名称 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/usv/pump_command` | String | Sub | 直接指令 |
| `/usv/pump_step` | String | Sub | 单步骤 JSON |
| `/usv/automation_steps` | String | Sub | 自动化步骤列表 JSON |
| `/usv/pump_angles` | String | Pub | 实时角度 |
| `/usv/pump_status` | String | Pub | 状态信息 |
| `/usv/pump_pid_complete` | String | Pub | PID 完成通知 |
| `/usv/pump_stop` | Trigger | Srv | 紧急停止 |
| `/usv/automation_start` | Trigger | Srv | 启动自动化 |
| `/usv/automation_stop` | Trigger | Srv | 停止自动化 |
| `/usv/automation_pause` | Trigger | Srv | 暂停自动化 |
| `/usv/automation_resume` | Trigger | Srv | 恢复自动化 |

---

### 2. spectrometer_node (分光检测器节点)

**功能**: 通过 NI DAQmx 采集分光光度检测器电压

**文件**: `scripts/spectrometer_node.py`

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `~device_name` | string | Dev1 | DAQ 设备名 |
| `~channel` | string | ai0 | 模拟输入通道 |
| `~sample_rate` | int | 100 | 采样率 (Hz) |
| `~auto_start` | bool | false | 自动开始采集 |

#### 话题与服务

| 名称 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/usv/spectrometer_voltage` | Float64 | Pub | 实时电压 |
| `/usv/spectrometer_status` | String | Pub | 设备状态 |
| `/usv/spectrometer_start` | Trigger | Srv | 开始采集 |
| `/usv/spectrometer_stop` | Trigger | Srv | 停止采集 |

#### 注意事项

- Jetson Nano 需安装 NI-DAQmx Linux 驱动
- 如果 `nidaqmx` 库不可用，节点将以模拟模式运行

---

### 3. mission_coordinator_node (任务协调节点)

**功能**: 协调无人船航行与水质检测任务

**文件**: `scripts/mission_coordinator_node.py`

#### 状态机

```
IDLE → NAVIGATING → HOLDING → SAMPLING → DETECTING → COMPLETED
                                                         ↓
                                                    NAVIGATING (循环)
```

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `~mavros_timeout` | float | 30.0 | MAVROS 连接超时 |
| `~sampling_duration` | float | 10.0 | 采样时长 (秒) |
| `~detection_duration` | float | 5.0 | 检测时长 (秒) |

#### 话题与服务

| 名称 | 类型 | 方向 | 说明 |
|------|------|------|------|
| `/mavros/state` | State | Sub | 飞控状态 |
| `/mavros/mission/reached` | WaypointReached | Sub | 航点到达 |
| `/usv/mission_status` | String | Pub | 任务状态 |
| `/usv/detection_result` | String | Pub | 检测结果 |

---

## 与遗留代码对比

### 架构对比

| 方面 | 遗留代码 (MotorControlApp_Pyside6) | 新 ROS 节点 |
|------|-----------------------------------|-------------|
| **定位** | Windows 桌面上位机 | Jetson Nano 嵌入式节点 |
| **框架** | PySide6 GUI | ROS Noetic (无头) |
| **Python** | 3.11+ | 3.8 (严格) |
| **线程** | QThread + Qt Signals | threading + rospy |
| **通信** | 直接串口 | ROS 话题/服务 |
| **船控制** | 无 | MAVROS 集成 |

### 代码映射

| 遗留模块 | 新实现 | 状态 |
|----------|--------|------|
| `serial_manager.py` | `PumpControlNode` | ✅ 核心功能已迁移 |
| `serial_reader.py` | `PumpSerialReader` | ✅ 全部二进制协议已迁移 |
| `command_generator.py` | `lib/command_generator.py` | ✅ **完整迁移 (含 R 指令)** |
| `automation_engine.py` | `lib/automation_engine.py` | ✅ **完整迁移** |
| `daq_thread.py` | `DAQReader` | ✅ 已迁移 |
| `pid_optimizer.py` | - | ❌ 未实现 |
| `preset_manager.py` | - | ❌ 未实现 |
| `src/ui/*` | - | ❌ 已移除 (无头模式) |

### 关键变更

#### 1. 串口通信

**遗留代码** (Qt 信号):
```python
class SerialManager(QObject):
    data_received = Signal(str)
    
    def send_command(self, cmd):
        self.serial_port.write(cmd.encode())
```

**新实现** (ROS 话题):
```python
class PumpControlNode:
    def __init__(self):
        self.cmd_sub = rospy.Subscriber('/usv/pump_command', String, self._cmd_callback)
        self.angles_pub = rospy.Publisher('/usv/pump_angles', String, queue_size=10)
```

#### 2. DAQ 采集

**遗留代码** (QThread):
```python
class DAQThread(QThread):
    data_acquired = Signal(float)
```

**新实现** (threading + ROS):
```python
class DAQReader:
    def start(self, callback):
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
```

#### 3. 任务协调 (新增)

遗留代码无此功能，新增与 MAVROS/Pixhawk 的集成：
```python
class MissionCoordinatorNode:
    def _waypoint_reached_cb(self, msg):
        # 航点到达 → 触发采样流程
        self._start_sampling_sequence()
```

---

## 已实现功能

### ✅ 泵控制节点

| 功能 | 说明 | 对应遗留代码 |
|------|------|--------------|
| 串口连接管理 | 连接/断开 ESP32 | `serial_manager.py` |
| 指令发送 | 电机控制指令 | `serial_manager.send_command()` |
| 角度数据解析 | 二进制协议 [0x55][0xCC] | `serial_reader.py` |
| 紧急停止 | 停止所有泵 | `command_generator.generate_stop_command()` |
| ROS 接口 | 话题/服务 | 新增 |

### ✅ 分光检测器节点

| 功能 | 说明 | 对应遗留代码 |
|------|------|--------------|
| DAQ 采集 | NI DAQmx 电压读取 | `daq_thread.py` |
| 采样率控制 | 可配置 Hz | `daq_thread.py` |
| 模拟模式 | 无 DAQ 时模拟数据 | 新增 |
| 服务控制 | 开始/停止采集 | 新增 |

### ✅ 任务协调节点

| 功能 | 说明 | 对应遗留代码 |
|------|------|--------------|
| MAVROS 连接 | 等待飞控连接 | 新增 |
| 航点监听 | 到达事件触发 | 新增 |
| 模式切换 | HOLD/AUTO | 新增 |
| 采样序列 | 泵控制 + 检测 | `automation_engine.py` (简化) |
| 结果发布 | 检测数据汇总 | 新增 |

---

## 已实现的高级功能

### ✅ 完整指令生成器 (lib/command_generator.py)

从遗留代码完整迁移的功能：
- 多电机协调控制 (X/Y/Z/A)
- **PID 闭环精确定位模式 (R 指令)**
- 传统开环模式 (J 指令)
- 连续转动模式 (JG 指令)
- 自动模式角度累积
- 校准补偿
- 理论偏差修正

**指令格式**:
```
# PID 闭环模式 (推荐)
XEFR90.0P0.1    -> X轴正转90°，精度0.1°

# 传统开环模式
XEFV5J90.000    -> X轴正转90°，速度5RPM

# 连续转动
XEFV5JG         -> X轴连续正转

# PID 配置
PIDCFG:0.14,0.015,0.06,1.0,8.0
```

### ✅ 自动化引擎 (lib/automation_engine.py)

从遗留代码完整迁移的功能：
- 多步骤序列执行
- 循环执行 (有限/无限)
- 暂停/恢复
- **PID 完成等待机制**
- 高精度时间间隔控制
- 状态回调和进度更新

**ROS 服务接口**:
```bash
rosservice call /usv/automation_start   # 启动
rosservice call /usv/automation_stop    # 停止
rosservice call /usv/automation_pause   # 暂停
rosservice call /usv/automation_resume  # 恢复
```

---

## 未实现功能

### ❌ PID 控制与优化

| 遗留模块 | 功能 |
|----------|------|
| `pid_analyzer.py` | PID 数据采集与分析 |
| `pid_optimizer.py` | 贝叶斯 PID 参数优化 |
| `pid_history_manager.py` | 历史记录管理 |

**当前状态**: 依赖 ESP32 下位机内置 PID

### ❌ 预设管理 (preset_manager.py)

- 保存/加载运动预设
- JSON 持久化

**建议方案**: ROS 参数服务器 + YAML

### ❌ 其他二进制数据包

| 包类型 | Header | 状态 |
|--------|--------|------|
| PID 数据包 | 0x55 0xAA | ✅ 已实现 |
| PID 测试结果 | 0x55 0xBB | ✅ 已实现 |
| 角度数据包 | 0x55 0xCC | ✅ 已实现 |

---

## 使用方法

### 编译

```bash
cd ~/usv_ws
catkin_make
source devel/setup.bash
```

### 安装依赖

```bash
# Flask Web 服务器
pip3 install flask flask-cors

# 串口通信
pip3 install pyserial
```

### 配置 Wi-Fi 热点 (野外使用)

**注意**: 如果 wlan0 已连接到现有网络 (如 10.33.106.36)，需要先断开才能创建热点。

```bash
# 方案1: 创建开放热点 (无密码，推荐野外使用)
sudo ./src/usv_ros/scripts/setup_hotspot.sh USV_Control

# 方案2: 如果需要密码保护
sudo nmcli dev wifi hotspot ifname wlan0 con-name USV_AP ssid "USV_Control" password "usv12345"

# 设置开机自启
sudo nmcli con mod USV_AP connection.autoconnect yes

# 如果热点创建失败，先断开当前 Wi-Fi
sudo nmcli dev disconnect wlan0
# 然后重新运行上述命令
```

**网络模式说明**:
- **开发模式**: wlan0 连接到实验室网络 (10.33.106.36)，通过 SSH 访问
- **野外模式**: wlan0 作为热点 (10.42.0.1)，笔记本直接连接

### 启动系统

```bash
# 完整启动
roslaunch usv_ros usv_bringup.launch

# 仅启动泵控制和 Web 服务器 (调试用)
roslaunch usv_ros usv_bringup.launch enable_spectrometer:=false enable_mavlink_trigger:=false
```

---

## 开发调试 (SSH 访问 Web 配置)

在开发阶段，Nano 通过 wlan0 连接到实验室网络 (IP: 10.33.106.36)，可以通过以下方式访问 Web 配置界面：

### 快速测试步骤

```bash
# 1. SSH 连接到 Nano
ssh jetson@10.33.106.36

# 2. 运行诊断脚本
cd ~/usv_ws
./src/usv_ros/scripts/test_web_server.sh

# 3. 如果服务未运行，启动系统
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch

# 4. 在开发电脑浏览器访问
# http://10.33.106.36:5000
```

> **详细测试指南**: 参见 [TESTING.md](TESTING.md)

### 方法1: 直接访问 Nano IP (推荐)

```bash
# 在浏览器直接访问:
http://10.33.106.36:5000

# SSH 访问:
ssh jetson@10.33.106.36

# 测试连接 (在 Nano 或开发电脑上):
curl http://10.33.106.36:5000
```

### 方法2: SSH 端口转发

```bash
# 在开发电脑上执行
ssh -L 5000:localhost:5000 jetson@10.33.106.36

# 然后在浏览器访问:
http://localhost:5000
```

### 方法3: VS Code Remote SSH

1. 安装 VS Code Remote - SSH 扩展
2. 连接到 Nano: `ssh jetson@10.33.106.36`
3. 在 VS Code 中打开端口转发 (Ports 面板)
4. 转发端口 5000
5. 浏览器访问 `http://localhost:5000`

### 常见问题

**问题: 浏览器显示"无法访问"**

```bash
# 1. 检查 Web 服务器是否运行
ssh jetson@10.33.106.36
ps aux | grep web_config_server

# 2. 检查端口是否监听
netstat -tuln | grep 5000

# 3. 测试本地访问
curl http://localhost:5000

# 4. 如果都失败，重新启动
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

**问题: Flask 未安装**

```bash
pip3 install flask flask-cors
```

> **注意**: Web 服务器绑定在 `0.0.0.0:5000`，监听所有网络接口。无论 Nano 是作为热点 (10.42.0.1) 还是连接到其他网络 (10.33.106.36)，都可以访问。

---

## 野外作业工作流

### 阶段一：岸边准备

1. **上电启动**: USV 上电，Nano 自动启动 Wi-Fi 热点和后台服务
2. **连接热点**: 笔记本连接 Wi-Fi `USV_Control` (无密码，开放网络)
3. **Web 配置**: 
   - 打开浏览器访问 `http://10.42.0.1:5000`
   - 配置采样参数 (泵速、角度、间隔时间)
   - 点击 **保存配置**
4. **SSH 检查** (可选):
   ```bash
   ssh jetson@10.42.0.1
   rostopic echo /usv/pump_angles
   ```

### 阶段二：下水任务

1. **下水**: 将 USV 放入水中 (Wi-Fi 可能断开，不影响控制)
2. **QGC 导航**: 通过数传电台上传航点任务
3. **到达采样点**: 
   - 自动触发: 到达航点自动开始采样
   - 手动触发: 在 QGC 发送 MAVLink 指令 31010
4. **采样执行**:
   - Nano 读取保存的配置
   - 切换 HOLD 模式
   - 执行采样序列
   - 恢复 AUTO 模式继续航行

### MAVLink 指令

| 指令 ID | 功能 |
|---------|------|
| 31010 | 开始采样 |
| 31011 | 停止采样 |
| 31012 | 暂停 |
| 31013 | 恢复 |

---

## 测试命令

**完整启动**:
```bash
roslaunch usv_ros usv_bringup.launch
```

**带参数启动**:
```bash
roslaunch usv_ros usv_bringup.launch \
    pump_port:=/dev/ttyUSB0 \
    pump_baudrate:=115200 \
    daq_device:=Dev1 \
    daq_channel:=ai0
```

**单独启动节点**:
```bash
# 泵控制
rosrun usv_ros pump_control_node.py _serial_port:=/dev/ttyUSB0

# 分光检测器
rosrun usv_ros spectrometer_node.py _device_name:=Dev1 _channel:=ai0

# 任务协调 (需要 MAVROS 运行)
rosrun usv_ros mission_coordinator_node.py
```

## 测试命令

```bash
# 发送泵控制指令 (PID 模式)
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'"

# 停止所有泵
rosservice call /usv/pump_stop

# 查看角度反馈
rostopic echo /usv/pump_angles

# 手动触发采样
rosservice call /usv/trigger_sampling

# 启动自动化
rosservice call /usv/automation_start

# 停止自动化
rosservice call /usv/automation_stop

# 查看任务状态
rostopic echo /usv/trigger_status
```

### Web API 测试

```bash
# 获取配置
curl http://10.42.0.1:5000/api/config

# 启动采样
curl -X POST http://10.42.0.1:5000/api/mission/start

# 停止采样
curl -X POST http://10.42.0.1:5000/api/mission/stop
```

### MAVROS 启动 (参考)

```bash
roslaunch mavros apm.launch fcu_url:=/dev/ttyTHS1:57600
```

---

## 后续开发建议

1. **完整自动化引擎**: 使用 `smach` 实现复杂采样序列
2. **PID 数据包支持**: 添加 0xAA/0xBB 包解析
3. **吸光度计算**: 实现朗伯-比尔定律浓度换算
4. **自定义消息**: 为泵状态和检测结果创建专用 msg
5. **Web 接口**: Flask/FastAPI 提供配置 API
6. **数据记录**: rosbag 记录检测数据

---

## 许可证

MIT License
