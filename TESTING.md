# USV ROS 系统功能测试手册
Updated: 2026-04-02T00:00:00Z

## 1. 目标
用于验证 `src/usv_ros/` 当前主链路是否可用，包括：
- ROS 启停脚本
- Web 配置页访问
- 四路步进泵控制
- 进样泵控制
- 自动化流程
- MAVLink 触发与遥测桥接
- 热点访问链路

## 2. 测试前准备
### 2.1 软件依赖
```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
source devel/setup.bash
python3 -c "import serial, flask, flask_cors, flask_socketio"
pip3 show pyserial flask flask-cors flask-socketio eventlet
```
若 `import serial` 失败，先安装：
```bash
pip3 install pyserial flask flask-cors flask-socketio eventlet
```

### 2.2 硬件/链路检查
- ESP32 串口存在：`/dev/ttyUSB0` 或现场实际串口
- ADS 分光计：通过串口访问
- MAVROS 正常：`rostopic echo /mavros/state`
- 前端静态资源存在：`src/usv_ros/static/dist/index.html` 或 `static/index.html`

### 2.3 测试脚本权限
```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/common_env.sh
chmod +x src/usv_ros/scripts/start_usv_all.sh
chmod +x src/usv_ros/scripts/stop_usv_all.sh
chmod +x src/usv_ros/scripts/status_usv_all.sh
chmod +x src/usv_ros/scripts/restart_usv_all.sh
chmod +x src/usv_ros/scripts/setup_hotspot.sh
chmod +x src/usv_ros/scripts/stop_hotspot.sh
```

## 3. 启动与状态测试
### 3.1 一键启动
```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
```
通过判据：
- `~/usv_ws/.usv_run/roscore.pid` 存在
- `~/usv_ws/.usv_run/usv_system.pid` 存在
- 无立即退出

### 3.2 状态检查
```bash
./src/usv_ros/scripts/status_usv_all.sh
rosnode list
```
期望至少看到：
- `/pump_control_node`
- `/web_config_server`
- `/mavlink_trigger_node`
- `/usv_mavlink_bridge`


### 3.3 日志检查
```bash
tail -f ~/usv_ws/.usv_run/logs/roscore.log
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
```
重点关注：
- `ModuleNotFoundError: No module named 'serial'`
- Werkzeug 生产模式报错
- MAVROS 超时

## 4. Web 配置页测试
### 4.1 本机访问
```bash
curl http://127.0.0.1:5000
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/config
```
通过判据：
- 首页返回 HTML
- `/api/ui/debug` 返回 JSON
- `/api/config` 返回配置 JSON

### 4.2 局域网访问
在浏览器访问：
- `http://<Jetson-IP>:5000`

通过判据：
- Monitor / Automation / Settings 页面可打开
- 页面能显示状态，不是纯空白页

## 5. 泵控测试
### 5.1 步进泵命令测试
```bash
rostopic echo /usv/pump_status
rostopic echo /usv/pump_angles
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'" -1
```
通过判据：
- `/usv/pump_status` 有状态变化
- `/usv/pump_angles` 有反馈或原始角度更新

### 5.2 紧急停泵
```bash
rosservice call /usv/pump_stop
```
通过判据：
- 返回 `success: True`
- 状态进入 stop/stopped

### 5.3 自动化任务启动链路测试
1. 先确认 Web 中 `Automation` 页面已配置有效步骤，至少包含 1 个启用电机或进样泵的步骤。
2. 分别在终端观察：
```bash
rostopic echo /usv/pump_status
rostopic echo /usv/pump_pid_error
rosservice call /usv/automation_stop
```
3. 在 ROS 日志终端中重点观察以下关键字：
- `Automation steps loaded:`
- `Automation start requested:`
- `[Automation] 开始执行步骤:`
- `[Automation] 指令已发送:`
4. 在 Web 点击“启动任务”，或直接调用：
```bash
curl -X POST http://127.0.0.1:5000/api/mission/start
```
5. 若需手工验证步骤下发，可执行：
```bash
rostopic pub /usv/automation_steps std_msgs/String "data: '{\"steps\": [{\"name\": \"测试步1\", \"X\": {\"enable\": \"E\", \"direction\": \"F\", \"angle\": \"90\"}}], \"loop_count\": 1, \"pid_mode\": true, \"pid_precision\": 0.1}'" -1
rosservice call /usv/automation_start
```
通过判据：
- Web 返回 `success: true`
- `pump_control_node` 日志出现 `Automation steps loaded` 与 `Automation start requested`
- 随后出现 `[Automation] 开始执行步骤:` 和 `[Automation] 指令已发送:`
- 泵组有实际动作，或至少 `/usv/pump_status` 显示进入 automation/running

