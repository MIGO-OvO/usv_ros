# USV ROS Package - 水质监测无人船系统

基于 ROS Noetic 的水质监测无人船载荷控制系统，运行于 Jetson Nano。包含完整的后端控制节点与现代化的 Web 交互前端。

## 目录

- [系统概述](#系统概述)
- [系统架构](#系统架构)
- [节点说明](#节点说明)
- [核心功能](#核心功能)
- [Web 前端](#web-前端)
- [使用方法](#使用方法)

---

## 系统概述

### 这是什么？

这是一个**基于液滴微流控技术的微型自动化化学分析实验室 (Lab-on-a-Chip)** 的控制系统，而非简单的浸入式探头传感器。

### 核心硬件

| 组件 | 说明 |
|------|------|
| **蠕动泵 (X/Y/Z/A)** | 4 个步进电机驱动的微型蠕动泵，带 MT6701 磁编码器闭环控制 |
| **ESP32 控制板** | 下位机，接收串口指令控制电机，返回角度反馈 |
| **NI DAQ 采集卡** | 采集分光光度检测器的电压信号 |
| **分光检测器** | L型流道光学检测单元，LED + 光电二极管 |
| **Pixhawk 6C** | 无人船飞控，通过 MAVROS 与 Jetson Nano 通信 |

### 工作流程

```
1. 航行 (Pixhawk 控制) 
   ↓
2. 到达采样点 → Nano 监听航点到达事件
   ↓
3. 切换 HOLD 模式 → 无人船悬停
   ↓
4. 采样 → Nano 控制蠕动泵抽取水样、注入试剂 (执行自动化序列)
   ↓
5. 检测 → DAQ 采集分光检测器电压，计算吸光度 (数据记录)
   ↓
6. 恢复 AUTO 模式 → 继续航行到下一航点
```

---

## 系统架构

系统采用前后端分离架构，前端运行于浏览器（可跨平台访问），后端运行于 Jetson Nano 的 ROS 环境中。

```
┌─────────────────────────────┐      ┌───────────────────────────────────────────────┐
│      Web Frontend           │      │                 Jetson Nano                   │
│ (React + Vite + Tailwind)   │ HTTP │  ┌─────────────────┐   ┌───────────────────┐  │
│                             │/WS   │  │ web_config_     │   │ mission_          │  │
│  [监控仪表盘] [自动化配置]  │◄────►│  │ server.py       │   │ coordinator_node  │  │
│  [数据中心]   [系统设置]    │      │  │ (Flask/SocketIO)│   │ (任务协调)        │  │
│                             │      │  └────────┬────────┘   └─────────┬─────────┘  │
└─────────────────────────────┘      │           │ ROS Topic/Service    │            │
                                     │           ▼                      ▼            │
                                     │  ┌─────────────────┐   ┌───────────────────┐  │
                                     │  │ pump_control_   │   │ spectrometer_     │  │
                                     │  │ node.py         │   │ node.py           │  │
                                     │  │ (泵控制/PID)    │   │ (电压采集)        │  │
                                     │  └────────┬────────┘   └─────────┬─────────┘  │
                                     └───────────┼──────────────────────┼────────────┘
                                                 │ UART                 │ USB
                                                 ▼                      ▼
                                          ┌──────────────┐       ┌──────────────┐
                                          │ ESP32 下位机 │       │ NI DAQ 采集卡│
                                          └──────────────┘       └──────────────┘
```

### ROS 话题拓扑

```
/usv/pump_command ──────────► pump_control_node ──────► /usv/pump_angles (原始/校准角度)
     (String)                       │                    (String JSON)
                                    │
                                    └──────────────────► /usv/pump_status
                                                         (String JSON)

spectrometer_node ──────────────────────────────────────► /usv/spectrometer_voltage
                                                          (Float64)

/mavros/state ──────────────► mission_coordinator ──────► /usv/mission_status
/mavros/mission/reached ────►      _node          ──────► /usv/detection_result
                                    │
                                    └──────────────────► /mavros/set_mode (Service)
```

---

## 节点说明

### 1. `web_config_server.py` (Web 配置服务器)

**功能**: 系统的核心网关，负责连接 React 前端与 ROS 后端。

**核心模块**:
*   **CalibrationManager**: 管理零点校准。加载/保存 `calibration.json`，在接收到原始角度后自动应用偏移量，向前端推送校准后的角度。
*   **MissionDataManager**: 任务数据管理。负责创建任务文件 (`mission_YYYYMMDD.json`)，实时记录电压数据，并提供历史数据的查询与导出 API。
*   **ConfigManager**: 管理采样序列配置 (`sampling_config.json`)。
*   **SocketIO**: 实时推送电压 (`voltage`)、角度 (`pump_angles`) 和状态信息。

**API 接口 (部分)**:
*   `POST /api/calibration/zero`: 设置零点
*   `GET /api/data/missions`: 获取任务列表
*   `GET /api/data/mission/<id>`: 获取任务详情

### 2. `pump_control_node.py` (泵控制节点)

**功能**: 直接与 ESP32 通信，执行电机控制。

**特性**:
*   **通信协议**: 解析 ESP32 的二进制反馈包 (`0x55 0xCC ...`) 和文本指令。
*   **自动化引擎**: 内置 `AutomationEngine`，负责解析并执行复杂的多步骤采样序列（控制电机转动、延时、等待 PID 完成）。
*   **PID 支持**: 处理 PID 参数配置 (`PIDCFG`) 和闭环控制指令。

### 3. `spectrometer_node.py` (分光检测节点)

**功能**: 读取分光光度计数据。

**特性**:
*   **硬件支持**: 通过 `nidaqmx` 驱动调用 NI 采集卡。
*   **模拟模式**: 当未检测到硬件时，自动切换到模拟模式，生成正弦波数据以便于开发调试。

### 4. `mission_coordinator_node.py` (任务协调节点)

**功能**: 无人船整体任务流控制。

**特性**:
*   **MAVROS 集成**: 监听 `/mavros/mission/reached` 消息判断是否到达采样点。
*   **模式切换**: 自动切换 `AUTO` (航行) 和 `HOLD` (悬停) 模式。

---

## 核心功能

### 1. 零点校准 (Zero Calibration)
由于磁编码器安装存在物理误差，导致“0度”位置不统一。
*   **实现**: 系统在 `web_config_server` 维护一个偏移量表 (`calibration.json`)。
*   **操作**: 用户在前端“设置”页点击“设为零点”，后端记录 `Offset = Current_Raw_Angle`。
*   **效果**: 后续所有显示和控制均基于 `Calibrated_Angle = Raw_Angle - Offset`，确保所有泵的物理零点一致。

### 2. 任务数据管理 (Mission Data Center)
*   **按任务存储**: 每次启动自动化采样，系统会自动创建一个新的任务记录文件，存储于 `~/usv_ws/data/missions/`。
*   **完整记录**: 包含任务开始/结束时间、采样点数量以及高频电压数据。
*   **数据可视化**: 前端“数据中心”提供分栏视图，可查看历史任务列表，并预览电压曲线。
*   **导出**: 支持将任意任务的数据导出为 CSV 格式。

### 3. 自动化采样序列
支持用户自定义采样步骤，每一步可配置：
*   **X/Y/Z/A 轴动作**: 启用/禁用、目标角度、速度。
*   **步骤间隔**: 毫秒级控制。
*   **循环执行**: 支持单次或无限循环。

---

## Web 前端

基于 **React + TypeScript + Vite + Tailwind CSS** 构建的现代化单页应用 (SPA)。

*   **UI 组件库**: shadcn/ui (基于 Radix UI)。
*   **状态管理**: Zustand。
*   **主要页面**:
    *   **监控 (Monitor)**: 实时显示电压曲线、PID 误差、系统连接状态。
    *   **自动化 (Automation)**: 可视化序列编辑器，支持预设管理。
    *   **数据 (Data)**: 历史任务查看与导出。
    *   **设置 (Settings)**: PID 参数配置、零点校准。
*   **特色功能**:
    *   **深色/浅色模式**: 支持一键切换主题。
    *   **国际化**: 全界面支持简体中文。
    *   **响应式设计**: 完美适配桌面和移动端。

---

## 使用方法

### 1. 编译与安装

```bash
# 1. 编译 ROS 工作空间
cd ~/usv_ws
catkin_make
source devel/setup.bash

# 2. 安装 Python 依赖
pip3 install flask flask-socketio flask-cors pyserial eventlet

# 3. 编译前端 (开发环境需要)
cd src/usv_ros/frontend
npm install
npm run build
```

### 2. 启动系统

```bash
# 启动所有节点 (包含 Web 服务器)
roslaunch usv_ros usv_bringup.launch
```

### 3. 访问控制台

*   **直连**: 浏览器访问 `http://<Jetson-IP>:5000`
*   **热点模式**: 连接 `USV_Control` 热点后访问 `http://10.42.0.1:5000`

### 4. 常用指令

```bash
# 手动发送泵指令 (X轴正转90度)
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'"

# 触发自动化采样
rosservice call /usv/automation_start

# 紧急停止
rosservice call /usv/pump_stop
```

---

## 许可证

MIT License
