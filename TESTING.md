# USV ROS 系统功能测试手册
Updated: 2026-03-15T02:00:00Z

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
- 当前应有：`USV_VOLT`、`PUMP_X/Y/Z/A`、`USV_STAT`
- 当前未实现：`USV_ABS`

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

## 11. 硬件连接设置测试
### 11.1 Web 页面入口
在浏览器打开 Settings 页面，应看到第三张卡片“硬件连接设置”。

页面动作应包括：
- `刷新设备`
- `测试泵控连接`
- `仅保存`
- `保存并应用`

### 11.2 设备枚举
点击“刷新设备”按钮：
```bash
curl http://127.0.0.1:5000/api/hardware/serial-ports
```
通过判据：
- 串口列表返回 `success=true`，`ports` 数组包含当前已插入的 USB 串口
- 若存在 `/dev/serial/by-id`，返回项应优先给出 `by_id`

### 11.3 硬件配置读写
```bash
curl http://127.0.0.1:5000/api/hardware/config
curl -X POST http://127.0.0.1:5000/api/hardware/config \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB1","pump_baudrate":115200}'
```
通过判据：
- GET 返回当前硬件配置 JSON
- POST 返回 `success=true`
- 再次 GET 可读到更新后的 `hardware` 配置段
- `POST /api/hardware/config` 不应触发运行中节点重连

### 11.4 连接测试
```bash
curl -X POST http://127.0.0.1:5000/api/hardware/test-pump-port \
  -H "Content-Type: application/json" \
  -d '{"serial_port":"/dev/ttyUSB0","baudrate":115200,"timeout":1.0}'
```
通过判据：
- 正确串口返回 `success=true`
- 错误串口返回 `success=false` 并附错误信息

### 11.5 保存并应用（运行时热切换）
```bash
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'

rosservice call /usv/pump_reconnect
```
通过判据：
- HTTP 返回 `success=true`
- `results.pump.success=true` 表示泵控节点已重连
- `pump_control_node.py` 日志出现 `Reconnecting:` 或 `Reconnected to`
- 错误时返回失败信息但 Web 仍可访问、节点进程不退出

### 11.6 边界确认
- `pump_serial_port`、`pump_baudrate`、`pump_timeout`：当前支持运行时热切换
- `spectrometer_auto_start`：当前已在默认配置与前端数据模型中存在，但 `POST /api/hardware/apply` 未运行时下发该字段；若要验证其效果，应重启 `pump_control_node` 或整套系统

## 12. 故障排查
- `serial` 模块缺失：安装 `pyserial`
- Web 启动即退出：检查 `usv_system.log` 中 Werkzeug 报错
- 5000 端口不通：检查 `ss -tuln | grep 5000`
- 串口测试失败：检查设备路径、用户组权限、串口占用
- 保存并应用失败：检查 `/usv/pump_reconnect` 是否存在，检查 `results.*.message`
- 热点无法创建：检查 `nmcli`、`wlan0` 是否支持 AP 模式
- 热点仍要求密码：先执行 `sudo ./stop_hotspot.sh`，再执行 `sudo ./setup_hotspot.sh USV_Control`，并检查 `nmcli con show USV_AP`
- MAVROS 无数据：检查 `/mavros/state` 与飞控串口

