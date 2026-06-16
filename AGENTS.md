# usv_ros Agent Knowledge Base

Generated: 2026-06-15
Branch: codex/web-mission-upload
Commit: 3f8fdb62

## OVERVIEW

Jetson Nano / Ubuntu 20.04 / ROS Noetic 载荷仓库，负责检测装置控制、Web 控制台、MAVROS、mavlink-router bridge 和采样任务编排。

## STRUCTURE

```text
usv_ros/
├── launch/usv_bringup.launch          # 默认启动入口
├── config/usv_params.yaml             # ROS 参数基线
├── scripts/
│   ├── usv_mavlink_router_bridge.py   # router TCP、遥测、命令、ACK、USV_DONE
│   ├── mavlink_trigger_node.py        # 31010..31019 与采样状态机
│   ├── pump_control_node.py           # ESP32 串口、自动化、分光、泵控
│   ├── system_health_node.py          # Jetson/ROS/ESP32 健康聚合
│   └── web_config_server.py           # Flask/Socket.IO/API/日志/数据
├── scripts/map_resources/             # 离线地图包与瓦片缓存工具
├── frontend/                          # React/Vite Web 控制台
├── static/dist/                       # 前端构建产物
└── tests/                             # unittest/pytest 兼容测试
```

## WHERE TO LOOK

| 任务 | 位置 |
|---|---|
| ROS 节点拓扑/参数 | `launch/usv_bringup.launch` |
| router/MAVROS 启停 | `scripts/common_env.sh`、`start_usv_all.sh`、`status_usv_all.sh` |
| MAVLink 载荷遥测 | `scripts/usv_mavlink_router_bridge.py` |
| 手动命令/航点采样 | `scripts/mavlink_trigger_node.py` |
| 检测装置串口协议 | `scripts/pump_control_node.py` + `../../DetFirmware/src/main.cpp` |
| Web API/Socket.IO | `scripts/web_config_server.py` |
| 污染物地图/IDW/GeoJSON | `web_config_server.py` + `frontend/src/pages/Map.tsx` |
| 系统健康 | `scripts/system_health_node.py` + `frontend/src/components/system-health-card.tsx` |

## CONVENTIONS

- MAVLink 变更先查 `../../ardupilot-usv/Rover/`，再改 ROS。
- `COMMAND_LONG 31010..31019` 只用于手动载荷控制；航线定点采样使用 `MAV_CMD_NAV_SCRIPT_TIME(param1=1)`。
- router 独占飞控串口；MAVROS 使用 UDP `127.0.0.1:14550`，自定义 bridge 使用 TCP `127.0.0.1:5760`。
- Web 数据中心跟随 `sampling_started`/`sampling_stopped`/`survey_stopped` 生命周期建档和停止。
- 前端构建输出写入 `static/dist/`，由 Flask 直接提供。
- 污染物浓度、采样点质量、GeoJSON/CSV、IDW surface 均归 ROS/Web。

## ANTI-PATTERNS

- 让 QGC 直连 ROS 作为稳定链路假设。
- 在 ROS 里绕开固件约定新增 MAVLink 字段名。
- 修改串口协议只改 ROS，不同步核对 DetFirmware。
- 系统运行时让 `usvupdate/usvbuild` 绕过拒绝语义。

## COMMANDS

```bash
python3 -m py_compile scripts/*.py scripts/map_resources/*.py
python3 -m unittest discover -s tests -p 'test_*.py'
cd frontend && npm run build
```

## NOTES

- 默认现场入口：`scripts/start_usv_all.sh`，诊断入口：`scripts/status_usv_all.sh`。
- `mission_coordinator_node.py` 保留但不在默认 launch 主链路。
- `USV_DONE` 由 bridge 发给固件；`USV_SMPL` 由固件触发 ROS 定点采样。
