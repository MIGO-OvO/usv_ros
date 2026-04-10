# USV ROS 系统功能测试手册
Updated: 2026-04-02T00:00:00Z

## 0. 脚本命令速查

| 功能 | 脚本命令 | 备注 |
|---|---|---|
| **一键启动** | `./src/usv_ros/scripts/start_usv_all.sh` | 启动 `roscore` 及系统节点 |
| **一键停止** | `./src/usv_ros/scripts/stop_usv_all.sh` | 停止所有相关进程 |
| **重启系统** | `./src/usv_ros/scripts/restart_usv_all.sh` | 停止后重新启动 |
| **查看状态** | `./src/usv_ros/scripts/status_usv_all.sh` | 检查节点、进程与网络 |
| **开启热点** | `sudo ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678` | 创建默认 WPA-PSK 热点 |
| **关闭热点** | `sudo ./src/usv_ros/scripts/stop_hotspot.sh` | 关闭并尝试回连原 WiFi |
| **脚本赋权** | `chmod +x src/usv_ros/scripts/*.sh` | 首次运行前需执行 |
| **查看日志** | `tail -f ~/usv_ws/.usv_run/logs/usv_system.log` | 监控系统主日志 |

## 1. 测试前准备

### 1.1 软件依赖与权限
```bash
cd ~/usv_ws
# 确保脚本有执行权限
chmod +x src/usv_ros/scripts/*.sh
# 检查依赖
pip3 install pyserial flask flask-cors flask-socketio eventlet
```

### 1.2 硬件链路检查
- ESP32串口设备存在 (如 `/dev/ttyUSB0` 或 `/dev/ttyTHS1`)
- 分光计物理串口已连接
- 飞控 MAVROS 链路正常
- 前端静态资源已构建 (`src/usv_ros/static/dist/index.html` 或 `static/index.html`)

---

## 2. 本地功能验证 (按模块)
启动系统后 (`./src/usv_ros/scripts/start_usv_all.sh`)，分模块验证功能。

### 2.1 Web 配置与硬件设置
**验证目标：** Web Server 能正常提供页面并修改硬件配置。

```bash
# 检查服务与基础接口
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/hardware/serial-ports

# 验证硬件配置热更新
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```
> **通过判据：**
> - Web 接口返回 `success=true`
> - 配置热更新后，`usv_system.log` 中出现泵控节点 `Reconnecting` 的日志，且进程不退出。

### 2.2 载荷与传感器节点测试 (新架构)
**验证目标：** 泵控节点能够桥接分光串口数据，以 JSON 格式发布。

```bash
# 验证传感器数据流
rostopic echo /usv/spectrometer_status
rostopic echo /usv/spectrometer_voltage
# 启停传感器
rosservice call /usv/spectrometer_start
rosservice call /usv/spectrometer_stop
```
> **通过判据：**
> - `/usv/spectrometer_voltage` 的 `data` 必须是包含 `voltage`, `timestamp_ms` 等字段的 JSON 字符串，不再是旧版 Float64。
> - `spectrometer_start` 后状态变为 `acquiring`。

### 2.3 泵组控制与自动化测试
**验证目标：** 步进泵、进样泵能正常发令，自动化流程按预期启停。

```bash
# 步进泵与紧急停泵
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'" -1
rosservice call /usv/pump_stop

# 进样泵控制
rosservice call /usv/injection_pump_on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off

# 自动化流程启停 (需先在 Web 端配置步骤)
rosservice call /usv/automation_start
rostopic echo /usv/pump_status
rosservice call /usv/automation_stop
```
> **通过判据：**
> - 泵组有状态更新或实际动作。
> - 自动化启动后，`pump_control_node` 日志提示 `Automation start requested` 并开始执行步骤。

### 2.4 通信链路测试 (路由桥接模式与热点)

#### 2.4.1 MAVLink 路由桥接联动
**验证目标：** 数据不单纯走 MAVROS，而是使用 `mavlink-routerd` 及自定义桥接程序通信。

```bash
rostopic echo /usv/bridge_diagnostics    # 观察桥接路由诊断状态与上行计数
rostopic echo /usv/mavlink_cmd_rx        # 观察下行路由解析后的指令
```
> **通过判据：**
> - **上行：** 桥接诊断定期输出包含 `pkt_count` 且持续增长；分光数据变化时，QGC 侧对应刷新（无需观察 `mavros/mavlink/to`）。
> - **下行：** 收到 QGC 指令后，`/usv/mavlink_cmd_rx` 应输出解析好的指令数组，触发 `/usv/trigger_status` 改变。