若失败，优先检查：
- Web 返回是否是 `No automation steps loaded`
- `Automation steps payload received but step list is empty`
- `/usv/automation_steps` 发布前后控制节点是否在线
- `pid_mode/pid_precision` 是否被错误配置

## 6. 进样泵测试
```bash
rostopic echo /usv/injection_pump_status
rosservice call /usv/injection_pump_on
rosservice call /usv/injection_pump_get_status
rosservice call /usv/injection_pump_off
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
curl -X POST http://127.0.0.1:5000/api/injection-pump/set -H "Content-Type: application/json" -d '{"speed":60}'
```
通过判据：
- `/usv/injection_pump_status` 中 `enabled/speed/last_response` 变化
- Web API 返回 `success=true`

## 7. 自动化流程测试
### 7.1 配置与启动
- 在 Web `Automation` 页面配置至少 1 个步骤
- 可加入 `step.pump.enable=true`、`step.pump.speed=60`

```bash
rosservice call /usv/automation_start
rostopic echo /usv/pump_status
```
通过判据：
- 状态进入 running/step
- 自动化步骤被执行
- 若步骤包含进样泵，则进样泵状态同步变化

### 7.2 暂停/恢复/停止
```bash
rosservice call /usv/automation_pause
rosservice call /usv/automation_resume
rosservice call /usv/automation_stop
```
通过判据：
- 三个服务均返回成功
- `/usv/pump_status` 出现对应状态变化

## 8. MAVLink 测试
### 8.1 命令下行
```bash
rostopic echo /usv/trigger_status
rostopic echo /mavros/mavlink/from
```
通过判据：
- 收到 `COMMAND_LONG` 后 `/usv/trigger_status` 变化
- `31010~31013` 可触发开始/停止/暂停/恢复
- `31014` 当前仅应看到 `calibrate_requested` 占位状态

### 8.2 遥测上行
```bash
rostopic echo /mavros/mavlink/to
```
通过判据：
- 存在 `msgid=251` (`NAMED_VALUE_FLOAT`) 输出
- 当前应有：`USV_VOLT`、`USV_ABS`、`PUMP_X/Y/Z/A`、`USV_STAT`
- 当 `/usv/spectrometer_voltage` 有 JSON 数据时，`USV_VOLT` 应同步变化
- 当分光数据变化时，`USV_ABS` 应同步变化

## 9. 热点访问测试
### 9.1 代码核查结论
- `web_config_server.py` **没有**实装“创建热点”的逻辑。
- Web 节点只负责监听 `0.0.0.0:5000` 并提供页面/API。
- 热点创建由独立脚本 `src/usv_ros/scripts/setup_hotspot.sh` 负责，关闭由 `src/usv_ros/scripts/stop_hotspot.sh` 负责，底层依赖 `nmcli`。
- 因此：**可以通过热点访问 Web 页，但热点功能不在 Web 节点内部实现。**

### 9.2 为什么可能会看到“热点要求输入密码”
- 旧脚本使用 `nmcli dev wifi hotspot ...`。
- 在很多 NetworkManager 版本中，这条命令会默认创建带 WPA/WPA2 安全配置的热点，而不是严格意义上的开放热点。
- 在当前 Jetson Nano / NetworkManager 环境中，开放热点配置还会触发 `802-11-wireless-security.key-mgmt: property is missing` 兼容性错误。
- 因此当前脚本已调整为：**默认创建 WPA-PSK 热点**，优先保证现场可连接、可复现。

