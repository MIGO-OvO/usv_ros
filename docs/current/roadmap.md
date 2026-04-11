# 后续开发规划
Updated: 2026-04-11T13:00:00Z

> 基于 v0.2.0-stable（三端通信链路稳定版本）的后续工作规划。

## 优先级说明
- **P0**：影响核心功能或安全性，应尽快完成
- **P1**：显著提升使用体验或运维效率
- **P2**：锦上添花，可按需排期

---

## P0 — 可靠性与安全性

### 1. stop 脚本安全加固
**现状**：`stop_usv_all.sh` 按 PID 文件 + `kill -0` 停进程，不校验 PID 对应的命令行。异常退出后 PID 残留可能导致误杀无关进程（包括 VSCode Remote SSH）。
**目标**：
- `stop_pid_file()` 增加 `/proc/$pid/cmdline` 校验
- `cleanup_port_process()` 增加进程名过滤
- 文件：`src/usv_ros/scripts/common_env.sh`

### 2. MAVROS 参数下载完成后再发起采样
**现状**：MAVROS 启动后参数下载需要 1-3 分钟，期间链路负载较高。用户如果在参数下载完成前点击采样，可能出现延迟。
**目标**：
- `mavlink_trigger_node.py` 启动时等待 `/mavros/param/param_value` 话题稳定
- 或在 `status_usv_all.sh` 中增加参数下载完成状态检测

### 3. 开机自启动
**现状**：每次 Jetson 重启需手动运行 `start_usv_all.sh`。
**目标**：
- 添加 systemd service 文件
- 支持 `sudo systemctl enable usv-ros`
- 文件：`src/usv_ros/scripts/usv-ros.service`

---

## P1 — 功能增强

### 4. 航点自动采样闭环
**现状**：`mavlink_trigger_node.py` 已支持航点到达自动触发采样，但 `set_mode("AUTO")` 在无任务时会被飞控拒绝。
**目标**：
- 采样完成后检查是否有后续航点再切 AUTO
- 无任务时切 HOLD 并等待新指令
- 文件：`src/usv_ros/scripts/mavlink_trigger_node.py`

### 5. 采样数据自动存储与导出
**现状**：分光数据通过 Socket.IO 实时推送到 Web 前端，但无持久化存储。
**目标**：
- 每次采样任务结果保存为 CSV/JSON 文件（含时间戳、GPS 坐标、吸光度、电压）
- Web 端增加"历史数据"页面和导出按钮
- 文件：`pump_control_node.py`、`web_config_server.py`、`frontend/src/pages/`

### 6. QGC 载荷面板数据图表
**现状**：QGC 面板仅显示实时数值。
**目标**：
- 在 `USVPayloadPanel.qml` 或详情页增加吸光度/电压的时间序列趋势图
- 利用 QGC 内置的 `LineSeries` 组件
- 文件：`WQ-USV-QGroundControl/custom/res/`

### 7. 多点采样任务编排
**现状**：采样参数通过 Web 配置，但只支持单次采样序列。
**目标**：
- Web 端支持"采样任务列表"：每个航点关联不同的采样配置
- 采样参数与航点序号绑定
- 文件：`web_config_server.py`、`frontend/`、`mavlink_trigger_node.py`

---

## P2 — 运维与工程化

### 8. 数传电台链路质量监控
**现状**：QGC 有 RADIO_STATUS 但未在载荷面板集成。
**目标**：
- 在 Web 或 QGC 面板显示 RSSI、noise、remrssi 等电台质量指标
- 低信号时在 QGC 面板提示警告

### 9. 固件 OTA 或半自动更新
**现状**：刷固件需要手动 WSL 编译 + USB/网络上传。
**目标**：
- 提供预编译固件二进制 + 版本标记
- 通过 Web 端或 QGC 上传刷入

### 10. 日志集中与远程查看
**现状**：ROS 日志在 Jetson 本地，需 SSH 查看。
**目标**：
- `web_config_server.py` 增加日志流式 API（最近 N 行 + WebSocket 实时推送）
- Web 端增加"系统日志"标签页

### 11. 水质数据可视化大屏
**现状**：数据仅在 Web 配置页和 QGC 面板显示。
**目标**：
- 独立的数据展示页面（地图 + 采样点 + 趋势图）
- 支持手机/平板访问
- 文件：`frontend/src/pages/Dashboard.tsx`

---

## 版本里程碑建议

| 版本 | 内容 | 目标时间 |
|---|---|---|
| v0.2.0 | ✅ 三端通信稳定（当前） | 已完成 |
| v0.3.0 | P0 全部 + P1 #4 #5 | 2-3 周 |
| v0.4.0 | P1 #6 #7 | 4-6 周 |
| v1.0.0 | 全部 P0/P1 + 实船验收 | 8-10 周 |
