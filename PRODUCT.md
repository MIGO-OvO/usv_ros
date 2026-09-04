# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

船载系统开发者、现场调试人员和比赛/实验操作员在 Jetson Web 控制台中使用该产品。他们通常处在船端联调、岸边测试或实验室复现环境，需要快速判断 ROS、MAVLink、检测装置和水质采样流程是否可靠运行。

## Product Purpose

`usv_ros` Web 控制台是水质监测无人船的船载任务工具，用于实时监控传感器与链路状态、控制检测装置、配置自动化采样、查看任务数据、规划 Web 侧实验航线和诊断现场问题。成功的界面应让用户在有限屏幕、有限网络和高压力调试场景下快速定位状态、执行命令并确认结果。

## Positioning

`usv_ros` 是水质监测无人船的船载载荷集成层：它在 Jetson 上把 ESP32 检测装置、ROS 采样自动化、MAVROS/自定义 MAVLink 桥接，以及可部署的 Web 控制台组织为一套可联调、可运行、可追溯的系统。它不替代飞控固件或 QGC，而负责其间的载荷控制、状态汇聚与业务数据工作流。

## Operating Context

目标运行环境为 Jetson Nano、Ubuntu 20.04 和 ROS Noetic。操作员通过局域网、船载热点或本机浏览器访问 Web 控制台，在有限网络和高压力调试条件下完成实时监控、手动控制、自动化采样、航点采样配置、任务数据查看、污染物地图与实验航线操作。

## Capabilities and Constraints

- 控制 ESP32 检测装置主控，包括 X/Y/Z/A 四路步进泵、进样泵 PWM、角度流和 ADS 分光采样。
- 通过 Flask + Socket.IO 提供实时状态、REST API 和 React/Vite Web 控制台；前端构建产物位于 `static/dist/`。
- 管理多步骤采样、暂停/恢复/停止、航点采样规则、任务 JSON 记录和 CSV/GeoJSON/IDW surface 数据输出。
- 通过 MAVROS 与 `mavlink-routerd` 接入飞控任务链路；MAVLink 字段与命令变更必须以固件源码为准，并同步核对 QGC 定制面板。
- 飞控固件和 QGC UI 不在本仓库维护；污染物地图、采样点质量和历史数据可视化保持在 ROS/Web 侧。

## Brand Commitments

产品语气保持克制、可靠、工程化，如现场仪表盘般直接、稳定、少装饰，优先呈现可扫读状态、清晰反馈和可预期操作。它是现场任务工具而非营销式产品页。

## Evidence on Hand

- `README.md`：船载部署、ROS 接口、Web API、任务与数据工作流说明。
- `frontend/src/App.tsx` 与 `frontend/src/pages/`：监控、自动化、手动控制、数据、地图、实验航线和设置等已实现页面。
- `scripts/web_config_server.py`：Flask、Socket.IO、配置、任务记录与数据 API 的实现。
- `launch/usv_bringup.launch` 与 `scripts/`：ROS 主链路、检测装置控制、系统健康和 MAVLink bridge 实现。

## Product Principles

1. 现场优先：把连接、任务、泵组、分光、地图和日志状态放在用户能立刻扫到的位置。
2. 低装饰高密度：每个面板服务一个调试或运行任务，避免营销式留白和纯装饰元素。
3. 源码边界清楚：Web 承载 ROS 数据、配置和污染物地图；MAVLink 与串口语义以对应源码为准。
4. 操作有回声：命令按钮必须给出禁用、加载、成功、失败或当前状态，不让用户猜命令是否发出。
5. 可在现场恢复：窄屏、离线地图、Socket.IO 断连、设备离线和数据为空时都要保持可理解、可继续操作。

## Accessibility & Inclusion

默认目标为 WCAG 2.1 AA：正文与关键状态文字对比度不低于 4.5:1，焦点态清晰，图标状态必须有文字或可访问标签，颜色不作为唯一状态编码。动效只用于状态反馈，并尊重 `prefers-reduced-motion`。