### 9.3 热点创建与访问测试
```bash
cd ~/usv_ws/src/usv_ros/scripts
sudo ./setup_hotspot.sh USV_Control 12345678
```
说明：
- 脚本会在启动热点前记录 `wlan0` 当前活动 WiFi 连接名。
- 记录文件路径：`~/usv_ws/.usv_run/previous_wifi_connection`
- 关闭热点时，`stop_hotspot.sh` 会尝试自动回连该连接。

客户端连接热点后访问：
- `http://10.42.0.1:5000`

建议附加检查：
```bash
nmcli -f GENERAL.NAME,802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless-security.key-mgmt con show USV_AP
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
curl http://10.42.0.1:5000/api/ui/debug
```

通过判据：
- 手机/电脑能连接 `USV_Control`
- `nmcli ... con show USV_AP` 中 `key-mgmt=wpa-psk`
- `status_usv_all.sh` 显示 `conn=active ip=assigned web_port=listening`
- `curl http://10.42.0.1:5000/api/ui/debug` 返回 JSON
- 浏览器能打开 Web 配置页

### 9.4 一键关闭热点
```bash
cd ~/usv_ws/src/usv_ros/scripts
sudo ./stop_hotspot.sh
```
通过判据：
- `nmcli con show --active` 中不再出现 `USV_AP`
- 若热点前存在活动 WiFi，则自动尝试回连该连接
- `status_usv_all.sh` 不再显示 `conn=active`

## 10. 停止与重启测试
```bash
./src/usv_ros/scripts/stop_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
./src/usv_ros/scripts/restart_usv_all.sh
```
通过判据：
- 停止后显示 `STOPPED`
- 重启后重新显示 `RUNNING`

## 11. 新采集架构验证
本章用于验证当前已部署到船载电脑上的新架构是否按设计工作：
- `pump_control_node.py` 同时负责泵控与 ADS 分光串口桥接
- `/usv/spectrometer_voltage` 为 `std_msgs/String`
- 载荷格式为 JSON，而不是旧的 `Float64`
- `web_config_server.py` 与 `usv_mavlink_bridge.py` 均消费新的 JSON 数据

推荐按“先本地 ROS 话题，再 Web，再 MAVLink”的顺序验证。

### 11.1 节点与话题基线检查
```bash
rosnode list
rostopic list | grep spectrometer
rosservice list | grep /usv/
```
通过判据：
- 存在 `/pump_control_node`、`/web_config_server`、`/usv_mavlink_bridge`
- 存在话题：`/usv/spectrometer_voltage`、`/usv/spectrometer_status`、`/usv/spectrometer_raw`、`/usv/spectrometer_absorbance`
- 存在服务：`/usv/spectrometer_start`、`/usv/spectrometer_stop`、`/usv/pump_reconnect`
- 不应再依赖独立分光节点

### 11.2 分光话题格式验证
先观察状态与数据：
```bash
rostopic echo /usv/spectrometer_status
rostopic echo /usv/spectrometer_voltage
rostopic echo /usv/spectrometer_raw
rostopic echo /usv/spectrometer_absorbance
```
通过判据：
- `/usv/spectrometer_status` 可见 `idle`、`acquiring`、`i2c_error`、`not_configured`、`saturated` 等状态
- `/usv/spectrometer_voltage` 的 `data` 字段是 JSON 字符串，至少包含 `voltage`、`timestamp_ms`、`tca_channel`
- `/usv/spectrometer_raw` 的 `data` 字段是 JSON 字符串，包含原始采样内容
- `/usv/spectrometer_absorbance` 的 `data` 字段是 JSON 字符串，至少包含 `absorbance`
- 不应出现旧 `Float64` 电压载荷验证步骤

### 11.3 启停验证
```bash
rosservice call /usv/spectrometer_start
sleep 3
rosservice call /usv/spectrometer_stop
sleep 2
rosservice call /usv/spectrometer_start
```
通过判据：
- 服务调用返回成功
- 启动后 `/usv/spectrometer_status` 进入 `acquiring` 或持续输出有效状态
- 停止后状态回到 `idle` 或停止采样更新
- 重新启动后可再次恢复数据发布

