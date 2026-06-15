# USV ROS 系统测试与现场排障手册

Updated: 2026-06-03

关键词：Jetson Nano、ROS Noetic、systemd、开机自启、热点、MAVROS、mavlink-routerd、QGC、泵控、分光计、航点采样。

## 0. 快速定位

| 场景 | 入口 | 通过判据 |
|---|---|---|
| 上电自动启动热点 + ROS | [2. 上电自启验证](#boot-autostart) | `usv-boot.service` active，`boot_check.log` 出现 `boot start complete` |
| 手动启动/停止 | [3. 脚本速查](#script-cheatsheet) | `status_usv_all.sh` 显示核心进程 RUNNING |
| 查看日志 | [4. 状态与日志索引](#status-logs) | 能定位到 `roscore`、router、roslaunch、自启日志 |
| Web/硬件配置 | [5.1 Web 配置](#web-config) | `/api/ui/debug` 和硬件接口正常返回 |
| 分光计/泵控 | [5.2](#spectrometer-flow) / [5.3](#pump-automation) | topic/service 有响应，日志无节点退出 |
| QGC 指令/遥测 | [5.4 MAVLink 路由](#mavlink-router-bridge) | `mavlink_cmd_rx` 有下行，`bridge_diagnostics` 计数增长 |
| 系统健康 | [5.5 系统健康监测](#system-health-flow) | Web、ROS、MAVLink 健康字段同步更新 |
| 现场闭环 | [6. 现场全链路](#field-e2e) | QGC 指令、飞控链路、载荷执行、遥测回传均连通 |
| 污染物地图 | [6.5 污染物地图现场证据包](#pollution-map-evidence) | Web 地图、CSV、GeoJSON、surface 和采样闭环日志可追溯 |
| 故障排查 | [7. 故障索引](#troubleshooting-index) | 按现象映射到日志、命令、责任模块 |

## 1. 基线准备

### 1.1 软件依赖

```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
catkin_make
source devel/setup.bash

chmod +x src/usv_ros/scripts/*.sh src/usv_ros/scripts/*.py
pip3 install pyserial flask flask-cors flask-socketio eventlet
```

必备命令：

| 命令 | 用途 | 缺失影响 |
|---|---|---|
| `roslaunch` / `rostopic` / `rosnode` | ROS 主系统 | ROS 无法启动或自检失败 |
| `mavlink-routerd` | 飞控串口复用 | MAVROS/bridge 无法同时接入飞控 |
| `nmcli` / `ip` | 热点创建与检查 | 热点和开机自启失败 |
| `ss` 或 `curl` | Web 端口检查 | 自检无法确认 Web 是否可达 |

### 1.2 硬件与链路

| 项 | 期望值 | 检查命令/位置 |
|---|---|---|
| ESP32 泵控串口 | `/dev/ttyUSB0` 或现场配置值 | `ls /dev/ttyUSB*` |
| Pixhawk TELEM2 | Jetson `/dev/ttyTHS1:921600` | 飞控参数 + 物理接线 |
| MAVROS | `/mavros/state connected: True` | `rostopic echo -n 1 /mavros/state` |
| Web UI | `static/dist/index.html` 或 `static/index.html` | `curl http://127.0.0.1:5000/api/ui/debug` |
| Wi-Fi AP | `wlan1` 支持 AP 模式和 5 GHz | `nmcli dev wifi list ifname wlan1` |

<a id="boot-autostart"></a>

## 2. 上电自启验证：systemd + 热点 + ROS + 自检

### 2.1 安装命令

代码更新到 Jetson Nano 后执行：

```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
catkin_make
sudo ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

推荐现场双网卡并行：`wlan0` 作为 2.4 GHz 外网 Wi-Fi，`wlan1` 专门创建 5 GHz `USV_Control`
热点；也可以用 `eth0` 或 `usb0` 保留外网上游，用于 `git pull`、`apt update`。

```bash
nmcli dev status
nmcli dev wifi connect "<外网SSID>" password "<外网密码>" ifname wlan0
nmcli connection modify "<外网SSID>" 802-11-wireless.band bg

cd ~/usv_ws
sudo USV_ENABLE_HOTSPOT=true INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

仅使用 SSH 访问 Web 配置页时，关闭自启热点：

```bash
cd ~/usv_ws
sudo USV_ENABLE_HOTSPOT=false ./src/usv_ros/scripts/install_boot_service.sh

# 电脑侧执行
ssh -N -L 5000:127.0.0.1:5000 jetson@<Jetson_IP>
```

然后在电脑浏览器打开 `http://127.0.0.1:5000`。

参数：

| 参数/环境变量 | 默认值 | 说明 |
|---|---|---|
| 第 1 参数 | `USV_Control` | 热点 SSID |
| 第 2 参数 | `12345678` | WPA-PSK 密码，至少 8 位 |
| 第 3 参数 | `SUDO_USER` | ROS 运行用户，默认安装脚本调用者 |
| `USV_ENABLE_HOTSPOT` | `false` | 是否在自启服务中创建/停止热点；设为 `true` 时启动 5 GHz 热点 |
| `INTERNET_IFACE` | `wlan0` | 外网 Wi-Fi 网卡 |
| `INTERNET_BAND` | `2.4g` | 外网 Wi-Fi 目标频段；会写入活动 NetworkManager profile |
| `INTERNET_WIFI_RECONNECT` | `false` | 是否立即重连外网 Wi-Fi 以应用频段；默认避免断开 SSH |
| `HOTSPOT_IFACE` | `wlan1` | 热点无线网卡 |
| `HOTSPOT_CONN_NAME` | `USV_AP` | NetworkManager 连接名 |
| `HOTSPOT_IP` | `10.42.0.1` | 热点网关 IP |
| `HOTSPOT_ROUTE_METRIC` | `900` | 热点连接 route metric；同时设置 never-default，避免抢默认路由 |
| `HOTSPOT_BAND` | `5g` | 热点频段 |
| `HOTSPOT_CHANNEL` | `149` | 热点信道 |
| `HOTSPOT_ALLOW_INTERNET_IFACE` | `false` | 是否允许热点复用当前默认路由网卡；双网卡部署保持默认 |
| `WEB_PORT` | `5000` | Web 服务端口 |
| `USV_BOOT_WAIT_SECONDS` | `90` | 热点/Web 等待超时 |
| `USV_STRICT_SELF_CHECK` | `true` | 严格自检，要求 ROS 节点和 MAVROS 均正常 |

无 Pixhawk、台架无飞控、只想验证热点/Web 时，先放宽严格自检：

```bash
cd ~/usv_ws
sudo USV_STRICT_SELF_CHECK=false ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

注意：热点密码会写入 `/etc/systemd/system/usv-boot.service`。现场请换专用密码。

### 2.2 上电/重启验收

```bash
sudo reboot
```

Jetson 重启后检查：

```bash
cd ~/usv_ws
sudo systemctl is-enabled usv-boot.service
sudo systemctl is-active usv-boot.service
sudo systemctl status usv-boot.service --no-pager
tail -n 120 .usv_run/logs/boot_check.log
./src/usv_ros/scripts/status_usv_all.sh
```

通过判据：

| 检查项 | 期望输出 |
|---|---|
| systemd | `enabled` + `active` |
| 自检日志 | `boot start complete` |
| 热点 | 启用热点时 `hotspot: ... iface=wlan1 ... conn=active ... ip=assigned ... band=5g channel=149 ... web_port=listening`；禁用热点时 `boot_check.log` 出现 `skip hotspot setup: disabled` |
| 外网 | `internet: ... iface=wlan0 source=external band=2.4g target_band=2.4g ... dns=ok github=reachable`，且默认路由不走热点网卡 |
| ROS 进程 | `roscore: RUNNING`、`mavlink_router: RUNNING`、`usv_system: RUNNING` |
| ROS 节点 | 严格模式下 `ros_nodes: ALL_OK` |
| MAVROS | 严格模式下 `mavros_link: CONNECTED` |
| Web | 客户端连热点后可访问 `http://10.42.0.1:5000` |

### 2.3 运维命令

```bash
# 查看服务与日志
sudo systemctl status usv-boot.service --no-pager
sudo journalctl -u usv-boot.service -f
tail -f ~/usv_ws/.usv_run/logs/boot_check.log

# 重启上电服务链路
sudo systemctl restart usv-boot.service

# 停止服务链路：先停 ROS，再停热点
sudo systemctl stop usv-boot.service

# 查看生成的 unit 文件
sudo systemctl cat usv-boot.service

# 卸载开机自启
sudo ./src/usv_ros/scripts/uninstall_boot_service.sh
```

### 2.4 自启失败快速定位

| 现象 | 首看 | 常见原因 |
|---|---|---|
| `usv-boot.service failed` | `sudo journalctl -u usv-boot.service -n 120 --no-pager` | 缺 `mavlink-routerd`、`nmcli`、ROS 未构建 |
| 热点无 IP | `.usv_run/logs/boot_check.log` + `nmcli dev status` | 网卡不支持 AP、`wlan1` 名称不对、旧连接冲突 |
| Web 端口未监听 | `tail -f .usv_run/logs/usv_system.log` | Flask 依赖缺失、5000 端口被占、节点启动失败 |
| `ros_nodes` 非 `ALL_OK` | `rosnode list` + `usv_system.log` | Python 依赖、launch 参数、节点初始化失败 |
| `mavros_link: DISCONNECTED` | `mavlink_router.log` + `/mavros/state` | 飞控未上电、TELEM2 接线/参数错误、router 未连串口 |
| 自检失败后热点也没了 | `boot_check.log` | 启动失败触发清理逻辑，需修复原因后 `systemctl restart` |

<a id="script-cheatsheet"></a>

## 3. 脚本速查：手动运行

所有命令默认从 `~/usv_ws` 执行。

| 功能 | 命令 | 输出/日志 |
|---|---|---|
| 启动 ROS 主系统 | `./src/usv_ros/scripts/start_usv_all.sh` | `.usv_run/logs/{roscore,mavlink_router,usv_system}.log` |
| 停止 ROS 主系统 | `./src/usv_ros/scripts/stop_usv_all.sh` | 停 `roslaunch`、router、roscore |
| 重启 ROS 主系统 | `./src/usv_ros/scripts/restart_usv_all.sh` | 停止后重新启动 |
| 状态总览 | `./src/usv_ros/scripts/status_usv_all.sh` | 进程、热点、外网默认路由、ROS 节点、MAVROS、bridge |
| 创建热点 | `sudo INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678` | NetworkManager 连接 `USV_AP` |
| 关闭热点 | `sudo ./src/usv_ros/scripts/stop_hotspot.sh` | 尝试回连原 Wi-Fi |
| 安装上电自启 | `sudo ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678` | 创建并启用 `usv-boot.service`，默认不立即启动 |
| 安装无热点自启 | `sudo USV_ENABLE_HOTSPOT=false ./src/usv_ros/scripts/install_boot_service.sh` | 只启动 ROS/Web，适合 SSH 端口转发 |
| 卸载上电自启 | `sudo ./src/usv_ros/scripts/uninstall_boot_service.sh` | 停止并删除 unit |

启动参数透传示例：

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

<a id="status-logs"></a>

## 4. 状态与日志索引

| 目标 | 命令 | 关键判据 |
|---|---|---|
| 自启日志 | `tail -f ~/usv_ws/.usv_run/logs/boot_check.log` | `boot start complete` 或明确 ERROR |
| systemd 日志 | `sudo journalctl -u usv-boot.service -f` | unit 启停、失败栈 |
| roscore 日志 | `tail -f ~/usv_ws/.usv_run/logs/roscore.log` | master 无异常退出 |
| router 日志 | `tail -f ~/usv_ws/.usv_run/logs/mavlink_router.log` | 串口/5760/TCP 无错误 |
| roslaunch 日志 | `tail -f ~/usv_ws/.usv_run/logs/usv_system.log` | 节点无 traceback |
| ROS 节点 | `rosnode list` | `/pump_control_node`、`/web_config_server`、`/system_health_node`、`/mavlink_trigger_node`、`/usv_mavlink_bridge`、`/mavros` |
| 单节点存活 | `rosnode ping /pump_control_node -c 1` | ping 成功 |
| MAVROS | `rostopic echo -n 1 /mavros/state` | `connected: True` |
| bridge 诊断 | `rostopic echo /usv/bridge_diagnostics` | `pkt_count`、`tx_named_value` 增长 |
| 系统健康 | `rostopic echo -n 1 /usv/system_health` | Jetson、detector、ROS 节点字段存在 |
| QGC 下行 | `rostopic echo /usv/mavlink_cmd_rx` | 点击 QGC 后出现 `Float32MultiArray` |
| Web | `curl http://127.0.0.1:5000/api/ui/debug` | 返回 UI debug JSON |
| 热点 | `nmcli con show --active` | `USV_AP` active |

## 5. 本地模块验证

先启动：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
```

<a id="web-config"></a>

### 5.1 Web 配置与硬件设置

```bash
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/hardware/serial-ports

curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```

通过判据：接口返回成功；`usv_system.log` 出现泵控重连日志；进程不退出。

<a id="spectrometer-flow"></a>

### 5.2 分光计数据流

```bash
rostopic echo /usv/spectrometer_status
rostopic echo /usv/spectrometer_voltage
rosservice call /usv/spectrometer_start
rosservice call /usv/spectrometer_stop
curl -X POST http://127.0.0.1:5000/api/spectrometer/baseline
```

通过判据：`/usv/spectrometer_voltage.data` 是 JSON 字符串，包含 `voltage`、`timestamp_ms` 等字段；启动后状态变为 `acquiring`；基线稳定后调用 baseline API，后续消息包含 `baseline_set=true`，且 `reference_voltage` 等于点击时的当前电压。

<a id="pump-automation"></a>

### 5.3 泵组控制与自动化

```bash
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'" -1
rosservice call /usv/pump_stop

rosservice call /usv/injection_pump_on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off

rosservice call /usv/automation_start
rostopic echo /usv/pump_status
rosservice call /usv/automation_stop
```

通过判据：泵状态有更新或实际动作；自动化启动后日志出现 `Automation start requested` 并进入步骤执行。

<a id="mavlink-router-bridge"></a>

### 5.4 MAVLink 路由桥接

```bash
rostopic echo /usv/bridge_diagnostics
rostopic echo /usv/mavlink_cmd_rx
rostopic echo /usv/trigger_status
```

通过判据：

| 方向 | 判据 |
|---|---|
| 上行遥测 | `bridge_diagnostics` 定期输出，`pkt_count` / `tx_named_value` 持续增长，QGC 可见 22 个字段 |
| 下行指令 | QGC 点击按钮后，`/usv/mavlink_cmd_rx` 出现命令数组 |
| 执行状态 | `/usv/trigger_status` 出现 `sampling_started` / `sampling_stopped` / `calibrate_started` |
| 航线采样 | QGC Plan 使用 `MAV_CMD_NAV_SCRIPT_TIME(param1=1)`；执行时 bridge 收到 `USV_SMPL`，完成后发 `USV_DONE` |
| 数据记录 | `sampling_started` 后 Web 数据中心出现任务记录；有效分光数据到达时数据点数量增长 |

### 5.4.1 Web 航线上传合同

Web 地图页的航线规划只写入飞控 mission，不解锁、不切 `AUTO`。后端接口必须走 MAVROS mission 服务并做回读比对：

```bash
curl -X POST http://127.0.0.1:5000/api/mission/plan/validate \
  -H 'Content-Type: application/json' \
  -d '{"waypoints":[{"lat":30.0,"lng":120.0,"sample":true},{"lat":30.001,"lng":120.002,"sample":false}]}'

curl -X POST http://127.0.0.1:5000/api/mission/plan/upload \
  -H 'Content-Type: application/json' \
  -d '{"replace":true,"waypoints":[{"lat":30.0,"lng":120.0,"sample":true},{"lat":30.001,"lng":120.002,"sample":false}]}'
```

通过判据：

| 项 | 判据 |
|---|---|
| 上传链路 | 依次调用 `/mavros/mission/clear`、`/mavros/mission/push`、`/mavros/mission/pull`；push 列表首项为 ArduPilot home 占位，避免真实首航点被 index 0 机制覆盖 |
| 采样任务项 | 采样航点后插入 `MAV_CMD_NAV_SCRIPT_TIME(42702)`，`param1=1`，`param2=1..255` |
| 安全边界 | 不调用 `/mavros/cmd/arming`，不调用 `/mavros/set_mode` |
| 回读验证 | `/mavros/mission/waypoints` 跳过 ArduPilot home 后的 command/坐标与上传计划一致，响应 `data.verified=true` |
| 地图反馈 | 上传成功后 `/api/map/live` 的 `route_waypoints` 立即反映 Web 航线 |

离线回归命令：

```bash
cd ~/usv_ws/src/usv_ros
python -m pytest tests/test_mission_plan_service.py tests/test_hardware_runtime_sync.py -q -k "mission_plan"
```

<a id="system-health-flow"></a>

### 5.5 系统健康监测

```bash
rostopic echo -n 1 /usv/detector_health
rostopic echo -n 1 /usv/system_health
curl http://127.0.0.1:5000/api/diagnostics/system
rostopic echo /usv/bridge_diagnostics
```

通过判据：

| 项 | 判据 |
|---|---|
| ESP32 健康帧 | `/usv/detector_health` 包含 `temperature_c`、`heap_percent_free`、`task_stack_hwm` |
| 聚合健康 | `/usv/system_health` 包含 `jetson`、`detector`、`ros_nodes`、`health` |
| Web 展示 | 监控页系统健康卡片刷新，REST API 返回 `latest` 和 `history` |
| MAVLink 健康字段 | QGC Fact 显示 `USV_JTMP`、`USV_ETMP`、`USV_JCPU`、`USV_JMEM`、`USV_EHEAP` |

### 5.6 热点访问

```bash
sudo INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
nmcli -f GENERAL.NAME,802-11-wireless.ssid,802-11-wireless-security.key-mgmt con show USV_AP
ip route show default
./src/usv_ros/scripts/status_usv_all.sh
```

通过判据：客户端能连接 `USV_Control`；访问 `http://10.42.0.1:5000`；状态显示
`iface=wlan1 conn=active ip=assigned band=5g channel=149 web_port=listening`；双网卡场景下
`internet` 行显示 `iface=wlan0 source=external band=2.4g target_band=2.4g`，默认路由不走热点网卡。

<a id="field-e2e"></a>

## 6. 现场全链路联调：QGC -> 飞控 -> Jetson -> 载荷

### 6.1 飞控参数与物理连接

| 项 | 期望值 |
|---|---|
| `SERIAL2_PROTOCOL` | `2`，MAVLink2 |
| `SERIAL2_BAUD` | `921`，921600 bps |
| `SYSID_THISMAV` | `1` |
| Jetson 串口 | `/dev/ttyTHS1` 接飞控 `TELEM2` |
| 串口占用 | `mavlink-routerd` 独占，MAVROS 走 UDP `127.0.0.1:14550` |

### 6.2 基线状态

```bash
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
rostopic echo -n 1 /mavros/state
rostopic echo /usv/bridge_diagnostics
```

硬性判据：`/mavros/state` 必须 `connected: True`。否则先停后续测试，排查飞控供电、接线、参数、router。

### 6.3 QGC 指令闭环

开 3 个终端：

```bash
# A：路由桥接诊断
rostopic echo /usv/bridge_diagnostics

# B：QGC 下行指令
rostopic echo /usv/mavlink_cmd_rx

# C：载荷执行状态
rostopic echo /usv/trigger_status
rostopic echo /usv/pump_status
```

操作：在 QGC 载荷面板点击“开始采样/停止采样/校准”。

通过判据：B 立刻出现 `Float32MultiArray`；C 状态跟随变化；A 收发计数增长；QGC 面板吸光度、泵组状态刷新。

### 6.4 自动航点采样闭环

```bash
rostopic echo /usv/mission_status
rostopic echo /usv/trigger_status
rostopic echo /usv/pump_status
```

流程：QGC 上传至少 2 个航点 Mission -> 开始任务 -> 船进入 AUTO -> 到达采样航点。

期望阶段：

```text
WAYPOINT_REACHED -> HOLDING -> WAITING_STABLE -> SAMPLING -> SAMPLING_DONE -> RESUMING_AUTO -> NAVIGATING
```

通过判据：到点后切 HOLD；速度/航向稳定后才采样；采样完成恢复 AUTO；同一航点不重复触发。

调参示例：

```bash
roslaunch usv_ros usv_bringup.launch \
  hold_settle_time:=5.0 \
  stable_speed_threshold:=0.10 \
  sampling_retry_count:=1 \
  sampling_on_fail:=SKIP
```

<a id="pollution-map-evidence"></a>

### 6.5 污染物地图现场证据包

前置：Web 设置页已保存单污染物线性模型、单位、校准 ID 或工作曲线 ID；走航门控已按现场水域设置最小距离、GPS 必需和分光 valid 必需。

采集流程：

1. QGC 上传航线并执行 `NAV_SCRIPT_TIME(param1=1)` 定点采样，或通过 `31015/31016` 启停走航采样。
2. Web 地图页选择当前任务，确认状态包含实时点位、污染物 surface 和走航门控信息；桌面端按 16:9 截图。
3. 导出任务 CSV、GeoJSON 和 IDW surface，并把 `/api/map/live` 快照一并保存。
4. 从 roslaunch/bridge 日志截取 `USV_SMPL/USV_DONE`、`USV_SURV` 或 `survey_gate_skipped:*` 片段。

导出命令：

```bash
MISSION_ID=<mission_id>
mkdir -p ~/usv_ws/evidence/$MISSION_ID
curl "http://127.0.0.1:5000/api/data/mission/$MISSION_ID/csv" \
  > ~/usv_ws/evidence/$MISSION_ID/mission.csv
curl "http://127.0.0.1:5000/api/data/mission/$MISSION_ID/geojson?metric=concentration&download=true" \
  > ~/usv_ws/evidence/$MISSION_ID/mission.geojson
curl "http://127.0.0.1:5000/api/data/mission/$MISSION_ID/surface?metric=concentration&size=80&power=2&download=true" \
  > ~/usv_ws/evidence/$MISSION_ID/mission_surface.json
curl "http://127.0.0.1:5000/api/map/live" \
  > ~/usv_ws/evidence/$MISSION_ID/live_map_snapshot.json
grep -E "USV_SMPL|USV_DONE|USV_SURV|survey_gate_skipped" \
  ~/usv_ws/.usv_run/logs/usv_system.log \
  > ~/usv_ws/evidence/$MISSION_ID/mavlink_sampling.log
```

离线自检命令：

```bash
cd ~/usv_ws/src/usv_ros
python3 scripts/verify_pollution_workflow.py --mock --evidence .omo/evidence/pollution-workflow-verify.json
```

通过判据：命令退出码为 0，JSON 证据中 `hardware_required=false`，并包含 CSV、GeoJSON、surface、导出头和前端 smoke/build 产物检查。

记录模板：

| 字段 | 记录值 |
|---|---|
| `mission_id` |  |
| `pollutant_name` / `unit` |  |
| `calibration_id` / `work_curve_id` |  |
| `sample_total` / `csv_rows` |  |
| `valid_gps_points` / `excluded_points` |  |
| `excluded_reasons` |  |
| `surface_grid` / `idw_power` |  |
| `survey_min_distance_m` / `last_gate_reason` |  |
| `screenshot_path` |  |
| `USV_SMPL/USV_DONE` 日志 |  |

<a id="troubleshooting-index"></a>

## 7. 故障索引：按现象查

| 现象 | 优先命令 | 责任模块/下一步 |
|---|---|---|
| systemd 自启失败 | `sudo journalctl -u usv-boot.service -n 120 --no-pager` | 缺依赖、热点失败、ROS 未构建、自检失败 |
| 进程 RUNNING 但节点缺失 | `rosnode list`、`tail -f usv_system.log` | roslaunch 内部节点失败 |
| `serial` 模块缺失 | `python3 -c "import serial"` | 执行 `pip3 install pyserial` |
| 5000 端口不通 | `ss -ltn \| grep 5000`、`usv_system.log` | Web 节点异常或端口占用 |
| 热点无法创建 | `nmcli dev status`、`nmcli con show USV_AP` | 网卡不支持 AP、接口名错误、历史连接冲突 |
| MAVROS `connected: False` | `mavlink_router.log`、飞控参数 | 飞控供电/TELEM2/baud/router 串口 |
| QGC 面板无数据 | `rostopic echo /usv/bridge_diagnostics` | 若计数增长，检查定制 QGC；若不增长，查 bridge/router |
| QGC 点击无下行 | `rostopic echo /usv/mavlink_cmd_rx` | QGC -> 飞控 -> router -> bridge 下行链路 |
| 有下行但无执行 | `rostopic echo /usv/trigger_status` | `mavlink_trigger_node.py` |
| 有执行但泵无动作 | `rostopic echo /usv/pump_status`、`ls /dev/tty*` | `pump_control_node.py`、串口、ESP32 |
| 分光数据不是 JSON | `rostopic echo /usv/spectrometer_voltage` | Jetson 代码未更新或旧节点仍在运行 |
| 停止脚本疑似误杀 | `cat .usv_run/*.pid`、`lsof -iTCP:5000 -n -P` | 先确认 PID/端口所有者，再执行 stop |

## 8. 停止与清理保护

停止前记录 PID 和端口：

```bash
cd ~/usv_ws
cat .usv_run/roscore.pid 2>/dev/null
cat .usv_run/mavlink_router.pid 2>/dev/null
cat .usv_run/usv_system.pid 2>/dev/null
lsof -iTCP:5000 -n -P
lsof -iTCP:5760 -n -P
```

安全停止：

```bash
./src/usv_ros/scripts/stop_usv_all.sh
sudo ./src/usv_ros/scripts/stop_hotspot.sh
```

上电自启服务链路停止：

```bash
sudo systemctl stop usv-boot.service
```

若 5000/5760 端口由非 `python`、`mavlink-routerd`、`roslaunch` 相关进程占用，先不要停止，确认是否为 SSH 隧道、调试代理或端口转发。
