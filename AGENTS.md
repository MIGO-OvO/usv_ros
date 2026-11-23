# usv_ros 开发约定

- Jetson 启动、停止、更新与诊断查 [README](README.md)。router 独占飞控串口，供 MAVROS 与载荷 bridge 分别接入。维护 `usvupdate` 和 `usvbuild` 时保留主系统运行期间的拒绝逻辑。
- 改 MAVLink 前核对同工作区 `../../ardupilot-usv/Rover/`。手动载荷命令走 `31010..31019`，航点采样由飞控以 `31021` 与 ROS 交互。
- 载荷重连按请求、会话和执行身份恢复原结果。断链停机按任务归属处理，禁止重放旧的物理动作。改采样建档或关闭逻辑时核对[自动化终态说明](docs/automation-terminal-reasons.md)。
- 串口协议同时核对 `scripts/pump_control_node.py`、DetFirmware 和 Windows 上位机。污染物浓度、数据记录和地图归 ROS/Web。
- Web API 与 Socket.IO 以 `scripts/web_config_server.py` 为准。前端改动按[前端开发指南](frontend/README.md)构建到 `static/dist/`。
- 按 [TESTING.md](TESTING.md) 运行改动相关的测试，分别记录离线结果和 Jetson／台架验证。