### 11.4 Web 侧联调验证
浏览器打开 `http://<Jetson-IP>:5000`，进入 Monitor 与 Settings 页面。

在终端可同时观察：
```bash
curl http://127.0.0.1:5000/api/data/voltage
curl http://127.0.0.1:5000/api/hardware/config
curl http://127.0.0.1:5000/api/hardware/serial-ports
```
通过判据：
- Monitor 页面有电压/吸光度更新，不是固定 0 或空白
- `/api/data/voltage` 返回 `value`、`absorbance`、`status`、`raw`
- Settings 页面能看到“硬件连接设置”卡片，且仅包含当前实现的串口配置项
- “刷新设备”“测试泵控连接”“仅保存”“保存并应用”均可点击

### 11.5 MAVLink 桥接联动验证
```bash
rostopic echo /usv/spectrometer_voltage
rostopic echo /mavros/mavlink/to
```
通过判据：
- 当 `/usv/spectrometer_voltage` 中 JSON 的 `voltage` 变化时，MAVLink 上行中的 `USV_VOLT` 应同步变化
- 当分光数据计算出吸光度变化时，`USV_ABS` 应同步变化
- `usv_mavlink_bridge.py` 不应因 JSON 解析失败而频繁报错

### 11.6 硬件连接设置测试
#### 11.6.1 Web 页面入口
在浏览器打开 Settings 页面，应看到第三张卡片“硬件连接设置”。

页面动作应包括：
- `刷新设备`
- `测试泵控连接`
- `仅保存`
- `保存并应用`

#### 11.6.2 设备枚举
点击“刷新设备”按钮：
```bash
curl http://127.0.0.1:5000/api/hardware/serial-ports
```
通过判据：
- 串口列表返回 `success=true`，`ports` 数组包含当前已插入的 USB 串口
- 若存在 `/dev/serial/by-id`，返回项应优先给出 `by_id`

#### 11.6.3 硬件配置读写
```bash
curl http://127.0.0.1:5000/api/hardware/config
curl -X POST http://127.0.0.1:5000/api/hardware/config \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB1","pump_baudrate":115200,"pump_timeout":1.0,"spectrometer_auto_start":false}'
```
通过判据：
- GET 返回当前硬件配置 JSON
- POST 返回 `success=true`
- 再次 GET 可读到更新后的 `hardware` 配置段
- `POST /api/hardware/config` 不应触发运行中节点重连

#### 11.6.4 连接测试
```bash
curl -X POST http://127.0.0.1:5000/api/hardware/test-pump-port \
  -H "Content-Type: application/json" \
  -d '{"serial_port":"/dev/ttyUSB0","baudrate":115200,"timeout":1.0}'
```
通过判据：
- 正确串口返回 `success=true`
- 错误串口返回 `success=false` 并附错误信息

#### 11.6.5 保存并应用（运行时热切换）
```bash
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```
通过判据：
- HTTP 返回 `success=true`
- `results.pump.success=true` 表示泵控节点已重连
- `pump_control_node.py` 日志出现 `Reconnecting:` 或 `Reconnected to`
- 错误时返回失败信息但 Web 仍可访问、节点进程不退出

#### 11.6.6 边界确认
- `pump_serial_port`、`pump_baudrate`、`pump_timeout`：当前支持运行时热切换
- `spectrometer_auto_start`：当前已在默认配置与前端数据模型中存在，但 `POST /api/hardware/apply` 不会运行时下发；若要验证其效果，应重启 `pump_control_node` 或整套系统

## 12. 现场 QGC / Jetson / ROS 联调检查清单
本章用于现场按固定顺序确认 `QGC -> 飞控 -> MAVROS -> Jetson ROS -> 载荷装置 -> QGC` 是否已闭环。

推荐按“先飞控参数与物理链路，再 Jetson 进程，再 ROS / MAVROS 话题，最后 QGC 面板操作”的顺序执行。