#### 2.4.2 热点访问测试
> Web 节点本身不实现热点，由 `setup_hotspot.sh` 脚本通过 `nmcli` 管理。默认创建 WPA-PSK 安全热点。

```bash
# 创建并测试
sudo ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
nmcli -f GENERAL.NAME,802-11-wireless.ssid,802-11-wireless-security.key-mgmt con show USV_AP
```
> **通过判据：**
> - 客户端能成功连接 `USV_Control`，并访问 `http://10.42.0.1:5000`。
> - `status_usv_all.sh` 显示 `ip=assigned web_port=listening`。

---

## 3. 现场全链路联调指南 (QGC -> 飞控 -> 路由桥接 -> 载荷)
本章节用于实际装船部署后，按顺序闭环验证 `地面站-飞控-边缘计算板-载荷` 的打通状态。

### 3.1 第一步：飞控参数与物理连接确认
- **QGC 参数设置：**
  - `SERIAL2_PROTOCOL = 2` (MAVLink2)
  - `SERIAL2_BAUD = 921` (921600 bps)
  - `SYSID_THISMAV = 1`
- **物理连接：** Jetson Nano (如 `ttyTHS1`) 的 TX/RX 接飞控 `TELEM2`。此串口由 `mavlink-routerd` 独占，MAVROS 通过 UDP 监听路由转发数据。

### 3.2 第二步：基线状态检查
在 Jetson 开 2 个终端检查：
```bash
# 终端 1：启动系统
./src/usv_ros/scripts/start_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh

# 终端 2：检查 MAVROS 连通性
source /opt/ros/noetic/setup.bash && source devel/setup.bash
rostopic echo -n 1 /mavros/state
```
> **判据：** `/mavros/state` 必须显示 `connected: True`。如果是 `False`，停止后续步骤，检查硬件连线、飞控参数以及 `mavlink-routerd` 进程状态。

### 3.3 第三步：MAVLink 路由桥接上下行验证
```bash
# 终端 A：监控路由桥接诊断信息 (包含链路收发统计)
rostopic echo /usv/bridge_diagnostics

# 终端 B：监控下行桥接指令 (QGC -> Jetson)
rostopic echo /usv/mavlink_cmd_rx

# 终端 C：监控载荷执行状态
rostopic echo /usv/trigger_status
rostopic echo /usv/pump_status
```
**操作流程：**
1. 在 QGC 载荷面板点击 "开始采样"。
2. 观察终端 B 是否收到包含命令的 `Float32MultiArray` 数据，同时终端 C 的 `trigger_status` 是否变为 `sampling_started`。
3. 观察终端 A 诊断输出，确认数据链路收发计数正常增加。
4. 确认 QGC 面板上的吸光度、泵组状态等数据有实时刷新。

---

## 4. 故障排查 (Troubleshooting)

| 现象 | 可能原因与解决方案 |
|---|---|
| **`serial` 模块缺失报错** | 未安装依赖。执行 `pip3 install pyserial`。 |
| **5000 端口不通 / Web 退出** | 端口被占用。执行 `ss -tuln \| grep 5000` 检查，或查看 `usv_system.log` 中的 Werkzeug 报错。 |
| **`/usv/spectrometer_voltage` 无数据** | 1. 检查泵控/分光硬件链路是否正常连接<br>2. 检查 `/usv/spectrometer_status` 是否处于 error 状态。 |
| **接收到的分光数据不是 JSON** | 船载电脑上的代码未更新。确认已拉取最新架构代码，替换了旧版 `Float64` 逻辑。 |
| **保存并应用硬件配置失败** | 检查填写的串口设备路径是否存在 (`ls /dev/tty*`)，检查当前用户的 dialout 组权限。 |
| **热点无法创建 / 仍要求异常密码** | 1. 设备不支持 AP 模式<br>2. 存在历史错误配置。先运行 `sudo ./stop_hotspot.sh`，再重新创建热点。 |
| **桥接服务离线 / MAVROS 连不上** | 系统依赖 `mavlink-routerd` 进行串口复用。请检查路由进程是否运行，以及飞控 TX/RX 连线是否接反。 |
| **QGC 面板无数据显示** | 若 `bridge_diagnostics` 显示发包正常且持续增长，但 QGC 面板无数据，说明当前运行的可能不是带定制面板的 QGC 编译版本。 |
