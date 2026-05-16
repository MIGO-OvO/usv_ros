# usv_ros

[![ROS Noetic](https://img.shields.io/badge/ROS-Noetic-22314E?logo=ros&logoColor=white)](https://wiki.ros.org/noetic)
[![Python](https://img.shields.io/badge/Python-3.8-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MAVLink](https://img.shields.io/badge/MAVLink-v2-0B7285)](https://mavlink.io/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-7-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`usv_ros` is the onboard ROS payload repository for the USV water-quality monitoring system. It targets
Jetson Nano / Ubuntu 20.04 / ROS Noetic and packages the detector controller, Web console, MAVROS integration,
and custom MAVLink payload link into one deployable stack.

This repository does not build the flight-controller firmware or the QGroundControl UI. It sits between them:
upstream of the ESP32 detector controller and downstream of Pixhawk/ArduRover and QGroundControl. Any MAVLink
command or telemetry-field change must be checked against the `ardupilot-usv/` firmware and the
`WQ-USV-QGroundControl/` custom panel.

Chinese version: `README.md`

English version: `README.en.md`

## Table of Contents

- [Role in the System](#role-in-the-system)
- [Runtime Topology](#runtime-topology)
- [Repository Status](#repository-status)
- [Repository Layout](#repository-layout)
- [Environment Setup](#environment-setup)
- [Startup and Deployment](#startup-and-deployment)
- [Configuration Model](#configuration-model)
- [ROS Interface](#ros-interface)
- [Web Console and API](#web-console-and-api)
- [MAVLink Link](#mavlink-link)
- [Detector Serial Protocol](#detector-serial-protocol)
- [Data and Logs](#data-and-logs)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Development Constraints](#development-constraints)
- [License](#license)

## Role in the System

`usv_ros` is responsible for the onboard payload computer:

- Controls the ESP32 detector controller: X/Y/Z/A stepper pumps, injection-pump PWM, angle streaming, and ADS
  spectrometer sampling.
- Runs sampling automation: the Web UI sends multi-step sampling sequences, while ROS executes, pauses, resumes,
  stops, and records mission data.
- Serves the Web console: Flask + Socket.IO backend with a React/Vite frontend for monitoring, hardware settings,
  waypoint sampling configuration, data review, log viewing, and link diagnostics.
- Connects to the flight-control workflow: MAVROS is used for state and mode services; a `mavlink-routerd` TCP
  endpoint is used for custom payload telemetry, commands, and acknowledgements.
- Uplinks payload status to the custom QGC panel through `NAMED_VALUE_FLOAT` fields for voltage, absorbance, pump
  angles, sampling progress, and PID state.

The default launch entry is `launch/usv_bringup.launch`:

| Node | Script | Purpose |
|---|---|---|
| `/mavros` | `mavros/launch/apm.launch` | Flight-controller state, mission, and mode services |
| `/pump_control_node` | `scripts/pump_control_node.py` | Serial handshake, pump control, automation, injection pump, ADS sampling |
| `/web_config_server` | `scripts/web_config_server.py` | Web pages, REST API, Socket.IO, config, and data recording |
| `/usv_mavlink_bridge` | `scripts/usv_mavlink_router_bridge.py` | MAVLink telemetry, commands, and ACKs over the router TCP endpoint |
| `/mavlink_trigger_node` | `scripts/mavlink_trigger_node.py` | Sampling command handling, mission phase state machine, waypoint sampling |

`scripts/mission_coordinator_node.py` is still present, but it is not part of the default launch chain.

## Runtime Topology

```text
QGroundControl / custom USV panel
  |  COMMAND_LONG 31010..31016
  |  NAMED_VALUE_FLOAT payload fields
  v
Telemetry radio -> Pixhawk 6C / custom ArduRover
  |  TELEM2, MAVLink2, 921600 bps
  v
mavlink-routerd  (Jetson Nano)
  |-- UDP 127.0.0.1:14550 -> MAVROS
  |-- TCP 127.0.0.1:5760 -> usv_mavlink_router_bridge.py
  v
ROS Noetic / usv_ros
  |-- mavlink_trigger_node.py
  |-- web_config_server.py
  |-- pump_control_node.py
  v
ESP32 detector controller
  |-- X/Y/Z/A stepper pumps
  |-- Injection-pump PWM
  |-- MT6701 angle sensing
  |-- ADS122C04 spectrometer sampling
```

The current stable link is not "QGC directly talks to ROS". The flight-controller firmware caches
`NAMED_VALUE_FLOAT` payload fields from the companion computer and relays them to the GCS at 2 Hz. This avoids the
direct forwarding path that is blocked by MAVLink routing.

## Repository Status

| Metric | Current Value |
|---|---:|
| ROS Python scripts | 13 |
| Linux operation scripts | 15 |
| Web API routes | 52 |
| Frontend TypeScript/TSX files | 29 |
| Unit test files | 2 |
| Committed frontend build artifacts | 4 |
| Stable tag | `v0.2.0-stable` |
| Remote repository | `https://github.com/MIGO-OvO/usv_ros.git` |

Implemented core capabilities:

- `mavlink-routerd` serial multiplexing: MAVROS and the custom bridge use separate endpoints.
- 13 QGC payload telemetry fields: `USV_VOLT`, `USV_ABS`, `PUMP_X/Y/Z/A`, `USV_STAT`, `USV_PKT`,
  `USV_STEP`, `USV_STOT`, `USV_SCNT`, `USV_PERR`, and `USV_PMOD`.
- Detector identity handshake: `HELLO?` / `DET?` must return `DET_ID:USV_DETECTOR*`.
- Web hardware hot-apply flow: saved serial settings call `/usv/pump_reconnect` and reconnect the pump node.
- Waypoint sampling CRUD, mission config import/export, mission JSON recording, and CSV download.
- Boot autostart scripts: hotspot, ROS payload stack, router, and self-check are managed through `usv-boot.service`.

## Repository Layout

```text
usv_ros/
├── CMakeLists.txt
├── package.xml
├── README.md
├── README.en.md
├── TESTING.md
├── config/
│   └── usv_params.yaml              # ROS parameter baseline loaded by launch
├── docs/current/
│   ├── overview.md                  # Repository-level architecture summary
│   └── roadmap.md                   # Follow-up roadmap
├── launch/
│   └── usv_bringup.launch           # Main bringup entry
├── scripts/
│   ├── pump_control_node.py         # Detector serial, pumps, automation, ADS data
│   ├── web_config_server.py         # Flask/Socket.IO Web gateway
│   ├── mavlink_trigger_node.py      # MAVLink commands and sampling state machine
│   ├── usv_mavlink_router_bridge.py # mavlink-router TCP bridge
│   ├── common_env.sh                # Runtime dirs, logs, and router helpers
│   ├── start_usv_all.sh             # Background roscore + router + payload startup
│   ├── stop_usv_all.sh              # Safe stop for payload, router, and roscore
│   ├── status_usv_all.sh            # Process, hotspot, ROS, MAVROS, and bridge diagnostics
│   └── lib/
│       ├── automation_engine.py
│       └── command_generator.py
├── frontend/
│   ├── src/                         # React console source
│   └── package.json
├── static/dist/                     # Built Web frontend
└── tests/
    ├── test_boot_service_scripts.py
    └── test_mavlink_command_compat.py
```

## Environment Setup

### Target Environment

- Jetson Nano / Ubuntu 20.04
- ROS Noetic + catkin
- Python 3.8
- Pixhawk 6C / custom ArduRover firmware
- ESP32 detector firmware
- Node.js 18+, only when rebuilding the Web frontend

### ROS and System Dependencies

Run from the ROS workspace root on Jetson or Ubuntu/WSL:

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

Common dependencies:

```bash
sudo apt update
sudo apt install ros-noetic-mavros ros-noetic-mavros-extras mavlink-router python3-pip
sudo /opt/ros/noetic/lib/mavros/install_geographiclib_datasets.sh
python3 -m pip install pyserial flask flask-cors flask-socketio eventlet pymavlink
```

If the distribution package name differs, the practical requirement is that `mavlink-routerd` is available on the
target system. You can also set `MAVLINK_ROUTERD_BIN` to point to the binary.

### Frontend Build

The repository already includes `static/dist/` build artifacts. Rebuild only when changing the frontend:

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm install
npm run build
```

Vite writes the output to `../static/dist`, which is served directly by `web_config_server.py`.

### First-Time Script Permission

```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/*.sh
```

## Startup and Deployment

### Full Onboard Startup

The recommended path is the operation script. It ensures runtime directories exist, starts `roscore` if needed,
starts `mavlink-routerd`, then starts `roslaunch usv_ros usv_bringup.launch`.

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
```

Default router settings come from `scripts/common_env.sh`:

| Environment Variable | Default | Purpose |
|---|---|---|
| `FCU_UART_DEVICE` | `/dev/ttyTHS1` | Jetson serial port connected to Pixhawk TELEM2 |
| `FCU_UART_BAUD` | `921600` | Flight-controller MAVLink serial baud rate |
| `ROUTER_MAVROS_UDP` | `127.0.0.1:14550` | MAVROS UDP endpoint |
| `ROUTER_BRIDGE_UDP` | `127.0.0.1:14551` | Reserved bridge UDP endpoint |
| `ROUTER_TCP_PORT` | `5760` | Default bridge TCP endpoint |
| `WEB_PORT` | `5000` | Web console port |

Override the flight-controller serial port if the field wiring differs:

```bash
cd ~/usv_ws
FCU_UART_DEVICE=/dev/ttyUSB1 FCU_UART_BAUD=921600 ./src/usv_ros/scripts/start_usv_all.sh
```

Launch arguments are forwarded:

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

Stop, restart, and view logs:

```bash
./src/usv_ros/scripts/stop_usv_all.sh
./src/usv_ros/scripts/restart_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
tail -f ~/usv_ws/.usv_run/logs/mavlink_router.log
```

### Direct roslaunch

This is useful for development. Direct `roslaunch` does not start `mavlink-routerd` for you.

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

Pump + Web only, without flight-controller integration:

```bash
roslaunch usv_ros usv_bringup.launch \
  enable_mavros:=false \
  enable_mavlink_trigger:=false \
  enable_mavlink_bridge:=false
```

Foreground payload startup with script-managed router readiness:

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_ros_master.sh
./src/usv_ros/scripts/start_usv_system.sh
```

### Web Access

- LAN: `http://<Jetson-IP>:5000`
- Hotspot mode: `http://10.42.0.1:5000`
- Local debugging: `http://127.0.0.1:5000`

Check which UI source is being served:

```bash
curl http://127.0.0.1:5000/api/ui/debug
```

### Hotspot

Create a WPA-PSK hotspot manually:

```bash
cd ~/usv_ws
sudo ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
sudo ./src/usv_ros/scripts/stop_hotspot.sh
```

The default connection name is `USV_AP`, and the default address is `10.42.0.1`. Override with
`HOTSPOT_IFACE`, `HOTSPOT_CONN_NAME`, `HOTSPOT_IP`, and `HOTSPOT_ROUTE_METRIC` when needed.

Recommended field networking uses two adapters in parallel: a USB Wi-Fi adapter for the USV hotspot, and
onboard Wi-Fi, Ethernet, or USB tethering for upstream internet access.

```bash
nmcli dev status
nmcli dev wifi connect "<internet-ssid>" password "<internet-password>" ifname wlan0

cd ~/usv_ws
sudo HOTSPOT_IFACE=wlan1 ./src/usv_ros/scripts/setup_hotspot.sh USV_Control 12345678
ip route
./src/usv_ros/scripts/status_usv_all.sh
```

The hotspot connection sets `ipv4.never-default=yes`, `ipv6.never-default=yes`, and a high route metric so
`USV_AP` does not take the default route. `status_usv_all.sh` reports `internet: ... source=external ...`
when the default route still uses the upstream interface.

### Boot Autostart

Install the systemd service:

```bash
cd ~/usv_ws
sudo ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

For the recommended two-adapter setup, pin the hotspot to the USB Wi-Fi interface:

```bash
cd ~/usv_ws
sudo HOTSPOT_IFACE=wlan1 ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

Default boot sequence:

1. Create or restore the hotspot.
2. Start `start_usv_all.sh` as the user that ran the installer.
3. Wait for Web, hotspot, ROS nodes, MAVROS, and bridge diagnostics.
4. Write self-check output to `~/usv_ws/.usv_run/logs/boot_check.log`.

Common commands:

```bash
sudo systemctl status usv-boot.service
sudo journalctl -u usv-boot.service -f
sudo systemctl restart usv-boot.service
sudo ./src/usv_ros/scripts/uninstall_boot_service.sh
```

Temporarily relax strict self-checks during field debugging:

```bash
sudo USV_STRICT_SELF_CHECK=false ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

## Configuration Model

There are three configuration layers. Keep them separate:

| Type | Path | Purpose |
|---|---|---|
| ROS static parameters | `config/usv_params.yaml` | Loaded by launch; defines pump, I2C, ADS, and MAVROS reference settings |
| Web runtime config | `~/usv_ws/config/sampling_config.json` | Sampling steps, waypoint sampling, and hardware settings saved by the Web UI |
| Calibration data | `~/usv_ws/config/calibration.json` | Web angle-zero calibration offsets |

The Web "save and apply" hardware flow:

1. Write the `hardware` section in `sampling_config.json`.
2. Update `/pump_control_node/serial_port`, `/pump_control_node/baudrate`, and `/pump_control_node/timeout`.
3. Call `/usv/pump_reconnect`.
4. `pump_control_node.py` reopens the serial port and performs the detector identity handshake.

### Launch Arguments

`launch/usv_bringup.launch` currently exposes:

| Group | Arguments |
|---|---|
| Pump control | `pump_port`, `pump_baudrate`, `pump_timeout`, `pid_mode`, `pid_precision`, `spectro_sample_wait_timeout` |
| Web | `web_host`, `web_port`, `web_ui` |
| MAVROS | `enable_mavros`, `mavros_fcu_url`, `mavros_gcs_url`, `mavros_tgt_system`, `mavros_tgt_component`, `mavros_fcu_protocol`, `mavros_respawn` |
| Sampling state machine | `mavros_timeout`, `hold_settle_time`, `stable_check_timeout`, `stable_speed_threshold`, `stable_yaw_rate_threshold`, `sampling_retry_count`, `sampling_on_fail` |
| MAVLink bridge | `mavlink_source_system`, `mavlink_source_component`, `mavlink_router_url` |
| Node switches | `enable_pump`, `enable_web`, `enable_mavlink_trigger`, `enable_mavlink_bridge` |

Example:

```bash
roslaunch usv_ros usv_bringup.launch \
  pump_port:=/dev/ttyUSB0 \
  web_port:=5000 \
  hold_settle_time:=5.0 \
  sampling_retry_count:=1 \
  sampling_on_fail:=SKIP
```

## ROS Interface

### Main Topics

| Topic | Type | Direction | Notes |
|---|---|---|---|
| `/usv/pump_command` | `std_msgs/String` | sub | Direct detector text command, such as `XEFR90.0P0.1` |
| `/usv/pump_step` | `std_msgs/String` | sub | Single sampling-step JSON |
| `/usv/automation_steps` | `std_msgs/String` | pub/sub | Full sampling sequence published by Web or trigger node |
| `/usv/pump_angles` | `std_msgs/String` | pub | X/Y/Z/A angle JSON |
| `/usv/pump_status` | `std_msgs/String` | pub | Pump and automation state text or structured status |
| `/usv/automation_status` | `std_msgs/String` | pub | Automation progress, step number, loop, and PID mode |
| `/usv/pump_pid_complete` | `std_msgs/String` | pub | PID completion notification |
| `/usv/pump_pid_error` | `std_msgs/String` | pub | PID error JSON |
| `/usv/injection_pump_status` | `std_msgs/String` | pub | Injection-pump status JSON |
| `/usv/spectrometer_voltage` | `std_msgs/String` | pub | Spectrometer JSON with `voltage`, `absorbance`, and related fields |
| `/usv/spectrometer_status` | `std_msgs/String` | pub | ADS configured/acquiring/error state |
| `/usv/spectrometer_raw` | `std_msgs/String` | pub | Raw spectrometer packet data |
| `/usv/spectrometer_absorbance` | `std_msgs/String` | pub | Absorbance data |
| `/usv/mission_status` | `std_msgs/String` | pub | Mission phase: `IDLE`, `SAMPLING`, `RESUMING_AUTO`, and others |
| `/usv/trigger_status` | `std_msgs/String` | pub | Trigger state: started, stopped, paused, calibration, and similar events |
| `/usv/bridge_diagnostics` | `std_msgs/String` | pub | Router bridge diagnostics JSON |
| `/usv/radio_status` | `std_msgs/String` | pub | `RADIO_STATUS` radio-link quality |
| `/usv/mavlink_cmd_rx` | `std_msgs/Float32MultiArray` | pub/sub | QGC/FCU command forwarded by the bridge |
| `/usv/mavlink_cmd_ack` | `std_msgs/Float32MultiArray` | pub/sub | Trigger node request for bridge `COMMAND_ACK` sending |

### Main Services

| Service | Type | Purpose |
|---|---|---|
| `/usv/pump_stop` | `std_srvs/Trigger` | Emergency stop for pumps |
| `/usv/automation_start` | `std_srvs/Trigger` | Start current automation steps |
| `/usv/automation_stop` | `std_srvs/Trigger` | Stop automation and halt pumps |
| `/usv/automation_pause` | `std_srvs/Trigger` | Pause automation |
| `/usv/automation_resume` | `std_srvs/Trigger` | Resume automation |
| `/usv/injection_pump_on` | `std_srvs/Trigger` | Turn on the injection pump |
| `/usv/injection_pump_off` | `std_srvs/Trigger` | Turn off the injection pump |
| `/usv/injection_pump_get_status` | `std_srvs/Trigger` | Query injection-pump state |
| `/usv/pump_reconnect` | `std_srvs/Trigger` | Reconnect detector serial using current ROS params |
| `/usv/spectrometer_start` | `std_srvs/Trigger` | Send `ADSSTART` |
| `/usv/spectrometer_stop` | `std_srvs/Trigger` | Send `ADSSTOP` |
| `/usv/i2c_map_apply` | `std_srvs/Trigger` | Send current I2C mapping |
| `/usv/trigger_sampling` | `std_srvs/Trigger` | Manually trigger sampling with HOLD and stability checks |

Common checks:

```bash
rosnode list
rostopic list | grep /usv/
rostopic echo /usv/pump_status
rostopic echo /usv/bridge_diagnostics
rosservice call /usv/pump_stop
```

## Web Console and API

The Web backend is `scripts/web_config_server.py`, listening on `0.0.0.0:5000` by default. It serves the static
frontend, REST API, and Socket.IO real-time events.

### Common REST API

| API | Purpose |
|---|---|
| `GET /api/config`, `POST /api/config`, `POST /api/config/reset` | Read, save, and reset runtime config |
| `POST /api/mission/start|stop|pause|resume` | Control sampling mission execution |
| `GET/POST /api/waypoint-sampling` | Waypoint sampling configuration |
| `GET /api/mission-config/export`, `POST /api/mission-config/import` | Mission config import/export |
| `POST /api/motor/command`, `POST /api/motor/stop` | Manual motor commands |
| `GET/POST /api/pid/config`, `POST /api/pid/test` | PID parameters and tests |
| `GET /api/calibration/offsets`, `POST /api/calibration/zero|reset|start` | Angle calibration |
| `GET /api/data/voltage`, `POST /api/data/voltage/clear` | Current in-memory voltage history |
| `GET /api/data/missions` | Historical mission file list |
| `GET /api/data/mission/<id>` | Historical mission JSON |
| `GET /api/data/mission/<id>/csv` | Historical mission CSV download |
| `GET /api/logs/files`, `GET /api/logs/<filename>` | System log viewing |
| `GET /api/hardware/config`, `POST /api/hardware/config` | Hardware connection config |
| `GET /api/hardware/serial-ports` | Serial-port enumeration |
| `POST /api/hardware/test-pump-port` | Open serial port and perform detector handshake |
| `POST /api/hardware/apply` | Save hardware config and reconnect the pump node |
| `GET /api/diagnostics/link|history|events|export` | Link diagnostics |

### Socket.IO Events

The backend emits:

```text
status
pump_angles
raw_angles
voltage
pid_error
injection_pump_status
log
mavros_state
bridge_diagnostics
radio_status
```

### Quick API Checks

```bash
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/config
curl http://127.0.0.1:5000/api/hardware/serial-ports
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
```

Hot-apply hardware config:

```bash
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```

## MAVLink Link

### Endpoints

| Endpoint | Default | Purpose |
|---|---|---|
| Pixhawk UART | `/dev/ttyTHS1:921600` | Physical Jetson to Pixhawk TELEM2 link |
| MAVROS | `udp://127.0.0.1:14550@` | State, mission, and mode services |
| Bridge | `tcp:127.0.0.1:5760` | Custom telemetry, commands, and ACKs |

### Downlink Commands

`usv_mavlink_router_bridge.py` receives `COMMAND_LONG` from the router TCP endpoint, filters `31010..31016`, and
forwards the command to `/usv/mavlink_cmd_rx`. `mavlink_trigger_node.py` executes it and publishes
`/usv/mavlink_cmd_ack`; the bridge then sends `COMMAND_ACK`.

| Command | Meaning |
|---:|---|
| `31010` | Start sampling. `param2 > 0` means FCU-native sampling trigger; no HOLD mode switch |
| `31011` | Stop sampling |
| `31012` | Pause sampling |
| `31013` | Resume sampling |
| `31014` | Calibration; publishes `CALXYZA\r\n` to the detector |
| `31015` | Start survey sampling |
| `31016` | Stop survey sampling |

The bridge also supports FCU-native triggers sent as `NAMED_VALUE_FLOAT`:

- `USV_SMPL=<sample_id>`: trigger one point sample; on completion the bridge sends `USV_DONE=<sample_id>`.
- `USV_SURV=1/0`: start or stop survey sampling.

### Uplink Telemetry

The bridge sends:

- `HEARTBEAT`: 1 Hz.
- `NAMED_VALUE_FLOAT`: 2 Hz, 13 fields per cycle.

| Field | Meaning |
|---|---|
| `USV_VOLT` | Spectrometer voltage |
| `USV_ABS` | Absorbance |
| `PUMP_X`, `PUMP_Y`, `PUMP_Z`, `PUMP_A` | Four pump angles |
| `USV_STAT` | Mission phase code |
| `USV_PKT` | Payload packet counter |
| `USV_STEP` | Current automation step |
| `USV_STOT` | Total automation steps |
| `USV_SCNT` | Sample count |
| `USV_PERR` | PID error |
| `USV_PMOD` | PID mode: 0 idle, 1 running, 2 complete, 3 error |

Diagnostics:

```bash
rostopic echo /usv/bridge_diagnostics
rostopic echo /usv/mavlink_cmd_rx
rostopic echo /usv/radio_status
```

## Detector Serial Protocol

The default pump serial port is `/dev/ttyUSB0`, `115200 8N1`. Text commands end with `\r\n`.

On connection, `pump_control_node.py`:

1. Opens the serial port and releases `DTR=False`, `RTS=False` to avoid actively resetting the ESP32.
2. Sends `HELLO?\r\n` and `DET?\r\n`.
3. Requires `DET_ID:USV_DETECTOR*`; otherwise the port is closed and startup fails.

The Web endpoint `POST /api/hardware/test-pump-port` uses the same handshake. A port that can be opened is not
considered connected unless the detector identity is returned.

Common downlink commands:

| Command | Example | Meaning |
|---|---|---|
| PID closed loop | `XEFR90.0P0.1` | X axis forward 90 degrees, precision 0.1 |
| Open-loop angle | `XEFV5J90.000` | X axis forward, 5 RPM, 90 degrees |
| Continuous rotation | `XEFV5JG` | X axis continuous forward rotation |
| Stop | `XDFV0J0` | Stop X axis |
| PID parameters | `PIDCFG:0.14,0.015,0.06,1.0,8.0` | Configure PID |
| Injection pump | `PUMP:ON`, `PUMP:OFF`, `PUMP:SET:60` | On/off and speed |
| I2C mapping | `I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2` | TCA channel mapping |
| ADS config | `ADSCFG:CH=2,ADDR=0x40,AIN=AIN0,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=20` | Spectrometer ADC |
| ADS start/stop | `ADSSTART`, `ADSSTOP` | Spectrometer acquisition |
| Calibration | `CALXYZA` | Four-axis calibration |

Uplink binary packets:

| Header | Content |
|---|---|
| `0x55 0xCC` | X/Y/Z/A angle floats |
| `0x55 0xAA` | PID data |
| `0x55 0xBB` | PID test result |
| `0x55 0xDD` | Spectrometer data: timestamp, channel, status, raw code, voltage |

## Data and Logs

Runtime paths live at the workspace root, not inside this repository directory:

| Path | Content |
|---|---|
| `~/usv_ws/.usv_run/` | PID and runtime log root |
| `~/usv_ws/.usv_run/logs/roscore.log` | ROS Master log |
| `~/usv_ws/.usv_run/logs/mavlink_router.log` | Router log |
| `~/usv_ws/.usv_run/logs/usv_system.log` | Main roslaunch log |
| `~/usv_ws/.usv_run/logs/boot_check.log` | Boot self-check log |
| `~/usv_ws/config/sampling_config.json` | Web sampling config |
| `~/usv_ws/config/calibration.json` | Calibration offsets |
| `~/usv_ws/data/missions/mission_*.json` | Mission sampling data |

Check status:

```bash
./src/usv_ros/scripts/status_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
curl http://127.0.0.1:5000/api/logs/files
curl "http://127.0.0.1:5000/api/logs/usv_system.log?lines=100"
```

Export mission data:

```bash
curl http://127.0.0.1:5000/api/data/missions
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/csv > mission.csv
```

## Verification

### Code Logic Verification

Run from this repository:

```bash
cd ~/usv_ws/src/usv_ros
python3 -m py_compile \
  scripts/pump_control_node.py \
  scripts/web_config_server.py \
  scripts/mavlink_trigger_node.py \
  scripts/usv_mavlink_router_bridge.py \
  scripts/lib/automation_engine.py \
  scripts/lib/command_generator.py
python3 -m unittest discover -s tests -p "test_*.py"
```

Frontend verification:

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm run lint
npm run build
```

### Runtime Baseline Check

After full startup:

```bash
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
rosnode list
rostopic echo -n 1 /mavros/state
rostopic echo -n 1 /usv/bridge_diagnostics
curl http://127.0.0.1:5000/api/ui/debug
```

Expected baseline:

- `status_usv_all.sh` shows `roscore`, `mavlink_router`, and `usv_system` running.
- `ros_nodes` includes at least `/pump_control_node`, `/web_config_server`, `/mavlink_trigger_node`,
  `/usv_mavlink_bridge`, and `/mavros`.
- `/mavros/state` contains `connected: True`.
- `/usv/bridge_diagnostics` has increasing `tx_named_value` and `pkt_count`.
- The Web console is reachable, and `/api/ui/debug` returns `dist_index_exists=true` or a clear static fallback.

More field test procedures are documented in [TESTING.md](TESTING.md).

## Troubleshooting

| Symptom | Check First |
|---|---|
| Blank Web page | `GET /api/ui/debug`, especially `dist_index_exists`, `ui_mode`, and `/assets/*` |
| Port 5000 unreachable | `status_usv_all.sh`, `ss -ltn | grep 5000`, and `usv_system.log` |
| Pump node fails to start | Serial path, permissions, ESP32 power, and `DET_ID:USV_DETECTOR` handshake |
| Web serial test fails | `POST /api/hardware/test-pump-port` identity or error response |
| No spectrometer data | `/usv/spectrometer_status`, `ADSSTART`, I2C mapping, and detector firmware state |
| MAVROS disconnected | Flight-controller serial wiring, `SERIAL2_PROTOCOL=2`, `SERIAL2_BAUD=921`, and `mavlink_router.log` |
| QGC command does not reach ROS | `/usv/mavlink_cmd_rx`, router TCP endpoint, and QGC target sys/comp |
| QGC panel shows no telemetry | `/usv/bridge_diagnostics` `tx_named_value`, and matching custom ArduPilot/QGC builds |
| Concern about stop script killing unrelated processes | The scripts verify PID command lines and only stop `roscore`, `roslaunch`, `mavlink-router`, and USV-related Python processes |

## Development Constraints

- Check flight-controller source before any MAVLink change, especially `Rover/GCS_MAVLink_Rover.cpp`,
  `Rover/sensors.cpp`, and `libraries/GCS_MAVLink/`. Do not change commands or fields based only on this README
  or interface notes.
- `src/usv_ros/` is an independent Git repository. Enter this directory before Git operations; do not commit
  subrepository changes from the workspace root.
- Commit messages use `Feat:`, `Fix:`, `Refactor:`, or `Docs:` prefixes.
- Windows is usually used for code editing and logic verification. Real Jetson, Pixhawk, and ESP32 runtime
  validation must be done on field hardware.
- When changing Web APIs, ROS topics/services, or MAVLink fields, update this README, [TESTING.md](TESTING.md),
  and the workspace-level `docs/current/INTERFACE.md`.

## License

`package.xml` declares this repository as MIT licensed. The repository currently does not include a standalone
`LICENSE` file; add one before public release or redistribution.
