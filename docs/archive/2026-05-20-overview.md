# 项目概览
Updated: 2026-05-20T00:00:00+08:00

## 1. 范围
- 地面站端：`WQ-USV-QGroundControl/`
- 船载 ROS 端：`src/usv_ros/`
- 飞控端：`ardupilot-usv/`
- 当前文档目录：`docs/current/`

## 2. 当前系统结构
- QGC 定制层通过 `COMMAND_LONG` 下发 USV 载荷命令，并通过 `NAMED_VALUE_FLOAT` 显示载荷遥测。
- ROS Noetic 运行泵控、分光采集、Web 服务、MAVLink 命令接收与载荷遥测发送。
- ArduRover 缓存来自伴随计算机的 `NAMED_VALUE_FLOAT`，再以 2Hz 周期转发到 GCS，避免被 MAVLink routing 直转发规则阻断。
- `mavlink-routerd` 独占 `/dev/ttyTHS1`，为 MAVROS (`UDP:14550`) 与载荷桥 (`TCP:5760`) 提供独立 MAVLink 路由。

## 3. 当前数据链路
### 3.1 QGC -> ROS 下行指令
`QGC -> 数传电台 -> 飞控 -> mavlink-routerd -> usv_mavlink_router_bridge.py -> /usv/mavlink_cmd_rx -> mavlink_trigger_node.py`

### 3.2 ROS -> QGC 上行遥测
```text
bridge(sysid=1/compid=191) -[2Hz x 17 NAMED_VALUE_FLOAT]-> mavlink-routerd -> 飞控
飞控: handle_message 缓存到 usv_payload
飞控: usv_telemetry_send() 2Hz 重发 gcs().send_named_float()
飞控 -> 数传电台 -> QGC USVPayloadFactGroup
```

## 4. 当前能力
- `mavlink_trigger_node.py` 处理 `31010..31019` 并回传 `COMMAND_ACK`。
- `31018/31019` 调用 `/usv/spectrometer_start`、`/usv/spectrometer_stop`，用于 QGC 明确启停检测器信号采集。
- `31017` 使用最新有效分光电压设置 baseline；`31010` 保留为点采样/自动采样流程。
- `usv_mavlink_router_bridge.py` 发送 17 个载荷遥测字段：`USV_VOLT`、`USV_ABS`、`PUMP_X/Y/Z/A`、`USV_STAT`、`USV_PKT`、`USV_STEP`、`USV_STOT`、`USV_SCNT`、`USV_PERR`、`USV_PMOD`、`USV_BSET`、`USV_REF`、`USV_BASE`、`USV_VLD`。
- QGC `USVPayloadPanel.qml` 使用“启动信号采集 -> 设基线 -> 开始点采样/走航”的流程；命令发送 `showError=false`，避免把流程失败误报成载荷硬件故障。

## 5. 运行约束
- `mavlink-routerd` 为必需依赖。
- MAVROS 默认连接 `udp://127.0.0.1:14550@`；bridge 默认连接 `tcp:127.0.0.1:5760`。
- `mission_coordinator_node.py` 保留在仓库中，但不在默认 launch 主链路内。

## 6. 文档入口
- 运维入口：`src/usv_ros/README.md`
- 测试入口：`src/usv_ros/TESTING.md`
- 接口速查：`docs/current/INTERFACE.md`
- 固件说明：`docs/current/ardupilot_firmware_guide.md`