### 12.1 飞控参数与物理连接确认
在 QGC 中确认以下参数：
- `SERIAL2_PROTOCOL = 2`（MAVLink2；若现场使用 MAVLink1，则至少必须是 MAVLink 协议，而不是 GPS/RCIN 等）
- `SERIAL2_BAUD = 921`（921600 bps）
- `SYSID_THISMAV = 1`

现场硬件应满足：
- Jetson Nano `TX/RX/GND` 已连接飞控 `TELEM2`
- Jetson 侧使用串口 `/dev/ttyTHS1`
- 飞控固件为 ArduRover `V4.4.0`

通过判据：
- QGC 参数页保存成功，无自动回滚
- Jetson 与飞控接线牢固，无串口复用冲突

### 12.2 Jetson 侧启动与日志窗口
建议现场开 4 个 Jetson 终端。

**终端 A：启动系统**
```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
```

**终端 B：查看整体状态**
```bash
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
```

**终端 C：查看主日志**
```bash
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
```

**终端 D：加载 ROS 环境备用**
```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
source devel/setup.bash
```

通过判据：
- `status_usv_all.sh` 显示 `roscore: RUNNING`
- `status_usv_all.sh` 显示 `usv_system: RUNNING`
- 日志中未出现 `ModuleNotFoundError: No module named 'serial'`
- 日志中未出现 Web 节点立即退出或 launch 立即退出

### 12.3 ROS 节点与 MAVROS 基线检查
在终端 D 中执行：
```bash
rosnode list
rosservice list | grep /usv/
rostopic echo -n 1 /mavros/state
```

期望至少看到：
- `/pump_control_node`
- `/web_config_server`
- `/mavlink_trigger_node`
- `/usv_mavlink_bridge`
- `/mavros`

通过判据：
- `/mavros/state` 中 `connected: True`
- `/usv/automation_start` `/usv/automation_stop` `/usv/pump_reconnect` 等服务存在
- 若 `/mavros` 不存在或 `connected: False`，先停止后续 QGC 联调，优先检查 TELEM2 参数和串口占用

### 12.4 MAVLink 下行链路检查（QGC -> 飞控 -> Jetson）
在 Jetson 终端 D 中分别执行：
```bash
rostopic echo /usv/trigger_status
```

另开一个 Jetson 终端执行：
```bash
source /opt/ros/noetic/setup.bash
source ~/usv_ws/devel/setup.bash
rostopic echo /mavros/mavlink/from
```

然后在 QGC 中打开载荷面板，依次点击：
- `开始采样`
- `暂停`
- `恢复`
- `停止`

通过判据：
- `/mavros/mavlink/from` 能看到 `msgid: 76`（`COMMAND_LONG`）
- `/usv/trigger_status` 出现 `sampling_started`、`sampling_paused`、`sampling_resumed`、`sampling_stopped`
- 若点击 QGC 后 `/mavros/mavlink/from` 无变化，优先检查飞控 `SERIAL2_PROTOCOL`、QGC 是否已连上飞控、以及 Jetson 侧 MAVROS 是否真的连到 `/dev/ttyTHS1:921600`

### 12.5 MAVLink 上行链路检查（Jetson -> 飞控 -> QGC）
在 Jetson 终端执行：
```bash
source /opt/ros/noetic/setup.bash
source ~/usv_ws/devel/setup.bash
rostopic echo /mavros/mavlink/to
```

重点观察：
- `msgid: 0`（`HEARTBEAT`）
- `msgid: 77`（`COMMAND_ACK`）
- `msgid: 251`（`NAMED_VALUE_FLOAT`）

通过判据：
- 可见来自 `sysid=2 compid=191` 的 `HEARTBEAT`
- 点击 QGC 按钮后可见 `COMMAND_ACK`
- 持续可见 `NAMED_VALUE_FLOAT`，且包含 `USV_VOLT` `USV_ABS` `PUMP_X/Y/Z/A` `USV_STAT` `USV_PKT`

