# usv_ros

[![ROS Noetic](https://img.shields.io/badge/ROS-Noetic-22314E?logo=ros&logoColor=white)](https://wiki.ros.org/noetic)
[![Python](https://img.shields.io/badge/Python-3.8-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MAVLink](https://img.shields.io/badge/MAVLink-v2-0B7285)](https://mavlink.io/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-7-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`usv_ros` 是水质监测无人船的船载 ROS 载荷仓库，目标运行环境是 Jetson Nano / Ubuntu 20.04 /
ROS Noetic。它把检测装置、Web 控制台、MAVROS 和定制 MAVLink 链路组织成一个可部署的载荷系统。

这个仓库不负责飞控固件和 QGC UI 的编译，但它处在三端链路的中间：上接 Pixhawk/ArduRover 与
QGroundControl，下接 ESP32 检测装置主控。任何 MAVLink 字段或命令变更都必须同步核对
`ardupilot-usv/` 固件和 `WQ-USV-QGroundControl/` 自定义面板。

中文文档：`README.md`

English version: `README.en.md`

## 目录

- [系统定位](#系统定位)
- [运行拓扑](#运行拓扑)
- [仓库现状](#仓库现状)
- [目录结构](#目录结构)
- [环境准备](#环境准备)
- [启动与部署](#启动与部署)
- [配置模型](#配置模型)
- [ROS 接口](#ros-接口)
- [Web 控制台与 API](#web-控制台与-api)
- [MAVLink 链路](#mavlink-链路)
- [检测装置串口协议](#检测装置串口协议)
- [数据与日志](#数据与日志)
- [验证](#验证)
- [故障排查](#故障排查)
- [开发约束](#开发约束)
- [License](#license)

## 系统定位

`usv_ros` 在船载计算机上承担以下职责：

- 控制 ESP32 检测装置主控：四路步进泵 `X/Y/Z/A`、进样泵 PWM、角度流和 ADS 分光采样。
- 管理采样自动化：Web 下发多步骤采样序列，ROS 侧执行、暂停、恢复、停止，并记录任务数据。
- 提供 Web 控制台：Flask + Socket.IO 后端，React/Vite 前端，支持实时监控、硬件设置、航点采样配置、
  数据查看、日志查看和链路诊断。
- 接入飞控任务链路：通过 MAVROS 读取状态、切换模式；通过 `mavlink-routerd` TCP 端点发送载荷遥测、
  接收 QGC/飞控自定义指令。
- 向定制 QGC 回传载荷状态：以 `NAMED_VALUE_FLOAT` 上报电压、吸光度、泵角度、采样进度、PID 状态等字段。

默认主链路由 `launch/usv_bringup.launch` 启动：

| 节点 | 脚本 | 作用 |
|---|---|---|
| `/mavros` | `mavros/launch/apm.launch` | 飞控状态、任务、模式切换服务 |
| `/pump_control_node` | `scripts/pump_control_node.py` | 串口握手、泵控、自动化、进样泵、ADS 分光采集 |
| `/web_config_server` | `scripts/web_config_server.py` | Web 页面、REST API、Socket.IO、配置与数据记录 |
| `/usv_mavlink_bridge` | `scripts/usv_mavlink_router_bridge.py` | router TCP 端点上的 MAVLink 遥测、命令、ACK 桥接 |
| `/mavlink_trigger_node` | `scripts/mavlink_trigger_node.py` | 采样命令解释、任务阶段状态机、航点采样控制 |

`scripts/mission_coordinator_node.py` 仍保留在仓库内，但当前不在默认 launch 主链路中。

## 运行拓扑

```text
QGroundControl / 定制 USV 面板
  |  COMMAND_LONG 31010..31016
  |  NAMED_VALUE_FLOAT 载荷字段
  v
数传电台 -> Pixhawk 6C / 定制 ArduRover
  |  TELEM2, MAVLink2, 921600 bps
  v
mavlink-routerd  (Jetson Nano)
  |-- UDP 127.0.0.1:14550 -> MAVROS
  |-- TCP 127.0.0.1:5760 -> usv_mavlink_router_bridge.py
  v
ROS Noetic / usv_ros
  |-- mavlink_trigger_node.py
  |-- web_config_server.py
  |-- pump_control_node.py
  v
ESP32 检测装置主控
  |-- X/Y/Z/A 步进泵
  |-- 进样泵 PWM
  |-- MT6701 角度采集
  |-- ADS122C04 分光采样
```

当前稳定链路不是“QGC 直连 ROS”。飞控固件会缓存来自伴随计算机的 `NAMED_VALUE_FLOAT` 载荷字段，并以
2 Hz 受控转发到 GCS；这避免了 MAVLink routing 对直转发的阻断。

## 仓库现状

| 指标 | 当前值 |
|---|---:|
| ROS Python 脚本 | 13 |
| Linux 运维脚本 | 15 |
| Web API 路由 | 52 |
| 前端 TypeScript/TSX 文件 | 29 |
| 单元测试文件 | 2 |
| 已提交前端构建产物 | 4 |
| 稳定标签 | `v0.2.0-stable` |
| 远端仓库 | `https://github.com/MIGO-OvO/usv_ros.git` |

已实现的核心能力包括：

- `mavlink-routerd` 串口复用：MAVROS 与自定义 bridge 分离。
- 13 个 QGC 载荷遥测字段：`USV_VOLT`、`USV_ABS`、`PUMP_X/Y/Z/A`、`USV_STAT`、`USV_PKT`、
  `USV_STEP`、`USV_STOT`、`USV_SCNT`、`USV_PERR`、`USV_PMOD`。
- 检测装置身份握手：`HELLO?` / `DET?` 必须返回 `DET_ID:USV_DETECTOR*`。
- Web 端硬件设置热切换：保存串口参数后调用 `/usv/pump_reconnect` 重连泵控节点。
- 航点采样配置 CRUD、任务配置导入导出、任务数据 JSON 记录和 CSV 下载。
- 开机自启脚本：热点、ROS 主系统、router 与自检统一由 `usv-boot.service` 管理。

## 目录结构

```text
usv_ros/
├── CMakeLists.txt
├── package.xml
├── README.md
├── README.en.md
├── TESTING.md
├── config/
│   └── usv_params.yaml              # launch 加载的 ROS 参数基线
├── docs/current/
│   ├── overview.md                  # 本仓库架构摘要
│   └── roadmap.md                   # 后续路线规划
├── launch/
│   └── usv_bringup.launch           # 主启动入口
├── scripts/
│   ├── pump_control_node.py         # 检测装置串口、泵控、自动化、ADS 数据
│   ├── web_config_server.py         # Flask/Socket.IO Web 网关
│   ├── mavlink_trigger_node.py      # MAVLink 指令与采样状态机
│   ├── usv_mavlink_router_bridge.py # mavlink-router TCP 桥
│   ├── common_env.sh                # 运行目录、日志、router 公共逻辑
│   ├── start_usv_all.sh             # 后台启动 roscore + router + 主系统
│   ├── stop_usv_all.sh              # 安全停止主系统、router、roscore
│   ├── status_usv_all.sh            # 进程、热点、ROS、MAVROS、bridge 诊断
│   └── lib/
│       ├── automation_engine.py
│       └── command_generator.py
├── frontend/
│   ├── src/                         # React 控制台源码
│   └── package.json
├── static/dist/                     # Web 前端构建产物
└── tests/
    ├── test_boot_service_scripts.py
    └── test_mavlink_command_compat.py
```

## 环境准备

### 目标环境

- Jetson Nano / Ubuntu 20.04
- ROS Noetic + catkin
- Python 3.8
- Pixhawk 6C / ArduRover 定制固件
- ESP32 检测装置固件
- Node.js 18+，仅在需要重构建 Web 前端时使用

### ROS 与系统依赖

在 Jetson 或 Ubuntu/WSL 的 ROS 工作空间根目录执行：

```bash
cd ~/usv_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make
source devel/setup.bash
```

建议加入 shell 初始化：

```bash
echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
echo "source ~/usv_ws/devel/setup.bash" >> ~/.bashrc
```

常用依赖：

```bash
sudo apt update
sudo apt install ros-noetic-mavros ros-noetic-mavros-extras mavlink-router python3-pip
sudo /opt/ros/noetic/lib/mavros/install_geographiclib_datasets.sh
python3 -m pip install pyserial flask flask-cors flask-socketio eventlet pymavlink
```

如果发行版包名不同，只要系统中能找到 `mavlink-routerd` 即可；也可以通过环境变量
`MAVLINK_ROUTERD_BIN` 指定二进制路径。

### 前端构建

仓库已包含 `static/dist/` 构建产物。仅在修改前端时需要重新构建：

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm install
npm run build
```

Vite 的输出会写入 `../static/dist`，由 `web_config_server.py` 直接提供。

### 首次脚本赋权

```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/*.sh
```

## 启动与部署

### 完整船载启动

推荐用脚本启动完整链路。脚本会确保运行目录存在，必要时后台启动 `roscore`，启动 `mavlink-routerd`，
最后启动 `roslaunch usv_ros usv_bringup.launch`。

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
```

默认 router 参数来自 `scripts/common_env.sh`：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FCU_UART_DEVICE` | `/dev/ttyTHS1` | Jetson 接 Pixhawk TELEM2 的串口 |
| `FCU_UART_BAUD` | `921600` | 飞控 MAVLink 串口波特率 |
| `ROUTER_MAVROS_UDP` | `127.0.0.1:14550` | MAVROS UDP 端点 |
| `ROUTER_BRIDGE_UDP` | `127.0.0.1:14551` | 预留 bridge UDP 端点 |
| `ROUTER_TCP_PORT` | `5760` | bridge 默认 TCP 端点 |
| `WEB_PORT` | `5000` | Web 控制台端口 |

示例：现场串口不是 `/dev/ttyTHS1` 时覆盖：

```bash
cd ~/usv_ws
FCU_UART_DEVICE=/dev/ttyUSB1 FCU_UART_BAUD=921600 ./src/usv_ros/scripts/start_usv_all.sh
```

脚本也会透传 launch 参数：

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

停止、重启和查看日志：

```bash
./src/usv_ros/scripts/stop_usv_all.sh
./src/usv_ros/scripts/restart_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
tail -f ~/usv_ws/.usv_run/logs/mavlink_router.log
```

### 直接 roslaunch

适合开发调试。注意：直接 `roslaunch` 不会替你启动 `mavlink-routerd`。

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

无飞控、只调试泵控和 Web：

```bash
roslaunch usv_ros usv_bringup.launch \
  enable_mavros:=false \
  enable_mavlink_trigger:=false \
  enable_mavlink_bridge:=false
```

前台启动主系统但由脚本保证 router 已就绪：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_ros_master.sh
./src/usv_ros/scripts/start_usv_system.sh
```

### Web 访问

- 局域网：`http://<Jetson-IP>:5000`
- 热点模式：`http://10.42.0.1:5000`
- 本机调试：`http://127.0.0.1:5000`

调试页面来源：

```bash
curl http://127.0.0.1:5000/api/ui/debug
```

### 热点

手动创建 WPA-PSK 热点：

```bash
cd ~/usv_ws
sudo ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
sudo ./src/usv_ros/scripts/stop_hotspot.sh
```

默认热点连接名为 `USV_AP`，默认地址为 `10.42.0.1`。可通过 `HOTSPOT_IFACE`、`HOTSPOT_CONN_NAME`、
`HOTSPOT_IP`、`HOTSPOT_ROUTE_METRIC` 覆盖。

推荐现场使用双网卡并行：USB Wi-Fi 作为热点网卡，板载 Wi-Fi、网线或手机 USB 共享作为外网上游。

```bash
nmcli dev status
nmcli dev wifi connect "<外网SSID>" password "<外网密码>" ifname wlan0

cd ~/usv_ws
sudo HOTSPOT_IFACE=wlan1 ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
ip route
./src/usv_ros/scripts/status_usv_all.sh
```

热点连接会设置 `ipv4.never-default=yes`、`ipv6.never-default=yes` 和较高 route metric，避免 `USV_AP`
抢默认路由。`status_usv_all.sh` 会输出 `internet: ... source=external ...`，用于确认默认路由仍走外网接口。

### 开机自启

安装 systemd 服务：

```bash
cd ~/usv_ws
sudo ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

双网卡并行安装推荐指定热点网卡：

```bash
cd ~/usv_ws
sudo HOTSPOT_IFACE=wlan1 ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

如果只通过 SSH 访问 Web 配置页，可以关闭自启热点，只保留 ROS/Web 自启：

```bash
cd ~/usv_ws
sudo USV_ENABLE_HOTSPOT=false ./src/usv_ros/scripts/install_boot_service.sh
```

电脑侧通过 SSH 端口转发访问：

```bash
ssh -N -L 5000:127.0.0.1:5000 jetson@<Jetson_IP>
```

浏览器打开 `http://127.0.0.1:5000`。如果本机 5000 被占用，可改成 `-L 5050:127.0.0.1:5000`
并访问 `http://127.0.0.1:5050`。

默认启动顺序：

1. 默认创建/恢复热点；`USV_ENABLE_HOTSPOT=false` 时跳过。
2. 以安装脚本调用者作为运行用户启动 `start_usv_all.sh`。
3. 等待 Web、ROS 节点、MAVROS 和 bridge 诊断就绪；启用热点时额外等待热点就绪。
4. 自检结果写入 `~/usv_ws/.usv_run/logs/boot_check.log`。

常用命令：

```bash
sudo systemctl status usv-boot.service
sudo journalctl -u usv-boot.service -f
sudo systemctl restart usv-boot.service
sudo ./src/usv_ros/scripts/uninstall_boot_service.sh
```

现场调试时可临时放宽严格自检：

```bash
sudo USV_STRICT_SELF_CHECK=false ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

## 配置模型

配置分为三层，不要混用：

| 类型 | 路径 | 作用 |
|---|---|---|
| ROS 静态参数 | `config/usv_params.yaml` | launch 时加载，定义泵、I2C、ADS、MAVROS 参考参数 |
| Web 运行配置 | `~/usv_ws/config/sampling_config.json` | Web 保存的采样步骤、航点采样、硬件设置 |
| 校准数据 | `~/usv_ws/config/calibration.json` | Web 角度零点校准 offset |

`web_config_server.py` 的“保存并应用”硬件设置流程：

1. 写入 `sampling_config.json` 的 `hardware` 段。
2. 更新 `/pump_control_node/serial_port`、`/pump_control_node/baudrate`、`/pump_control_node/timeout`。
3. 调用 `/usv/pump_reconnect`。
4. `pump_control_node.py` 重新打开串口并执行检测装置身份握手。

### launch 参数

`launch/usv_bringup.launch` 当前暴露的参数如下：

| 分组 | 参数 |
|---|---|
| 泵控 | `pump_port`、`pump_baudrate`、`pump_timeout`、`pid_mode`、`pid_precision`、`spectro_sample_wait_timeout` |
| Web | `web_host`、`web_port`、`web_ui` |
| MAVROS | `enable_mavros`、`mavros_fcu_url`、`mavros_gcs_url`、`mavros_tgt_system`、`mavros_tgt_component`、`mavros_fcu_protocol`、`mavros_respawn` |
| 采样状态机 | `mavros_timeout`、`hold_settle_time`、`stable_check_timeout`、`stable_speed_threshold`、`stable_yaw_rate_threshold`、`sampling_retry_count`、`sampling_on_fail` |
| MAVLink bridge | `mavlink_source_system`、`mavlink_source_component`、`mavlink_router_url` |
| 节点开关 | `enable_pump`、`enable_web`、`enable_mavlink_trigger`、`enable_mavlink_bridge` |

示例：

```bash
roslaunch usv_ros usv_bringup.launch \
  pump_port:=/dev/ttyUSB0 \
  web_port:=5000 \
  hold_settle_time:=5.0 \
  sampling_retry_count:=1 \
  sampling_on_fail:=SKIP
```

## ROS 接口

### 主要 topic

| Topic | 类型 | 方向 | 说明 |
|---|---|---|---|
| `/usv/pump_command` | `std_msgs/String` | sub | 直接下发检测装置文本命令，如 `XEFR90.0P0.1` |
| `/usv/pump_step` | `std_msgs/String` | sub | 单步采样 JSON |
| `/usv/automation_steps` | `std_msgs/String` | pub/sub | Web 或 trigger 发布整套采样步骤 |
| `/usv/pump_angles` | `std_msgs/String` | pub | X/Y/Z/A 角度 JSON |
| `/usv/pump_status` | `std_msgs/String` | pub | 泵和自动化状态文本/结构化状态 |
| `/usv/automation_status` | `std_msgs/String` | pub | 自动化进度、步骤号、循环、PID 模式 |
| `/usv/pump_pid_complete` | `std_msgs/String` | pub | PID 完成通知 |
| `/usv/pump_pid_error` | `std_msgs/String` | pub | PID 误差 JSON |
| `/usv/injection_pump_status` | `std_msgs/String` | pub | 进样泵状态 JSON |
| `/usv/spectrometer_voltage` | `std_msgs/String` | pub | 分光数据 JSON，包含 `voltage`、`absorbance` 等 |
| `/usv/spectrometer_status` | `std_msgs/String` | pub | ADS 配置、采集中、错误等状态 |
| `/usv/spectrometer_raw` | `std_msgs/String` | pub | 原始分光包 |
| `/usv/spectrometer_absorbance` | `std_msgs/String` | pub | 吸光度数据 |
| `/usv/mission_status` | `std_msgs/String` | pub | 任务阶段：`IDLE`、`SAMPLING`、`RESUMING_AUTO` 等 |
| `/usv/trigger_status` | `std_msgs/String` | pub | 触发状态：开始、停止、暂停、校准等 |
| `/usv/bridge_diagnostics` | `std_msgs/String` | pub | router bridge 诊断 JSON |
| `/usv/radio_status` | `std_msgs/String` | pub | `RADIO_STATUS` 电台链路质量 |
| `/usv/mavlink_cmd_rx` | `std_msgs/Float32MultiArray` | pub/sub | bridge 转发的 QGC/飞控命令 |
| `/usv/mavlink_cmd_ack` | `std_msgs/Float32MultiArray` | pub/sub | trigger 请求 bridge 发送 `COMMAND_ACK` |

### 主要 service

| Service | 类型 | 说明 |
|---|---|---|
| `/usv/pump_stop` | `std_srvs/Trigger` | 紧急停泵 |
| `/usv/automation_start` | `std_srvs/Trigger` | 启动当前自动化步骤 |
| `/usv/automation_stop` | `std_srvs/Trigger` | 停止自动化并停泵 |
| `/usv/automation_pause` | `std_srvs/Trigger` | 暂停自动化 |
| `/usv/automation_resume` | `std_srvs/Trigger` | 恢复自动化 |
| `/usv/injection_pump_on` | `std_srvs/Trigger` | 打开进样泵 |
| `/usv/injection_pump_off` | `std_srvs/Trigger` | 关闭进样泵 |
| `/usv/injection_pump_get_status` | `std_srvs/Trigger` | 查询进样泵状态 |
| `/usv/pump_reconnect` | `std_srvs/Trigger` | 按最新 ROS 参数重连检测装置串口 |
| `/usv/spectrometer_start` | `std_srvs/Trigger` | 下发 `ADSSTART` |
| `/usv/spectrometer_stop` | `std_srvs/Trigger` | 下发 `ADSSTOP` |
| `/usv/i2c_map_apply` | `std_srvs/Trigger` | 下发当前 I2C 映射 |
| `/usv/trigger_sampling` | `std_srvs/Trigger` | 手动触发带 HOLD/稳定判定的采样流程 |

常用检查：

```bash
rosnode list
rostopic list | grep /usv/
rostopic echo /usv/pump_status
rostopic echo /usv/bridge_diagnostics
rosservice call /usv/pump_stop
```

## Web 控制台与 API

Web 后端是 `scripts/web_config_server.py`，默认监听 `0.0.0.0:5000`。它既提供前端静态文件，也提供
REST API 和 Socket.IO 实时事件。

### 常用 REST API

| API | 说明 |
|---|---|
| `GET /api/config`、`POST /api/config`、`POST /api/config/reset` | 读取、保存、重置运行配置 |
| `POST /api/mission/start|stop|pause|resume` | 控制采样任务 |
| `GET/POST /api/waypoint-sampling` | 航点采样配置 |
| `GET /api/mission-config/export`、`POST /api/mission-config/import` | 任务配置导入导出 |
| `POST /api/motor/command`、`POST /api/motor/stop` | 手动电机命令 |
| `GET/POST /api/pid/config`、`POST /api/pid/test` | PID 参数与测试 |
| `GET /api/calibration/offsets`、`POST /api/calibration/zero|reset|start` | 角度校准 |
| `GET /api/data/voltage`、`POST /api/data/voltage/clear` | 当前内存电压历史 |
| `GET /api/data/missions` | 历史任务文件列表 |
| `GET /api/data/mission/<id>` | 历史任务 JSON |
| `GET /api/data/mission/<id>/csv` | 历史任务 CSV 下载 |
| `GET /api/logs/files`、`GET /api/logs/<filename>` | 系统日志查看 |
| `GET /api/hardware/config`、`POST /api/hardware/config` | 硬件连接配置 |
| `GET /api/hardware/serial-ports` | 枚举串口 |
| `POST /api/hardware/test-pump-port` | 打开串口并执行检测装置握手 |
| `POST /api/hardware/apply` | 保存硬件配置并重连泵控节点 |
| `GET /api/diagnostics/link|history|events|export` | 链路诊断 |

### Socket.IO 事件

后端会推送：

```text
status
pump_angles
raw_angles
voltage
pid_error
injection_pump_status
log
mavros_state
bridge_diagnostics
radio_status
```

### 快速 API 验证

```bash
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/config
curl http://127.0.0.1:5000/api/hardware/serial-ports
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
```

硬件配置热应用：

```bash
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```

## MAVLink 链路

### 端点

| 端点 | 默认值 | 用途 |
|---|---|---|
| Pixhawk UART | `/dev/ttyTHS1:921600` | Jetson 与飞控 TELEM2 物理链路 |
| MAVROS | `udp://127.0.0.1:14550@` | 状态、任务、模式切换 |
| bridge | `tcp:127.0.0.1:5760` | 自定义遥测、命令、ACK |

### 下行命令

`usv_mavlink_router_bridge.py` 从 router TCP 端点接收 `COMMAND_LONG`，筛选 `31010..31016`，转成
`/usv/mavlink_cmd_rx`；`mavlink_trigger_node.py` 执行后发布 `/usv/mavlink_cmd_ack`，再由 bridge 封装
`COMMAND_ACK`。

| 命令 | 含义 |
|---:|---|
| `31010` | 开始采样。`param2 > 0` 表示飞控原生采样触发，不切 HOLD |
| `31011` | 停止采样 |
| `31012` | 暂停采样 |
| `31013` | 恢复采样 |
| `31014` | 校准，向检测装置发布 `CALXYZA\r\n` |
| `31015` | 开始走航采样 |
| `31016` | 停止走航采样 |

bridge 也兼容飞控以 `NAMED_VALUE_FLOAT` 发出的原生任务触发：

- `USV_SMPL=<sample_id>`：触发一次定点采样，完成后 bridge 发送 `USV_DONE=<sample_id>`。
- `USV_SURV=1/0`：开启或停止走航采样。

### 上行遥测

bridge 发送：

- `HEARTBEAT`：1 Hz。
- `NAMED_VALUE_FLOAT`：2 Hz，每轮 13 个字段。

| 字段 | 说明 |
|---|---|
| `USV_VOLT` | 分光电压 |
| `USV_ABS` | 吸光度 |
| `PUMP_X`、`PUMP_Y`、`PUMP_Z`、`PUMP_A` | 四路泵角度 |
| `USV_STAT` | 任务阶段编码 |
| `USV_PKT` | 载荷包计数 |
| `USV_STEP` | 当前自动化步骤号 |
| `USV_STOT` | 自动化总步骤数 |
| `USV_SCNT` | 样本计数 |
| `USV_PERR` | PID 误差 |
| `USV_PMOD` | PID 模式：0 空闲、1 运行、2 完成、3 错误 |

诊断：

```bash
rostopic echo /usv/bridge_diagnostics
rostopic echo /usv/mavlink_cmd_rx
rostopic echo /usv/radio_status
```

## 检测装置串口协议

默认泵控串口为 `/dev/ttyUSB0`，`115200 8N1`，文本命令以 `\r\n` 结尾。

连接时 `pump_control_node.py` 会：

1. 打开串口并释放 `DTR=False`、`RTS=False`，避免主动复位 ESP32。
2. 发送 `HELLO?\r\n` 和 `DET?\r\n`。
3. 必须收到 `DET_ID:USV_DETECTOR*`，否则关闭串口并报错。

Web 的 `POST /api/hardware/test-pump-port` 使用同一握手逻辑，不能只用“串口可打开”判断连接成功。

常用下行命令：

| 命令 | 示例 | 说明 |
|---|---|---|
| PID 闭环 | `XEFR90.0P0.1` | X 轴正转 90 度，精度 0.1 |
| 开环角度 | `XEFV5J90.000` | X 轴正转，5 RPM，90 度 |
| 连续转动 | `XEFV5JG` | X 轴连续正转 |
| 停止 | `XDFV0J0` | X 轴停止 |
| PID 参数 | `PIDCFG:0.14,0.015,0.06,1.0,8.0` | 配置 PID |
| 进样泵 | `PUMP:ON`、`PUMP:OFF`、`PUMP:SET:60` | 开关和速度 |
| I2C 映射 | `I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2` | TCA 通道映射 |
| ADS 配置 | `ADSCFG:CH=2,ADDR=0x40,AIN=AIN0,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=20` | 分光 ADC |
| ADS 启停 | `ADSSTART`、`ADSSTOP` | 分光采集 |
| 校准 | `CALXYZA` | 四轴校准 |

上行二进制包：

| 包头 | 内容 |
|---|---|
| `0x55 0xCC` | X/Y/Z/A 角度 float |
| `0x55 0xAA` | PID 数据 |
| `0x55 0xBB` | PID 测试结果 |
| `0x55 0xDD` | 分光数据：时间戳、通道、状态、raw code、电压 |

## 数据与日志

运行时目录都在工作空间根目录，不在本仓库目录内：

| 路径 | 内容 |
|---|---|
| `~/usv_ws/.usv_run/` | PID 与运行日志根目录 |
| `~/usv_ws/.usv_run/logs/roscore.log` | ROS Master 日志 |
| `~/usv_ws/.usv_run/logs/mavlink_router.log` | router 日志 |
| `~/usv_ws/.usv_run/logs/usv_system.log` | roslaunch 主日志 |
| `~/usv_ws/.usv_run/logs/boot_check.log` | 开机自启自检日志 |
| `~/usv_ws/config/sampling_config.json` | Web 采样配置 |
| `~/usv_ws/config/calibration.json` | 校准 offset |
| `~/usv_ws/data/missions/mission_*.json` | 任务采样数据 |

查看状态：

```bash
./src/usv_ros/scripts/status_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
curl http://127.0.0.1:5000/api/logs/files
curl "http://127.0.0.1:5000/api/logs/usv_system.log?lines=100"
```

导出任务数据：

```bash
curl http://127.0.0.1:5000/api/data/missions
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/csv > mission.csv
```

## 验证

### 代码逻辑验证

在仓库目录执行：

```bash
cd ~/usv_ws/src/usv_ros
python3 -m py_compile \
  scripts/pump_control_node.py \
  scripts/web_config_server.py \
  scripts/mavlink_trigger_node.py \
  scripts/usv_mavlink_router_bridge.py \
  scripts/lib/automation_engine.py \
  scripts/lib/command_generator.py
python3 -m unittest discover -s tests -p "test_*.py"
```

前端验证：

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm run lint
npm run build
```

### 运行时基线检查

完整启动后：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
rosnode list
rostopic echo -n 1 /mavros/state
rostopic echo -n 1 /usv/bridge_diagnostics
curl http://127.0.0.1:5000/api/ui/debug
```

通过判据：

- `status_usv_all.sh` 显示 `roscore`、`mavlink_router`、`usv_system` 运行。
- `ros_nodes` 至少包含 `/pump_control_node`、`/web_config_server`、`/mavlink_trigger_node`、
  `/usv_mavlink_bridge`、`/mavros`。
- `/mavros/state` 中 `connected: True`。
- `/usv/bridge_diagnostics` 的 `tx_named_value`、`pkt_count` 持续增长。
- Web 控制台可访问，`/api/ui/debug` 返回 `dist_index_exists=true` 或明确回退到静态页面。

更多现场测试流程见 [TESTING.md](TESTING.md)。

## 故障排查

| 现象 | 优先检查 |
|---|---|
| Web 页面空白 | `GET /api/ui/debug`，确认 `dist_index_exists`、`ui_mode` 和 `/assets/*` |
| 5000 端口不通 | `status_usv_all.sh`、`ss -ltn | grep 5000`、`usv_system.log` |
| 泵控节点启动失败 | 串口路径、权限、ESP32 供电、`DET_ID:USV_DETECTOR` 握手 |
| Web 串口测试失败 | `POST /api/hardware/test-pump-port` 返回的 `identity` / 错误信息 |
| 分光无数据 | `/usv/spectrometer_status`、`ADSSTART`、I2C 映射、检测装置固件状态 |
| MAVROS 断开 | 飞控串口线序、`SERIAL2_PROTOCOL=2`、`SERIAL2_BAUD=921`、`mavlink_router.log` |
| QGC 指令不到 ROS | `/usv/mavlink_cmd_rx`、router TCP 端点、QGC target sys/comp |
| QGC 面板无遥测 | `/usv/bridge_diagnostics` 的 `tx_named_value`、定制 ArduPilot/QGC 是否匹配 |
| stop 后担心误杀 | 当前脚本会校验 PID 命令行，只停止 `roscore`、`roslaunch`、`mavlink-router`、USV 相关 Python |

## 开发约束

- MAVLink 改动必须先看飞控源码，尤其是 `Rover/GCS_MAVLink_Rover.cpp`、`Rover/sensors.cpp` 和
  `libraries/GCS_MAVLink/`。不要只凭 README 或接口文档改命令/字段。
- `src/usv_ros/` 是独立 Git 仓库；提交时进入本目录执行 Git 操作，不要在总管理仓库根目录提交子仓库改动。
- Commit message 使用 `Feat:`、`Fix:`、`Refactor:`、`Docs:` 前缀。
- Windows 上通常只做代码编辑和逻辑验证；Jetson、Pixhawk、ESP32 的真实运行验证需要现场硬件。
- 更新 Web API、ROS topic/service 或 MAVLink 字段后，同步更新本 README、[TESTING.md](TESTING.md) 和工作空间
  `docs/current/INTERFACE.md`。

## License

`package.xml` 声明本仓库使用 MIT License。当前仓库未单独提交 `LICENSE` 文件；发布或对外分发前建议补齐。
