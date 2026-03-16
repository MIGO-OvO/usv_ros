# USV ROS Package

> ROS Noetic payload stack for USV water-quality monitoring (Jetson Nano + Pixhawk + ESP32 + NI DAQ).

中文文档：`README.md`
English version: `README.en.md`

## 目录

- [1. 项目简介](#1-项目简介)
- [2. 系统架构](#2-系统架构)
- [3. 仓库结构](#3-仓库结构)
- [4. 环境与初始准备](#4-环境与初始准备)
- [5. 启动方式](#5-启动方式)
- [6. 常用操作与验证](#6-常用操作与验证)
- [7. 运行模式说明](#7-运行模式说明)
- [8. 故障排查](#8-故障排查)
- [9. License](#9-license)

---

## 1. 项目简介

`usv_ros` 是无人船（USV）水质监测载荷系统的 ROS 包，部署在 Jetson Nano，负责以下能力：

- 串口控制 ESP32 电机板（四路步进泵 + 进样泵）
- 采集 NI DAQ 分光电压并发布 ROS 话题
- 提供 Web 控制界面（Flask + Socket.IO + React）
- 对接 MAVROS / MAVLink，实现任务触发与遥测回传

核心代码目录：

- `launch/usv_bringup.launch`：一键启动入口
- `scripts/pump_control_node.py`：泵控制节点（含进样泵）
- `scripts/spectrometer_node.py`：分光采集节点
- `scripts/web_config_server.py`：Web 网关节点
- `scripts/mavlink_trigger_node.py`：MAVLink 触发节点
- `scripts/usv_mavlink_bridge.py`：遥测桥接节点

---

## 2. 系统架构

### 2.1 组件分层

```text
Ground Station (QGC)
  ├─ MAVLink command (COMMAND_LONG 31010~31014)
  └─ MAVLink telemetry (NAMED_VALUE_FLOAT)
            ▲
            │
Pixhawk + MAVROS
            ▲
            │
Jetson Nano / ROS Noetic
  ├─ mavlink_trigger_node.py      (任务触发)
  ├─ mission_coordinator_node.py  (任务状态协调)
  ├─ usv_mavlink_bridge.py        (遥测桥接)
  ├─ web_config_server.py         (HTTP/WebSocket 网关)
  ├─ pump_control_node.py         (泵控制 + 自动化执行 + 进样泵协议)
  └─ spectrometer_node.py         (电压采样)
            ▲
            │
Hardware
  ├─ ESP32 motor board (UART)
  │   ├─ X/Y/Z/A 步进泵
  │   └─ 进样泵（DC PWM）
  └─ NI DAQ + 分光检测器 (USB)
```

### 2.2 关键数据流

1. Web UI 调用 `web_config_server.py` REST API，或通过 Socket.IO 接收实时状态。
2. `web_config_server.py` 将控制请求转发为 ROS topic/service。
3. `pump_control_node.py` 对串口协议进行编解码，驱动 ESP32 并发布泵状态。
4. `spectrometer_node.py` 发布 `/usv/spectrometer_voltage`。
5. `mavlink_trigger_node.py` 监听 `/mavros/mavlink/from`，将采样触发转换为 ROS 动作。
6. `usv_mavlink_bridge.py` 将载荷状态映射回 `/mavros/mavlink/to` 遥测。

### 2.3 当前 ROS 节点（`usv_bringup.launch`）

默认可启动以下节点（可通过 `enable_*` 参数单独开关）：

- `pump_control_node`
- `spectrometer_node`
- `web_config_server`
- `mavlink_trigger_node`
- `usv_mavlink_bridge`

---

## 3. 仓库结构

```text
src/usv_ros/
├─ config/
│  └─ usv_params.yaml
├─ launch/
│  └─ usv_bringup.launch
├─ scripts/
│  ├─ pump_control_node.py
│  ├─ spectrometer_node.py
│  ├─ web_config_server.py
│  ├─ mavlink_trigger_node.py
│  ├─ mission_coordinator_node.py
│  ├─ usv_mavlink_bridge.py
│  └─ lib/
├─ frontend/
│  ├─ src/
│  └─ package.json
├─ static/
│  └─ dist/ (前端构建产物)
├─ README.md
└─ README.en.md
```


## 4. 环境与初始准备

### 4.1 软件版本基线

- Ubuntu 20.04 (Jetson Nano)
- ROS Noetic
- Python 3.8
- Node.js 18+（仅前端开发/重构建需要）

### 4.2 ROS 与系统依赖

```bash
cd ~/usv_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make
source devel/setup.bash
```

建议将环境加载加入 `~/.bashrc`：

```bash
echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
echo "source ~/usv_ws/devel/setup.bash" >> ~/.bashrc
```

### 4.3 Python 依赖

```bash
pip3 install pyserial flask flask-cors flask-socketio eventlet
# NI DAQ 硬件环境可选
pip3 install nidaqmx
```

### 4.4 前端与静态资源准备

`web_config_server.py` 支持两种 UI 来源：

- `static/index.html`（静态模式）
- `static/dist/index.html`（前端构建产物）

如需更新前端产物：

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm install
npm run build
# 产物应位于 ../static/dist
```

### 4.5 硬件与链路检查（启动前）

1. 串口设备：确认 ESP32 串口存在（如 `/dev/ttyUSB0`）。
2. DAQ 设备：确认 NI DAQ 已连接并识别（如 `Dev1/ai0`）。
3. MAVROS：确认 `/mavros/state` 正常更新。
4. 参数文件：确认 `config/usv_params.yaml` 与现场硬件一致。

---

## 5. 启动方式

### 5.1 单终端一键启动（推荐）

适用于常规部署；`roslaunch` 会自动拉起 ROS Master。

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

### 5.2 多终端顺序启动（调试/联调推荐）

适用于你提到的“先起 ROS，再起服务”流程，便于分终端观察日志。

**终端 A：启动 ROS Master**

```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
roscore
```

**终端 B：启动载荷服务节点**

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

**终端 C（可选）：运行时监控**

```bash
source devel/setup.bash
rosnode list
rostopic echo /usv/pump_status
```

### 5.3 一键启动/停止脚本（Linux 终端直接运行）

已新增以下脚本到 `src/usv_ros/scripts/`：

- `start_usv_all.sh`：一键后台启动 `roscore` + `usv_ros` 主系统
- `stop_usv_all.sh`：一键停止 `usv_ros` 主系统 + `roscore`
- `common_env.sh`：公共环境加载、PID 管理、日志目录管理

首次使用建议先赋予执行权限：

```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/common_env.sh
chmod +x src/usv_ros/scripts/start_usv_all.sh
chmod +x src/usv_ros/scripts/stop_usv_all.sh
```

一键启动：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
```

一键停止：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/stop_usv_all.sh
```

脚本行为说明：

- `start_usv_all.sh` 会先自动加载 ROS Noetic 和工作空间环境。
- 若未检测到运行中的 `roscore`，会先后台启动 `roscore`。
- 随后后台启动 `roslaunch usv_ros usv_bringup.launch`。
- PID 文件与日志保存在 `~/usv_ws/.usv_run/`：
  - `~/usv_ws/.usv_run/roscore.pid`
  - `~/usv_ws/.usv_run/usv_system.pid`
  - `~/usv_ws/.usv_run/logs/roscore.log`
  - `~/usv_ws/.usv_run/logs/usv_system.log`

查看日志：

```bash
tail -f ~/usv_ws/.usv_run/logs/roscore.log
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
```

脚本支持透传 launch 参数，例如：

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

如需保留分终端调试方式，也可以继续使用：

- `start_ros_master.sh`
- `start_usv_system.sh`
- `start_usv_minimal.sh`

### 5.4 按需覆盖 launch 参数

常用参数（见 `launch/usv_bringup.launch`）：

- `pump_port`（默认 `/dev/ttyUSB0`）
- `pump_baudrate`（默认 `115200`）
- `daq_device`（默认 `Dev1`）
- `daq_channel`（默认 `ai0`）
- `web_host`（默认 `0.0.0.0`）
- `web_port`（默认 `5000`）
- `enable_pump|enable_spectrometer|enable_web|enable_mavlink_trigger|enable_mavlink_bridge`

示例：仅启动泵控 + Web

```bash
roslaunch usv_ros usv_bringup.launch \
  enable_spectrometer:=false \
  enable_mavlink_trigger:=false \
  enable_mavlink_bridge:=false
```

### 5.5 访问 Web 控制台

- 局域网：`http://<Jetson-IP>:5000`
- 热点模式：`http://10.42.0.1:5000`

---

## 6. 常用操作与验证

### 6.1 ROS 基础检查

```bash
# 节点
rosnode list

# 核心话题
rostopic list | grep /usv/

# 示例：查看泵状态
rostopic echo /usv/pump_status
```

### 6.2 服务调用示例

```bash
# 启动自动化
rosservice call /usv/automation_start

# 暂停 / 恢复 / 停止
rosservice call /usv/automation_pause
rosservice call /usv/automation_resume
rosservice call /usv/automation_stop

# 紧急停泵
rosservice call /usv/pump_stop

# 进样泵
rosservice call /usv/injection_pump_on
rosservice call /usv/injection_pump_off
rosservice call /usv/injection_pump_get_status
```

### 6.3 Web API 快速检查

```bash
# 读取配置
curl http://127.0.0.1:5000/api/config

# 进样泵开关
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
```

---

## 7. 运行模式说明

### 7.1 Web UI 模式

`web_config_server.py` 支持参数 `~web_ui` 或环境变量 `USV_WEB_UI`：

- `auto`：优先 `static/dist`，否则回退 `static`
- `dist`：强制使用构建产物
- `vite`：前端开发联调入口（仅开发场景）

### 7.2 分光采集模式

- 当 `nidaqmx` 可用：真实 DAQ 采样
- 当 `nidaqmx` 不可用：自动进入模拟模式（用于开发联调）

---

## 8. 故障排查

1. **Web 页面空白**
   先访问 `GET /api/ui/debug`，检查 `dist_index_exists` 与 `ui_mode`。

2. **泵无响应**
   检查 `pump_port`、串口权限、ESP32 供电与线缆连接。

3. **无分光数据**
   检查 `daq_device/daq_channel` 参数，确认 NI 驱动和 `nidaqmx` 安装。

4. **MAVLink 未触发采样**
   检查 `/mavros/mavlink/from`、`/mavros/mission/reached` 是否有数据。

---

## 9. License

MIT License
