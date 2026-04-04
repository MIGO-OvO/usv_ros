# USV ROS Package

> ROS Noetic payload stack for USV water-quality monitoring (Jetson Nano + Pixhawk + ESP32).

Chinese version: `README.md`  
English version: `README.en.md`

## Table of Contents

- [1. Overview](#1-overview)
- [2. System Architecture](#2-system-architecture)
- [3. Repository Layout](#3-repository-layout)
- [4. Prerequisites and Initial Setup](#4-prerequisites-and-initial-setup)
- [5. Startup Guide](#5-startup-guide)
- [6. Common Operations and Verification](#6-common-operations-and-verification)
- [7. Runtime Modes](#7-runtime-modes)
- [8. Troubleshooting](#8-troubleshooting)
- [9. License](#9-license)

---

## 1. Overview

`usv_ros` is the ROS package for the USV payload subsystem, deployed on Jetson Nano. It provides:

- UART control for ESP32 motor board (4 stepper pumps + injection pump)
- ADS spectrometer data acquisition via ESP32 serial bridge
- Web control interface (Flask + Socket.IO + React)
- MAVROS / MAVLink integration for mission trigger and telemetry uplink

Core entry points:

- `launch/usv_bringup.launch`: all-in-one startup entry
- `scripts/pump_control_node.py`: pump control (including injection pump) and spectrometer data acquisition
- `scripts/web_config_server.py`: Web gateway
- `scripts/mavlink_trigger_node.py`: MAVLink trigger node
- `scripts/usv_mavlink_bridge.py`: telemetry bridge

---

## 2. System Architecture

### 2.1 Layered Components

```text
Ground Station (QGC)
  ├─ MAVLink command (COMMAND_LONG 31010~31014)
  └─ MAVLink telemetry (NAMED_VALUE_FLOAT)
            ▲
            │
Pixhawk + MAVROS
            ▲
            │
Jetson Nano / ROS Noetic
  ├─ mavlink_trigger_node.py      (trigger flow)
  ├─ mission_coordinator_node.py  (mission state orchestration)
  ├─ usv_mavlink_bridge.py        (telemetry bridge)
  ├─ web_config_server.py         (HTTP/WebSocket gateway)
  ├─ pump_control_node.py         (pump control + automation + injection pump protocol + spectrometer reading)
            ▲
            │
Hardware
  ├─ ESP32 motor board (UART)
  │   ├─ X/Y/Z/A stepper pumps
  │   └─ Injection pump (DC PWM)
  └─ ADS Spectrometer (USB)
```

### 2.2 Key Data Flow

1. Web UI calls REST APIs of `web_config_server.py`, and receives real-time states via Socket.IO.
2. `web_config_server.py` forwards control requests to ROS topics/services.
3. `pump_control_node.py` encodes/decodes serial protocol and drives ESP32.
4. `pump_control_node.py` manages pumps and reads ADS spectrometer data, publishing `/usv/spectrometer_voltage` as JSON.
5. `mavlink_trigger_node.py` listens on `/mavros/mavlink/from` and converts triggers to ROS actions.
6. `usv_mavlink_bridge.py` maps payload states to `/mavros/mavlink/to` telemetry.

### 2.3 Nodes Started by `usv_bringup.launch`

By default (switchable with `enable_*` args):

- `pump_control_node`
- `pump_control_node`
- `web_config_server`
- `mavlink_trigger_node`
- `usv_mavlink_bridge`

---

## 3. Repository Layout

```text
src/usv_ros/
├─ config/
│  └─ usv_params.yaml
├─ launch/
│  └─ usv_bringup.launch
├─ scripts/
│  ├─ pump_control_node.py

│  ├─ web_config_server.py
│  ├─ mavlink_trigger_node.py
│  ├─ mission_coordinator_node.py
│  ├─ usv_mavlink_bridge.py
│  └─ lib/
├─ frontend/
│  ├─ src/
│  └─ package.json
├─ static/
│  └─ dist/ (frontend build output)
├─ README.md
└─ README.en.md
```

## 4. Prerequisites and Initial Setup

### 4.1 Baseline

- Ubuntu 20.04 (Jetson Nano)
- ROS Noetic
- Python 3.8
- Node.js 18+ (only needed for frontend rebuild/development)

### 4.2 ROS and System Dependencies

```bash
cd ~/usv_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make
source devel/setup.bash
```

Recommended shell initialization:

```bash
echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
echo "source ~/usv_ws/devel/setup.bash" >> ~/.bashrc
```

### 4.3 Python Dependencies

```bash
pip3 install pyserial flask flask-cors flask-socketio eventlet

```

### 4.4 Frontend and Static Assets

`web_config_server.py` supports two UI sources:

- `static/index.html` (static fallback)
- `static/dist/index.html` (built frontend)

To rebuild frontend assets:

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm install
npm run build
# Build output should be available under ../static/dist
```

### 4.5 Hardware and Link Checks (Before Launch)

1. UART device is present for ESP32 (e.g. `/dev/ttyUSB0`).
2. ESP32 serial bridge is accessible.
3. MAVROS state topic is updating (`/mavros/state`).
4. `config/usv_params.yaml` matches on-site hardware settings.

---

## 5. Startup Guide

### 5.1 One-Command Bringup (Recommended)

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

### 5.2 Multi-Terminal Startup Sequence (Debug/Integration Recommended)

Use this flow when you want to explicitly start ROS Master first and keep logs separated by terminal.

**Terminal A: start ROS Master**

```bash
cd ~/usv_ws
source /opt/ros/noetic/setup.bash
roscore
```

**Terminal B: start payload services**

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

**Terminal C (optional): runtime monitoring**

```bash
source devel/setup.bash
rosnode list
rostopic echo /usv/pump_status
```

### 5.3 One-Click Start/Stop Scripts (Run Directly from Linux Terminal)

The following scripts have been added under `src/usv_ros/scripts/`:

- `start_usv_all.sh`: one-click background startup for `roscore` + the `usv_ros` main system
- `stop_usv_all.sh`: one-click stop for the `usv_ros` main system + `roscore`
- `common_env.sh`: shared environment loading, PID management, and log directory management

For the first use, grant execute permission:

```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/common_env.sh
chmod +x src/usv_ros/scripts/start_usv_all.sh
chmod +x src/usv_ros/scripts/stop_usv_all.sh
```

One-click start:

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
```

One-click stop:

```bash
cd ~/usv_ws
./src/usv_ros/scripts/stop_usv_all.sh
```

Script behavior:

- `start_usv_all.sh` automatically loads ROS Noetic and workspace environment.
- If no running `roscore` is detected, it starts `roscore` in the background first.
- It then starts `roslaunch usv_ros usv_bringup.launch` in the background.
- PID files and logs are stored under `~/usv_ws/.usv_run/`:
  - `~/usv_ws/.usv_run/roscore.pid`
  - `~/usv_ws/.usv_run/usv_system.pid`
  - `~/usv_ws/.usv_run/logs/roscore.log`
  - `~/usv_ws/.usv_run/logs/usv_system.log`

View logs:

```bash
tail -f ~/usv_ws/.usv_run/logs/roscore.log
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
```

The start script supports forwarding launch arguments, for example:

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

If you still want separate-terminal debugging, you can continue using:

- `start_ros_master.sh`
- `start_usv_system.sh`
- `start_usv_minimal.sh`

### 5.4 Override Launch Arguments

Common args in `launch/usv_bringup.launch`:

- `pump_port` (default `/dev/ttyUSB0`)
- `pump_baudrate` (default `115200`)
- `pump_timeout` (default `1.0` seconds)
- `pid_mode` (default `true`)
- `pid_precision` (default `0.1`)

- `web_host` (default `0.0.0.0`)
- `web_port` (default `5000`)
- `web_ui` (default `auto`, options: `legacy|react|auto`)
- `mavros_timeout` (default `30.0`)
- `auto_trigger_on_waypoint` (default `true`)
- `trigger_waypoints` (default `[]`, example: `[1,3,5]`)
- `mavros_fcu_url` (default `/dev/ttyTHS1:921600`)
- `mavlink_source_system` (default `1`)
- `mavlink_source_component` (default `240`)
- `enable_pump|enable_web|enable_mavlink_trigger|enable_mavlink_bridge`

Example: pump + web only

```bash
roslaunch usv_ros usv_bringup.launch \
  
  enable_mavlink_trigger:=false \
  enable_mavlink_bridge:=false
```

Example: restrict auto-trigger waypoints and switch Web UI

```bash
roslaunch usv_ros usv_bringup.launch \
  trigger_waypoints:="[2,4]" \
  web_ui:=react \
  pump_timeout:=2.0
```

### 5.5 Access Web Console

- LAN: `http://<Jetson-IP>:5000`
- AP mode: `http://10.42.0.1:5000`

---

## 6. Common Operations and Verification

### 6.1 ROS Health Checks

```bash
rosnode list
rostopic list | grep /usv/
rostopic echo /usv/pump_status
```

### 6.2 Service Calls

```bash
rosservice call /usv/automation_start
rosservice call /usv/automation_pause
rosservice call /usv/automation_resume
rosservice call /usv/automation_stop
rosservice call /usv/pump_stop

rosservice call /usv/injection_pump_on
rosservice call /usv/injection_pump_off
rosservice call /usv/injection_pump_get_status
```

### 6.3 Quick API Checks

```bash
curl http://127.0.0.1:5000/api/config
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
```

---

## 7. Runtime Modes

### 7.1 Web UI Source Mode

`web_config_server.py` supports `~web_ui` param or `USV_WEB_UI` env:

- `auto`: prefer `static/dist`, fallback to `static`
- `dist`: force built frontend
- `vite`: frontend dev integration mode

### 7.2 Spectrometer Mode



---

## 8. Troubleshooting

1. **Blank web page**  
   Check `GET /api/ui/debug` for `ui_mode` and `dist_index_exists`.

2. **Pump not responding**  
   Check serial port path, permissions, ESP32 power, and cabling.

3. **No spectrometer data**  
   Verify pump hardware connections.

4. **MAVLink trigger not effective**  
   Verify `/mavros/mavlink/from` and `/mavros/mission/reached` traffic.

---

## 9. License

MIT License