### 12.6 载荷 ROS 数据源检查
在 Jetson 终端执行：
```bash
source /opt/ros/noetic/setup.bash
source ~/usv_ws/devel/setup.bash
rostopic echo /usv/pump_status
rostopic echo /usv/pump_angles
rostopic echo /usv/spectrometer_voltage
```

通过判据：
- `/usv/pump_status` 有 automation 或设备状态变化
- `/usv/pump_angles` 持续刷新，不是长期固定空值
- `/usv/spectrometer_voltage` 的 `data` 是 JSON 字符串，包含 `voltage`，有分光数据时还应包含 `absorbance`

### 12.7 QGC 侧面板检查
在 QGC 载荷面板中观察：
- “数据链路超时”提示是否消失
- 电压、吸光度、泵角度是否刷新
- `USV_PKT` 对应的链路包计数是否持续增长
- 点击按钮后界面状态是否切换为“正在采样 / 故障 / 空闲”等

通过判据：
- 面板不再长期显示无数据
- `USV_PKT` 持续增加，说明上行遥测在刷新
- 即使短时出现“载荷遥测超时”，按钮仍可尝试下发命令，不应出现整块面板完全锁死

### 12.8 一次完整现场联调顺序
建议现场按以下顺序逐项执行：

1. 在 QGC 中确认 `SERIAL2_PROTOCOL` 与 `SERIAL2_BAUD`
2. 在 Jetson 执行：
```bash
cd ~/usv_ws
./src/usv_ros/scripts/restart_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
```
3. 在 Jetson 确认 MAVROS 已连通：
```bash
source /opt/ros/noetic/setup.bash
source ~/usv_ws/devel/setup.bash
rostopic echo -n 1 /mavros/state
```
4. 在 Jetson 观察 MAVLink 下行：
```bash
rostopic echo /mavros/mavlink/from
```
5. 在 QGC 点击“开始采样”，确认 Jetson 收到 `COMMAND_LONG`
6. 在 Jetson 观察 MAVLink 上行：
```bash
rostopic echo /mavros/mavlink/to
```
7. 在 QGC 确认面板数据刷新、`USV_PKT` 增长、状态切换正常
8. 在 Jetson 观察业务话题：
```bash
rostopic echo /usv/trigger_status
rostopic echo /usv/pump_status
```
9. 完成暂停 / 恢复 / 停止三组动作验证

### 12.9 现场常见定位结论
- `QGC 点按钮无反应` + `/mavros/mavlink/from` 无数据：优先检查飞控串口参数、QGC 与飞控连接状态、Jetson 侧 MAVROS
- `/mavros/mavlink/from` 有 `COMMAND_LONG` + `/usv/trigger_status` 无变化：优先检查 `mavlink_trigger_node.py` 是否在线
- `/mavros/mavlink/to` 有 `NAMED_VALUE_FLOAT` + QGC 无数据显示：优先检查 QGC 当前是否运行工作区内定制版本
- `/usv/spectrometer_voltage` 无数据：优先检查泵控/分光硬件链路，而不是 QGC
- `/mavros/state` 为 `connected: False`：先修 MAVROS，再做其余联调

## 13. 故障排查
- `serial` 模块缺失：安装 `pyserial`
- Web 启动即退出：检查 `usv_system.log` 中 Werkzeug 报错
- 5000 端口不通：检查 `ss -tuln | grep 5000`
- 分光话题无数据：检查 ESP32 串口桥接是否在线，检查 `/usv/spectrometer_status` 与 `usv_system.log`
- `/usv/spectrometer_voltage` 不是 JSON：确认船载电脑已拉取到当前版本，而不是旧版 `Float64` 架构
- 串口测试失败：检查设备路径、用户组权限、串口占用
- 保存并应用失败：检查 `/usv/pump_reconnect` 是否存在，检查 `results.*.message`
- 热点无法创建：检查 `nmcli`、`wlan0` 是否支持 AP 模式
- 热点仍要求密码：先执行 `sudo ./stop_hotspot.sh`，再执行 `sudo ./setup_hotspot.sh USV_Control`，并检查 `nmcli con show USV_AP`
- MAVROS 无数据：检查 `/mavros/state` 与飞控串口


