# 项目概览
Updated: 2026-04-11T13:00:00Z

## 1. 范围
- 地面端：`WQ-USV-QGroundControl/`
- 船载端：`src/usv_ros/`
- 飞控端：`ardupilot-usv/`
- 当前文档目录：`docs/current/`

## 2. 当前系统结构
- QGC 自定义层发送 `COMMAND_LONG` 指令并显示载荷遥测。
- ROS Noetic 运行泵控、分光采集、Web 服务、MAVLink 指令接收与载荷遥测发送。
- ArduRover 固件接收来自伴随计算机的 `NAMED_VALUE_FLOAT`，缓存后以 2Hz 周期性转发到 GCS（直转发已被 MAVLink_routing 阻断）。
- `mavlink-routerd` 独占 `/dev/ttyTHS1`，为 MAVROS（UDP:14550）与载荷遥测桥（TCP:5760）提供物理隔离的 MAVLink 路由。

## 3. 当前数据链路
### 3.1 QGC -> ROS（下行指令）
`QGC -> 数传电台 -> 飞控 -> mavlink-routerd -> usv_mavlink_router_bridge.py -> /usv/mavlink_cmd_rx -> mavlink_trigger_node.py`

### 3.2 ROS -> QGC（上行遥测 — 飞控中继模式）
```
bridge(sysid=1/compid=191) -[2Hz×8字段]-> mavlink-routerd -[UART]-> 飞控
  飞控: handle_message 缓存到 usv_payload（MAVLink_routing 阻断直转发）
  飞控: usv_telemetry_send() 以 2Hz 重发 gcs().send_named_float()
  飞控 -[TELEM1]-> 数传电台 -> QGC USVPayloadFactGroup
```

### 3.3 端点隔离
| 端点 | 协议 | 用途 |
|---|---|---|
| MAVROS | UDP 127.0.0.1:14550 | 参数下载、状态监控、set_mode、COMMAND_ACK |
| bridge | TCP 127.0.0.1:5760 | NAMED_VALUE_FLOAT 遥测、COMMAND_LONG 接收 |
| UART | /dev/ttyTHS1:921600 | 飞控物理链路 |

## 4. 当前已实现能力
- `mavlink_trigger_node.py` 处理 `31010~31014`，并回传 `COMMAND_ACK`。
- `mavlink_trigger_node.py` 将 `31014` 转换为 `CALXYZA\r\n` 并发布到 `/usv/pump_command`。
- `usv_mavlink_router_bridge.py` 通过 `pymavlink` 向 `mavlink-routerd` 发送 `USV_VOLT`、`USV_ABS`、`PUMP_X/Y/Z/A`、`USV_STAT`、`USV_PKT`（线程安全队列模式）。
- `start_usv_all.sh`、`stop_usv_all.sh`、`status_usv_all.sh`（含 ROS 节点级检查 + MAVROS 连通检查 + bridge 诊断摘要）。
- `USVPayloadFactGroup` 解析 `NAMED_VALUE_FLOAT`、`DEBUG_VECT`、`DEBUG`，维护 `linkActive`（5s 超时）、`packetCount`、诊断计数。
- ArduRover 固件 `GCS_MAVLink_Rover.cpp` 缓存 `NAMED_VALUE_FLOAT`；`sensors.cpp` 以 2Hz 受控重发；`MAVLink_routing.cpp` 阻断直转发。
- `web_config_server.py` 提供硬件配置 API、任务控制 API、进样泵 API、Socket.IO 状态推送。
- `pump_control_node.py` 提供四路步进泵、进样泵、自动化步骤执行、分光采集。
- QGC `USVPayloadPanel.qml` 命令发送 `showError=true`，命令超时/拒绝时弹出 toast。

## 5. 当前运行约束
- `mavlink-routerd` 为必需依赖；未包含自动安装逻辑。
- MAVROS 默认连接 `udp://127.0.0.1:14550@`；bridge 默认连接 `tcp:127.0.0.1:5760`。
- `mission_coordinator_node.py` 未包含在默认 launch 主链路。

## 6. 稳定版本标签
| 仓库 | 标签 | commit | 说明 |
|---|---|---|---|
| `ardupilot-usv` | `v0.2.0-stable` | `56741bb0fa` | 含 NAMED_VALUE_FLOAT 路由阻断 + 2Hz 重发 |
| `src/usv_ros` | `v0.2.0-stable` | `63ae83ec` | 含线程安全队列 + MAVROS UDP 隔离 + 遥测解耦 |
| `WQ-USV-QGroundControl` | `v0.2.0-stable` | `af6c56478` | 含 showError=true + UI 优化 |

回滚命令：
```bash
cd ~/usv_ws/src/usv_ros && git checkout v0.2.0-stable
cd ~/usv_ws/ardupilot-usv && git checkout v0.2.0-stable
cd ~/usv_ws/WQ-USV-QGroundControl && git checkout v0.2.0-stable
```

## 7. 文档入口
- 运维入口：`src/usv_ros/README.md`
- 测试入口：`src/usv_ros/TESTING.md`
- 接口速查：`docs/current/INTERFACE.md`
- 固件说明：`docs/current/ardupilot_firmware_guide.md`
- 技术计划：`docs/current/plan.md`
- 任务记录：`docs/current/task.md`
- 后续规划：`docs/current/roadmap.md`
