# usv_ros

## 2026-10 跨端采样安全修复

- 必须与匹配的 DetFirmware、ArduRover 和 QGC 成套更新。26 个显示遥测字段（含 `USV_LOOP/LTOT/ADS/PROTO`）；`USV_ADS` 为 0 未知、1 停止已确认、2 启动中、3 采集中、4 错误、5 停止未确认。旧 QGC/ROS 组合不用于自动任务验收。
- STOP 使用独立有界通道，先取消旧工作再发送；注射泵队列在连接换代、取消、期限届满后不得重放。重连恢复需真实 STOPALL 与 ADSSTOP 确认，串口写成功不代表硬件停止。
- 真实采样准入要求当前会话的真实新鲜帧与有效基线，GPS 仍可选。运行断流 2 秒后数据无效，在默认 10 秒有界窗口内可等待恢复；恢复前不发下一条运动命令，不用旧数据完成步骤，现有输出可能继续运行。设备错误、owner 丢失与停止不等待这个窗口。
- 请求由内存 RequestLedger 去重，JSONL 仅为尽力诊断，不阻塞动作、STOP 或通道释放。bridge 固定每个请求的 trigger 执行实例；trigger 重启但 TCP 未重连时，旧身份返回 `executor_restarted`，不能续跑。不要清空 QGC 请求身份文件来“解锁”。失败/取消保留首因、准备阶段和清理失败标记；Web 按 trigger 最终结果关窗，并导出 `sample_results.csv`。
- Web 回零和 QGC 31014 共用 `calibration_start`，读取 `calibration.json` 和新鲜原始角度，以最短相对 R/PID 运动回保存零点，全部 PID_DONE 后成功。缺失零点明确拒绝；STOP 抢占并核验 STOPALL_OK，清理失败保留标志。独立回零不执行 360°补偿或油相分隔，自动化原流程保留。Web 与 bridge 需指向同一零点文件，bridge/pump 可用 `~calibration_file` 指定；Web 修改后立即更新显示，QGC 下一遥测周期同步，未校准轴分别显示 null/-1。

日志入口为 `usvlogs`，支持最近日志打包、占用查询、清理预览和定时清理。安装与用法见下节。

### 日志管理与下载

更新后安装快捷命令，系统依赖为 Python 3、logrotate 和 cron：

```bash
sudo apt install logrotate cron
bash ~/usv_ws/src/usv_ros/scripts/install_usv_commands.sh install
usvlogs usage
usvlogs pack field-1022              # 输出 field-1022.tar.gz
usvlogs pack field-1022-more --count 5
usvlogs clean                        # 预览轮转和删除目标
usvlogs clean --apply                # 执行，删除后不可恢复
usvlogs auto on                      # 当前用户 cron，每 10 分钟检查
usvlogs auto status
usvlogs auto off
```

`usvctl logs` 与 `usvlogs` 等价。日志包默认取最近 3 个 ROS 会话、最近 3 个 ROS 根目录日志，以及三类启动日志；包含当前文件和未压缩的 `.1` 轮转文件，不含更早的压缩轮转。每文件只取末尾 2 MiB，总计最多 64 MiB，包内 `manifest.json` 记录原始大小和截取大小。日志包保存在 `~/usv_ws/.usv_run/bundles/`，同名包不覆盖。它是排障快照，不是完整现场证据备份。

安装遇到旧版独立命令或其他符号链接时，先迁移到 `~/.local/bin/.usv-commands-backup.XXXXXX/`，逐项打印备份路径，再建立新链接；自定义 `USV_COMMAND_DIR` 时备份位于该目录。重复安装保留已有正确链接；卸载仅移除本仓库链接，备份由用户保管。若目标是目录，安装会提示手动处理。

清理先复用 logrotate：启动日志和 ROS `*.log` 超过 10 MiB 时轮转，保留 5 份。copytruncate 保持正在写入的文件描述符，但复制与截断之间可能丢少量日志。随后按旧到新清理 ROS UUID 会话、ROS 根目录日志和日志包，每类至少保留最近 3 份。数量与活动保护优先于容量预算；ROS 共用 512 MiB、日志包 256 MiB，保留后仍超预算则输出目录、占用与预算告警，不继续删除最近份数。`clean --keep 5 --max-mib 1024` 可调整本次手动清理参数，自动清理使用默认值。

工具保留 `latest` 会话、当前用户进程打开的文件及其会话和最近一小时修改的日志；无法检查进程时跳过过期日志删除，并输出警告。ROS 根目录的 `.log` 及其编号轮转文件也按最近 3 个文件清理，与会话共用 512 MiB 预算。预算是容量告警线，不是磁盘配额：活跃文件、保护期内日志和两次检查之间的增长仍可超过预算。采样数据、pcap、QGC tlog、系统 journal 和其他应用目录不在清理范围内。需要长期保存的日志包应下载到本机。

自动清理默认关闭，须显式执行 `usvlogs auto on`。它不需要 sudo，以启用命令的用户运行，退出 SSH 后仍由 cron 执行。先用 `systemctl is-active cron` 确认服务已启动；运行记录可用 `journalctl -t usv-logs` 查询。自定义日志位置时，用 `usvlogs --ros-dir /path/to/log auto on`，该路径会写入定时任务；默认遵循 `ROS_LOG_DIR`、`ROS_HOME`。只以运行 USV 的普通用户启用，不要用 root 管理其他用户的 ROS 日志。

Windows 更新根仓库与 ROS 仓库后，双击根目录 `download_jetson_logs.bat`，输入 Jetson IP 和日志包名称即可通过 SCP 下载。默认用户为 `jetson`，远端工作区为用户目录下的 `usv_ws`，本机保存到根目录 `.usv_run/downloads/`。首次连接需核对 SSH 指纹，使用密码认证时还需输入密码。下载失败会报错，不留下冒充成功包的文件，也不覆盖已有包。

非默认用户名或工作区可运行：

```powershell
powershell -NoProfile -File .\src\usv_ros\scripts\download_jetson_logs.ps1 -User boat -RemoteWorkspace /home/boat/usv_ws
```

Windows 需要 OpenSSH Client（`scp`）。该入口下载已经由 `usvlogs pack` 生成的包，不会远程启动设备、打包或清理。
- 离线验证：`python -B tests/run_non_map_tests.py`。地图测试按要求跳过，设备烧录、Jetson/台架/实船验收由操作者执行；PID 分阶段超时需要同步更新 ROS 和检测板固件。

## safety1 成套升级注意

本版本需匹配带 `CAP=WATCHDOG1` 的 DetFirmware，旧固件会拒绝连接；ROS 每 500 ms 续约设备心跳，设备超过 3 秒失联停机且不因迟到心跳恢复。所有停止入口取消自动化；重连先取消旧事务。Web/trigger 通过 `ControlCommand.automation_start` 原子装载并启动本次步骤，`sampling_context` 和 `attempt_id` 用于归档及 5 秒 owner 续约。

终态通过结构化 `automation_status` 的 attempt/source/sample_id 关联，快任务提前终态缓存到启动确认后消费。内部清理通过 `ControlCommand.automation_cleanup` 在同一控制锁内校验 attempt 并停止全部输出，结果为 `stopped/failed/superseded`；旧 owner 清理不能干扰新任务。失败或已锁存的会话故障不算清理成功，人工 31011/STOPALL 仍为全局停止。走航间隔保持采样 owner 续约、进样关闭，停止会取消整个调度，MANUAL/RTL 接管不被迟到失败覆盖。

飞控需更新为采样超时默认 HOLD、支持 `USV_FAIL` 的匹配版本。当前 26 个显示遥测字段，31010..31019 命令号不变；分光超过 2 秒未更新时置无效。`USV_DONE/USV_FAIL` 尚无端到端 ACK 或跨飞控重启会话保证，不能以本地发送成功当作船态证明。

船载热点/LAN 的 API 写操作默认无需登录，现有前端可直接进行分光、泵控和自动采样；跨源/跨站写请求仍拒绝。仅设置进程环境 `USV_WEB_REQUIRE_AUTH=1` 才启用认证，并必须同时配置 `USV_WEB_CONTROL_TOKEN`（至少 16 字符，否则服务拒绝启动）；仅设置 token 不启用认证。认证模式下本机和远程写操作均需 Bearer 或 HTTP Basic（用户名 `operator`，密码为令牌），浏览器先访问 `/api/control/auth`。环境变更后重启 Web 服务。不把令牌保存到源码或配置 JSON。默认模式下任何可达客户端都可调用控制 API，同源检查不能替代身份认证；非可信网络应启用认证并通过 HTTPS/VPN 和网络访问隔离保护。

安全测试：`python -B -m unittest discover -s tests -p 'test_system_safety_contract.py'`、`test_sampling_context_contract.py`、`test_web_control_access.py`；完整验收与整船版本检查见总仓库 `docs/current/70_verification.md`。

提交前审计新增回归：`test_sampling_terminal_contract.py`、`test_sampling_cleanup_ownership.py`，覆盖跨任务终态、早到响应、旧清理 RPC、显式停止和走航租约。Web 记录窗口按 attempt 串行化建立/清理，拒绝启动只回滚自身资源。

### QGC 启动、进样泵与普通停止

bridge 连接 router 后立即发送 component 191 心跳并接收命令；飞控心跳在接收循环中处理。trigger 的 MAVROS 服务初始化在独立线程中执行，采样 owner 每秒续约，启动等待不会覆盖已经开始的采样状态。

QGC 手动点采样采用 Web 全局步骤和自动进样泵策略的 `speed`；缺少自动策略时沿用 `default_speed`。进样仍在泵组准备完成后开启。准备阶段通过现有 `USV_STAT=8` 上报，QGC 主按钮显示“停止采样准备”；进入采样步骤后才提供暂停/恢复。`31011` 在准备阶段和步骤执行期间均全局停止全部泵输出，并核对设备 `PIDQUERY → STOPALL_OK` 回应。

QGC 发送层与面板响应期限一致：分光启动 40 秒（覆盖最多三次配置重试和首帧等待），停止类 `31011/31016/31019` 为 15 秒。期限是等待确认的上限，停止命令点击后即发送。更新 ROS 后重启 trigger/bridge/pump，并运行重新编译的 QGC。

QGC 在总预算内以 1 秒间隔最多发送三份同 session/request/command/参数的帧，不创建新请求。RequestLedger 重放原阶段与结果，不重复执行物理动作；标准 ACK 与 USVE1 可任意先后到达，只有匹配关联事件结束收执等待。

TCP 每轮发送预算仍为 30 ms，从收包处理结束后计时。尚未写出字节的帧延期，ACK 与事件分片完整发送后出队；部分帧发送失败或真实连接错误仍退役连接并停止任务。USVE1 保留分片发送位置，超过 QGC 的 1 秒组包期限后从头重播。

断线取消保留 `transport_session_lost`。重连后 bridge 通过 `recover_only` 查询保存的原请求，trigger 核对执行实例、请求身份和参数，再向新会话重放缓存结果或后续终态。同身份重试也走该校验，不重复执行 START。bridge/trigger 必须一起更新并重启；QGC 更新后显示通信取消的中文原因。飞控与检测板本轮无协议变更。

回归入口：`test_mavlink_tcp_transport.py`、`test_payload_transport_recovery.py`、`test_qgc_payload_startup.py`、`test_bridge_qml_event_chain.py`。最后一项单独运行于真实 pymavlink 环境，包含发送前预算耗尽与分片中途延期。

回归：`python -B -m unittest discover -s tests -p test_qgc_payload_startup.py`。台架验收：ROS 启动后立即使用 QGC 启动信号；对比 Web/QGC 相同配置的进样速度；分别在准备、运行、暂停阶段点击普通停止，确认泵组与进样泵停止且记录窗口关闭。

### 分光冷启动与现场诊断

`connect()` 的成功仅表示串口已打开、设备身份与会话握手通过；发布 `serial_connected`，不会以 `connected` 暗示 I2C/ADS 配置成功。Web 的 Automation 和监控页常驻显示串口、I2C/ADS 运行配置、采集状态、事务阶段、最近错误及重试记录。`ready` 只表示 I2CMAP 和 ADSCFG 回显已验证；只有 ADSSTART ACK 后收到有效真实帧才表示启动成功。

- 新串口会话握手后等待 0.5 秒稳定期，独立 watchdog keepalive 持续运行。
- 一次点击执行 `I2CMAP → ADSCFG → ADSSTART → 首个有效帧`。瞬时 I2C 错误或 ACK 超时最多尝试 3 次，退避 0.15 / 0.30 秒；每次重新发送并校验配置回显。参数错误、回显不匹配、控制器故障直接失败。
- 重试前消费独立 `ADS_STATUS:` 响应，排空前次迟到 ACK；ADSSTART 失败先确认 ADSSTOP。清理失败不重试；响应同步失败要求重连，避免后续点击误用旧 ACK。固件文本命令按顺序处理是该屏障的协议依据。
- ACK 预算仍为 2 秒。冷、暖启动首帧预算均至少为 4 秒（从 START ACK 计时）；运行数据 freshness 使用 `measurement_timeout`（默认 2 秒）。无效、模拟、过期或截止后到达的帧不算成功。4 秒是有界启动容错预算，仍须在 Jetson/ESP32 上验证实际延迟分布。
- 失败阶段分别见 `serial_failed`、`i2c_map_failed`、`ads_config_failed`、`start_failed`、`frame_timeout`；`spectrometer_last_txn_error` 保留主要原因，重试恢复后该字段清空，但本次重试记录仍可展开查看。自动化 `terminal_reason` 保持独立。

回归：`python -B -m pytest tests/test_spectrometer_cold_start.py tests/test_field_transactions.py tests/test_terminal_reasons.py`；前端：`cd frontend && node --test scripts/test-spectrometer-diagnostics.mjs`。部署时更新前端构建产物并重启 `pump_control_node`、`web_config_server`；本次没有更改串口协议或固件。实机验收：冷上电一次启动、前两次瞬时 I2C 失败的同次点击恢复、配置失败显示、串口拔插、首帧延迟/缺失、运行断流及物理输出停止。

### PID 分阶段超时与采样等待

- DetFirmware `2026.10.25-pid-phases1` 每轴单次 PID 总上限为 600 秒。首次进入剩余解环误差 10° 以内后，另计 180 秒收敛时间，两项期限先到者生效。进入 1°、回摆或其他轴完成都不重置时钟；小行程从首次控制周期开始收敛计时。0.1° 预处理定位精度和真实 `PID_DONE` 要求不变。
- 正式步骤、独立回零和 preflight 共用 `/automation/pid_wait_timeout`（默认 610 秒，节点私有 `~pid_wait_timeout` 可覆盖，范围 1～1800 秒）。主机使用单调时钟，并给固件总上限留 10 秒回传余量；`PID_TIMEOUT/PID_FAIL` 仍立即失败。已有私有参数若小于 610 秒，仍会提前结束主机等待。旧固件保留 30 秒限时，部署时必须同步更新检测板。
- 新分光平均样本等待默认 10 秒；冷、暖启动首帧至少等 4 秒。运行断流 2 秒仍置数据无效，不用旧值完成步骤；断流超过 `spectro_stale_stop_timeout`（默认 10 秒，launch 可覆盖，最大 60 秒）才终止整次任务。该恢复窗口内输出可能继续运行；不是对无效测量的放行。明确的控制器故障、失联、owner 丢失和停止路径不变。
- 油相行程阶段允许 120 秒无净正向进展，累计前进至少 0.1° 才更新进展时钟。首次进入剩余 10° 或收到本轴匹配运动的 `PID_SETTLE` 后，不再要求持续正向运动，由固件收敛期限和主机总等待兜底；真实 `PID_DONE` 与最终行程核验仍为必需。角度反馈中断超过 1 秒、跳变或传感器异常仍结束准备。日志保留 `PID_START`、`PID_SETTLE`；板端超时带 `phase=total|settling`、总耗时和收敛耗时，`Preflight failed` 记录 attempt 与任务阶段。
- 离线回归：`python -B -m unittest discover -s tests -p test_sampling_timeout_budget.py`。台架先验证 STOP、断线与低负载慢速运行，再检查进入 10° 后的实际收敛耗时。停止系统后更新 ROS 并烧录匹配固件，重启后核对板端版本与 PID 等待参数；Agent 离线测试不包含烧录或实机运行。

[![ROS Noetic](https://img.shields.io/badge/ROS-Noetic-22314E?logo=ros&logoColor=white)](https://wiki.ros.org/noetic)
[![Python](https://img.shields.io/badge/Python-3.8-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MAVLink](https://img.shields.io/badge/MAVLink-v2-0B7285)](https://mavlink.io/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-7-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`usv_ros` 是水质监测无人船的船载 ROS 载荷仓库，目标运行环境是 Jetson Nano / Ubuntu 20.04 /
ROS Noetic。它把检测装置、Web 控制台、MAVROS 和定制 MAVLink 链路组织成一个可部署的载荷系统。

这个仓库不负责飞控固件和 QGC UI 的编译，但它处在三端链路的中间：上接 Pixhawk/ArduRover 与
QGroundControl，下接 ESP32 检测装置主控。任何 MAVLink 字段或命令变更都必须同步核对
`ardupilot-usv/` 固件和 `WQ-USV-QGroundControl/` 自定义面板。

中文文档：`README.md`

English version: `README.en.md`

## 目录

- [系统定位](#系统定位)
- [运行拓扑](#运行拓扑)
- [仓库现状](#仓库现状)
- [目录结构](#目录结构)
- [环境准备](#环境准备)
- [启动与部署](#启动与部署)
- [配置模型](#配置模型)
- [ROS 接口](#ros-接口)
- [Web 控制台与 API](#web-控制台与-api)
- [MAVLink 链路](#mavlink-链路)
- [实验仿真与坐标 Schema v2](#实验仿真与坐标-schema-v2)
- [检测装置串口协议](#检测装置串口协议)
- [数据与日志](#数据与日志)
- [验证](#验证)
- [故障排查](#故障排查)
- [开发约束](#开发约束)
- [License](#license)

## 系统定位

`usv_ros` 在船载计算机上承担以下职责：

- 控制 ESP32 检测装置主控：四路步进泵 `X/Y/Z/A`、进样泵 PWM、角度流和 ADS 分光采样。
- 管理采样自动化：Web 下发多步骤采样序列，ROS 侧执行、暂停、恢复、停止，并记录任务数据。
- 提供 Web 控制台：Flask + Socket.IO 后端，React/Vite 前端，支持实时监控、硬件设置、航点采样配置、
  数据查看、日志查看和链路诊断。
- 接入飞控任务链路：通过 MAVROS 读取状态、切换模式；通过 `mavlink-routerd` TCP 端点发送载荷遥测、
  接收 QGC/飞控自定义指令。
- 向定制 QGC 回传载荷状态：以 `NAMED_VALUE_FLOAT` 上报电压、吸光度、泵角度、采样进度、PID 状态等字段。

默认主链路由 `launch/usv_bringup.launch` 启动：

| 节点 | 脚本 | 作用 |
|---|---|---|
| `/mavros` | `mavros/launch/apm.launch` | 飞控状态、任务、模式切换服务 |
| `/pump_control_node` | `scripts/pump_control_node.py` | 串口握手、泵控、自动化、进样泵、ADS 分光采集 |
| `/web_config_server` | `scripts/web_config_server.py` | Web 页面、REST API、Socket.IO、配置与数据记录 |
| `/system_health_node` | `scripts/system_health_node.py` | Jetson、ROS 节点与 ESP32 主控健康聚合 |
| `/usv_mavlink_bridge` | `scripts/usv_mavlink_router_bridge.py` | router TCP 端点上的 MAVLink 遥测、命令、ACK 桥接 |
| `/mavlink_trigger_node` | `scripts/mavlink_trigger_node.py` | 采样命令解释、任务阶段状态机、航点采样控制 |

`scripts/mission_coordinator_node.py` 仍保留在仓库内，但当前不在默认 launch 主链路中。

## 运行拓扑

```text
QGroundControl / 定制 USV 面板
  |  Plan: MAV_CMD_NAV_SCRIPT_TIME(param1=1)
  |  Manual: COMMAND_LONG 31010..31019
  |  NAMED_VALUE_FLOAT 载荷字段
  v
数传电台 -> Pixhawk 6C / 定制 ArduRover
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
ESP32 检测装置主控
  |-- X/Y/Z/A 步进泵
  |-- 进样泵 PWM
  |-- MT6701 角度采集
  |-- ADS122C04 分光采样
```

当前稳定链路不是“QGC 直连 ROS”。飞控固件会缓存来自伴随计算机的 `NAMED_VALUE_FLOAT` 载荷字段，并以
2 Hz 受控转发到 GCS；这避免了 MAVLink routing 对直转发的阻断。

## 仓库现状

| 指标 | 当前值 |
|---|---:|
| ROS Python 脚本 | 13 |
| Linux 运维脚本 | 15 |
| Web API 路由 | 52 |
| 前端 TypeScript/TSX 文件 | 29 |
| 单元测试文件 | 2 |
| 已提交前端构建产物 | 4 |
| 稳定标签 | `v0.2.0-stable` |
| 远端仓库 | `https://github.com/MIGO-OvO/usv_ros.git` |

已实现的核心能力包括：

- `mavlink-routerd` 串口复用：MAVROS 与自定义 bridge 分离。
- 26 个 QGC 载荷遥测字段（含 `USV_LOOP/LTOT/ADS/PROTO`）：`USV_VOLT`、`USV_ABS`、`PUMP_X/Y/Z/A`、`USV_STAT`、`USV_PKT`、
  `USV_STEP`、`USV_STOT`、`USV_SCNT`、`USV_PERR`、`USV_PMOD`、`USV_BSET`、`USV_REF`、`USV_BASE`、`USV_VLD`。
  其中 5 个系统健康字段为 `USV_JTMP`、`USV_ETMP`、`USV_JCPU`、`USV_JMEM`、`USV_EHEAP`。
- 检测装置身份握手：`HELLO?` / `DET?` 必须返回 `DET_ID:USV_DETECTOR*`。
- Web 端硬件设置热切换：保存串口参数后调用 `/usv/pump_reconnect` 重连泵控节点。
- 航点采样配置 CRUD、任务配置导入导出、任务数据 JSON 记录和 CSV 下载。
- Web 数据中心跟随 `sampling_started` 自动建档，跟随 `sampling_stopped` / `survey_stopped` 停止记录。
- 开机自启脚本：热点、ROS 主系统、router 与自检统一由 `usv-boot.service` 管理。

## 目录结构

```text
usv_ros/
├── CMakeLists.txt
├── package.xml
├── README.md
├── README.en.md
├── TESTING.md
├── config/
│   └── usv_params.yaml              # launch 加载的 ROS 参数基线
├── docs/current/
│   ├── overview.md                  # 本仓库架构摘要
│   └── roadmap.md                   # 后续路线规划
├── launch/
│   └── usv_bringup.launch           # 主启动入口
├── scripts/
│   ├── pump_control_node.py         # 检测装置串口、泵控、自动化、ADS 数据
│   ├── system_health_node.py        # Jetson/ROS/ESP32 健康聚合
│   ├── web_config_server.py         # Flask/Socket.IO Web 网关
│   ├── mavlink_trigger_node.py      # MAVLink 指令与采样状态机
│   ├── usv_mavlink_router_bridge.py # mavlink-router TCP 桥
│   ├── common_env.sh                # 运行目录、日志、router 公共逻辑
│   ├── start_usv_all.sh             # 后台启动 roscore + router + 主系统
│   ├── stop_usv_all.sh              # 安全停止主系统、router、roscore
│   ├── status_usv_all.sh            # 进程、热点、ROS、MAVROS、bridge 诊断
│   ├── usvctl.sh                    # 全局 usvctl/usvon/usvoff 命令入口
│   ├── install_usv_commands.sh      # 安装 ~/.local/bin 下的全局命令 symlink
│   └── lib/
│       ├── automation_engine.py
│       └── command_generator.py
├── frontend/
│   ├── src/                         # React 控制台源码
│   └── package.json
├── static/dist/                     # Web 前端构建产物
└── tests/
    ├── test_boot_service_scripts.py
    └── test_mavlink_command_compat.py
```

## 环境准备

### 目标环境

- Jetson Nano / Ubuntu 20.04
- ROS Noetic + catkin
- Python 3.8
- Pixhawk 6C / ArduRover 定制固件
- ESP32 检测装置固件
- Node.js 18+，仅在需要重构建 Web 前端时使用

### ROS 与系统依赖

在 Jetson 或 Ubuntu/WSL 的 ROS 工作空间根目录执行：

```bash
cd ~/usv_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make
source devel/setup.bash
```

建议加入 shell 初始化：

```bash
echo "source /opt/ros/noetic/setup.bash" >> ~/.bashrc
echo "source ~/usv_ws/devel/setup.bash" >> ~/.bashrc
```

常用依赖：

```bash
sudo apt update
sudo apt install ros-noetic-mavros ros-noetic-mavros-extras mavlink-router python3-pip
sudo /opt/ros/noetic/lib/mavros/install_geographiclib_datasets.sh
python3 -m pip install pyserial flask flask-cors flask-socketio eventlet pymavlink
```

如果发行版包名不同，只要系统中能找到 `mavlink-routerd` 即可；也可以通过环境变量
`MAVLINK_ROUTERD_BIN` 指定二进制路径。

### 前端构建

仓库已包含 `static/dist/` 构建产物。仅在修改前端时需要重新构建：

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm install
npm run build
```

Vite 的输出会写入 `../static/dist`，由 `web_config_server.py` 直接提供。

### 首次脚本赋权

```bash
cd ~/usv_ws
chmod +x src/usv_ros/scripts/*.sh src/usv_ros/scripts/*.py
```

## 启动与部署

### 全局命令

如需像 `clashon` / `clashoff` 一样在任意目录管理船载 ROS 系统，可安装全局命令到
`~/.local/bin`：

```bash
cd ~/usv_ws
bash src/usv_ros/scripts/install_usv_commands.sh install
```

安装脚本会创建以下 symlink，不需要 sudo：

```bash
usvon       # 启动 roscore + mavlink-routerd + usv_ros
usvoff      # 停止 usv_ros + mavlink-routerd + roscore
usvstatus   # 查看进程、热点、ROS、MAVROS、bridge 状态
usvhotspot  # 启停/查看备用 Wi-Fi 热点
usvaddr     # 查看 SSH 与 Web 端口转发地址
usvrestart  # 停止后重新启动，参数透传给 roslaunch
usvupdate   # 在 usv_ros 自动 stash 本地改动，再 git pull --ff-only
usvbuild    # 在 ~/usv_ws 执行 catkin_make，参数透传给 catkin_make
usvdeploy   # stop -> update -> chmod -> build -> start
```

全局命令通过 Bash 调用下级 Shell 脚本，不依赖这些 `.sh` 文件的执行位，
避免更新或 stash 恢复仓库权限后再次出现 `Permission denied`。
入口 `usvctl.sh` 的执行位由安装器设置，Git 也记录为可执行。
这不替代文件读取、目录访问或设备权限；热点启停仍保留原有 sudo 授权，
不需要配置全局免密 sudo。ROS Python 节点的执行权限仍按首次赋权/部署步骤处理。

旧版本遇到 `usvstatus: line 92: ... Permission denied` 时，可先无需赋权查看状态：

```bash
bash ~/usv_ws/src/usv_ros/scripts/status_usv_all.sh
```

同步包含此修复的代码后，执行一次上述安装命令，再用 `usvstatus` 验证。

也可以使用统一入口：

```bash
usvctl start
usvctl stop
usvctl restart web_port:=5050 pump_port:=/dev/ttyUSB1
usvctl status
usvctl hotspot status
usvctl addr
usvctl update
usvctl build -j2
usvctl deploy
```

`usvupdate` 和 `usvbuild` 会在主系统运行时拒绝执行，避免一边拉代码或编译，一边运行旧节点。
需要现场更新并重启时使用 `usvdeploy`，或先执行 `usvoff`。

`usvupdate` / `usvdeploy` 更新前会自动用 `git stash push --include-untracked` 备份
已跟踪文件的工作区、暂存区改动和未跟踪文件，再通过 `git pull --ff-only` 同步当前分支配置的远端上游。
备份带有 `usvctl auto-stash before update` 时间标记，更新后保留，不自动恢复本地改动，
确保构建使用更新后的代码。Git 忽略的数据、日志和缓存不纳入 stash。
干净工作区不创建新备份，已有 stash 不删除；备份或拉取失败会停止部署，不继续构建和启动。
本地已提交的分叉仍会被 `--ff-only` 拒绝，不会自动丢弃提交。

命令会打印备份提交号和查看/恢复命令，也可手动查看：

```bash
git -C ~/usv_ws/src/usv_ros stash list
# 将 <备份提交号> 替换为更新日志中的值；恢复可能与新代码冲突，先 usvoff
git -C ~/usv_ws/src/usv_ros stash show --stat <备份提交号>
git -C ~/usv_ws/src/usv_ros stash apply --index <备份提交号>
```

首次同步此功能时，Jetson 仍使用旧版更新脚本；在本次改动发布到远端后，先手动备份并拉取一次：

```bash
usvoff
cd ~/usv_ws/src/usv_ros
git stash push --include-untracked -m "manual backup before usvdeploy auto-stash update"
git pull --ff-only
usvdeploy
```

后续可直接运行 `usvdeploy`。

如果 `~/.local/bin` 不在 `PATH`，安装脚本只会打印需要加入 `~/.bashrc` 的命令，不会自动修改用户配置。
卸载：

```bash
./src/usv_ros/scripts/install_usv_commands.sh uninstall
```

### 完整船载启动

推荐用脚本启动完整链路。脚本会确保运行目录存在，必要时后台启动 `roscore`，启动 `mavlink-routerd`，
最后启动 `roslaunch usv_ros usv_bringup.launch`。

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_usv_all.sh
./src/usv_ros/scripts/status_usv_all.sh
```

默认 router 参数来自 `scripts/common_env.sh`：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FCU_UART_DEVICE` | `/dev/ttyTHS1` | Jetson 接 Pixhawk TELEM2 的串口 |
| `FCU_UART_BAUD` | `921600` | 飞控 MAVLink 串口波特率 |
| `ROUTER_MAVROS_UDP` | `127.0.0.1:14550` | MAVROS UDP 端点 |
| `ROUTER_BRIDGE_UDP` | `127.0.0.1:14551` | 预留 bridge UDP 端点 |
| `ROUTER_TCP_PORT` | `5760` | bridge 默认 TCP 端点 |
| `WEB_PORT` | `5000` | Web 控制台端口 |

示例：现场串口不是 `/dev/ttyTHS1` 时覆盖：

```bash
cd ~/usv_ws
FCU_UART_DEVICE=/dev/ttyUSB1 FCU_UART_BAUD=921600 ./src/usv_ros/scripts/start_usv_all.sh
```

脚本也会透传 launch 参数：

```bash
./src/usv_ros/scripts/start_usv_all.sh web_port:=5050 pump_port:=/dev/ttyUSB1
```

停止、重启和查看日志：

```bash
./src/usv_ros/scripts/stop_usv_all.sh
./src/usv_ros/scripts/restart_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
tail -f ~/usv_ws/.usv_run/logs/mavlink_router.log
```

### 直接 roslaunch

适合开发调试。注意：直接 `roslaunch` 不会替你启动 `mavlink-routerd`。

```bash
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

无飞控、只调试泵控和 Web：

```bash
roslaunch usv_ros usv_bringup.launch \
  enable_mavros:=false \
  enable_mavlink_trigger:=false \
  enable_mavlink_bridge:=false
```

前台启动主系统但由脚本保证 router 已就绪：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/start_ros_master.sh
./src/usv_ros/scripts/start_usv_system.sh
```

### Web 访问

- 局域网：`http://<Jetson-IP>:5000`
- 热点模式：`http://10.42.0.1:5000`
- 本机调试：`http://127.0.0.1:5000`

调试页面来源：

```bash
curl http://127.0.0.1:5000/api/ui/debug
```

### 热点

手动创建 WPA-PSK 热点：

```bash
cd ~/usv_ws
HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 usvhotspot on USV_Control 12345678
usvhotspot status
usvhotspot off
```

默认热点连接名为 `USV_AP`，默认地址为 `10.42.0.1`。可通过 `HOTSPOT_IFACE`、`HOTSPOT_CONN_NAME`、
`HOTSPOT_IP`、`HOTSPOT_ROUTE_METRIC`、`HOTSPOT_BAND`、`HOTSPOT_CHANNEL` 覆盖。默认双网卡角色为
`INTERNET_IFACE=wlan0`、`INTERNET_BAND=2.4g`、`HOTSPOT_IFACE=wlan1`、`HOTSPOT_BAND=5g`、
`HOTSPOT_CHANNEL=149`；若现场 5GHz AP 受地区码或驱动限制，可改用
`HOTSPOT_BAND=2.4g HOTSPOT_CHANNEL=6`。

推荐现场使用双网卡并行：USB Wi-Fi 作为热点网卡，板载 Wi-Fi、网线或手机 USB 共享作为外网上游。

```bash
nmcli dev status
nmcli dev wifi connect "<外网SSID>" password "<外网密码>" ifname wlan0
nmcli connection modify "<外网SSID>" 802-11-wireless.band bg

cd ~/usv_ws
INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 usvhotspot on USV_Control 12345678
ip route
./src/usv_ros/scripts/status_usv_all.sh
```

热点连接会设置 `ipv4.never-default=yes`、`ipv6.never-default=yes` 和较高 route metric，避免 `USV_AP`
抢默认路由。`setup_hotspot.sh` 默认拒绝把当前默认路由接口切为热点，除非显式设置
`HOTSPOT_ALLOW_INTERNET_IFACE=true`。热点脚本会把 `INTERNET_IFACE` 上的活动 Wi-Fi profile 设为
`INTERNET_BAND=2.4g`；默认不主动重连，避免断开当前 SSH。`status_usv_all.sh` 会输出
`internet: ... source=external ... band=2.4g target_band=2.4g ...`，用于确认默认路由仍走外网接口且频段正确。

### 开机自启

安装 systemd 服务：

```bash
cd ~/usv_ws
sudo ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

开机自启默认不拉起热点，只启动 ROS/Web，方便 Nano 接入现有局域网后用 SSH/Web 隧道访问。查看当前可用地址：

```bash
usvaddr
```

双网卡并行安装推荐指定热点网卡：

```bash
cd ~/usv_ws
sudo USV_ENABLE_HOTSPOT=true INTERNET_IFACE=wlan0 INTERNET_BAND=2.4g HOTSPOT_IFACE=wlan1 HOTSPOT_BAND=5g HOTSPOT_CHANNEL=149 ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

安装脚本也会把活动外网 Wi-Fi 连接的 NetworkManager profile 设为 2.4 GHz；默认不主动重连，避免断开当前 SSH。
如需安装后立即切到 2.4 GHz，可加 `INTERNET_WIFI_RECONNECT=true`。

安装脚本默认只写入并启用 `usv-boot.service`，不会在安装阶段立即启动整套 ROS/热点链路，避免因现场硬件或网络尚未就绪导致安装报错。
如需安装后立刻启动并验证服务：

```bash
cd ~/usv_ws
sudo USV_BOOT_START_NOW=true ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

如果只通过 SSH 访问 Web 配置页，可以关闭自启热点，只保留 ROS/Web 自启：

```bash
cd ~/usv_ws
sudo USV_ENABLE_HOTSPOT=false ./src/usv_ros/scripts/install_boot_service.sh
```

电脑侧通过 SSH 端口转发访问：

```bash
ssh -N -L 5000:127.0.0.1:5000 jetson@<Jetson_IP>
```

浏览器打开 `http://127.0.0.1:5000`。如果本机 5000 被占用，可改成 `-L 5050:127.0.0.1:5000`
并访问 `http://127.0.0.1:5050`。

服务启动顺序：

1. 默认跳过热点；`USV_ENABLE_HOTSPOT=true` 时创建/恢复热点。
2. 以安装脚本调用者作为运行用户启动 `start_usv_all.sh`。
3. 等待 Web、ROS 节点、MAVROS 和 bridge 诊断就绪；启用热点时额外等待热点就绪。
4. 自检结果写入 `~/usv_ws/.usv_run/logs/boot_check.log`。

常用命令：

```bash
sudo systemctl status usv-boot.service
sudo journalctl -u usv-boot.service -f
sudo systemctl restart usv-boot.service
sudo ./src/usv_ros/scripts/uninstall_boot_service.sh
```

现场调试并立即启动服务时可临时放宽严格自检：

```bash
sudo USV_BOOT_START_NOW=true USV_STRICT_SELF_CHECK=false ./src/usv_ros/scripts/install_boot_service.sh USV_Control 12345678
```

## 配置模型

配置分为三层，不要混用：

| 类型 | 路径 | 作用 |
|---|---|---|
| ROS 静态参数 | `config/usv_params.yaml` | launch 时加载，定义泵、I2C、ADS、MAVROS 参考参数 |
| Web 运行配置 | `~/usv_ws/config/sampling_config.json` | Web 保存的采样步骤、航点采样、硬件设置 |
| 校准数据 | `~/usv_ws/config/calibration.json` | Web 角度零点校准 offset |

`web_config_server.py` 的“保存并应用”硬件设置流程：

1. 写入 `sampling_config.json` 的 `hardware` 段。
2. 更新 `/pump_control_node/serial_port`、`/pump_control_node/baudrate`、`/pump_control_node/timeout`。
3. 调用 `/usv/pump_reconnect`。
4. `pump_control_node.py` 重新打开串口并执行检测装置身份握手。

### Automation 启动前准备（Preflight）

每次任务先完成以下准备，再进入现有 `AutomationEngine`；序列内部循环不重复准备：

1. 自动统计本次 steps 中 `enable: "E"` 的 X/Y/Z/A（包括连续转动步骤）。
2. 各相关轴以最短路径回到**预先保存的相对零点**，随后全部正转 360° 补偿液体位移。使用相对 `R` 指令，分别等待匹配的 `PID_START`、`CMD_OK` 和各轴 `PID_DONE`；不会发送归绝对 0° 的 `CAL` 或改写零点。归位、360° 补偿及油相分隔的独立定位容差固定为 0.1°，覆盖 MT6701 的 14-bit 量化步长 `360/16384 ≈ 0.022°` 及少量噪声；正式步骤仍使用用户指定的 PID 精度。
3. 油相泵按配置额外正转分隔圈数，使用与回零、360° 补偿相同的相对角度 `R/PID`。启动时 `PIDQUERY` 读取设备实际速度上下限，按 `PIDPARAM` 的一位小数精度取保守最低速度 `max(0.1, out_min - 0.05)` rpm，每段角度不超过该速度对应的 360 秒行程且最多 360°，在板端 600 秒总上限内预留 180 秒收敛与 60 秒启动余量。最低速度为 1 rpm 时五圈分为五段；0.1 rpm 时每段最多 216°。每段均等待匹配的 `PID_START`、`CMD_OK`、真实 `PID_DONE`，并核验角度解环行程，之后才发送下一段。不在 steps 中的油相轴也执行同样核验。主机每段行程容差为 `min(0.5°, 段角度 × 5%)`，PID 定位容差仍为 0.1°。分隔阶段不发送开环 `J` 指令。
4. 按进样泵任务策略预启动，确认 `PUMP_OK:SET=…,ON` 并等待 lead time；完成后才启动正式序列。受管进样在归位前先确认 OFF。Web 直接启动仍按其 automation 策略；trigger 发起的 Survey 每次采样将进样策略纳入同一准备事务，使用已配置联动速度和 lead time。Survey 启动/停止广播不会控制硬件，每次采样结束停机，间隔期间进样关闭；Survey 停止按 attempt 清理整个采样事务，不影响新 owner。

当前 DetFirmware 的 `0xCC` 角度帧没有有效位或源时间戳；传感器失效时会重复 last_valid，`ANGLE_AGE_CH_MS=0` 也不能单独证明有效。Preflight 使用逐轴 monotonic 接收时刻（1 秒断流预算）、有限/范围校验、通道 age 和独立健康包时间戳（2.5 秒预算，重复/倒退不续约，支持 uint32 回绕）。油相行程阶段需在 120 秒内累计至少 0.1° 的净正向进展；相同帧或固定位置附近的小抖动不能续约计时。进入收敛后允许反向纠偏，角度与健康新鲜度检查继续生效。以上检测失败均停止输出；机械停机延迟需台架测量。

`automation_start` 返回本次 `attempt_id` 与 `admission`：`rejected` 表示未准入，`owned` 表示泵节点已接管该 attempt；RPC 异常或身份不匹配按 `unknown` 处理。启动失败首因保留在 QGC 关联结果、日志、`sampling_result` 与采样记录中，清理另存 `cleanup_result`。明确未准入仍登记取消，阻止迟到 START；未知结果按 attempt 核验，当前 owner 匹配才执行停止，其他 owner 不受影响。`not_admitted/not_owned` 表示已登记取消且当前无本 attempt 的输出归属；`superseded` 表示其他 owner 已接管；`stopped` 才表示本 attempt 停止确认成功。航行 Manual 与泵手动模式独立，泵手动模式、控制器故障、忙碌和非法配置分别返回具体原因。

未到 `sampling_started` 就失败的 attempt，Web 按匹配的上下文和最终结果建立失败记录；支持两者任意到达顺序，重复或旧 owner 结果不重复建档。任务 JSON 与 `sample_results.csv` 保存首因、`start_admission` 和独立 JSON 格式的 `cleanup_result`，不附加有效测量。

独立校准期间，Web `/api/injection-pump/off` 与 ROS `/usv/injection_pump_off` 只关闭进样泵，校准继续等待各轴 PID 完成。取消校准使用全局 STOP，沿用轴停止确认与取消终态；独立校准只回保存零点，自动化继续执行回零、360° 补偿、油相分隔、进样和正式步骤。

Web 自动化页的“启动前准备”可编辑相对零点、油相轴和分隔圈数；分隔速度使用设备 PID 上下限。桌面并列显示零点与分隔参数，窄屏纵向排列。运行期间配置锁定，Preflight 只能停止，不能暂停/恢复。QGC 的 31012/31013 ACK 和 mission 状态仅在泵服务成功后更新；准备阶段的拒绝返回失败 ACK，不显示假暂停/恢复。阶段和待完成轴通过 `automation_status.preflight` 显示；准备期间 `running=true`、`automation_step=0`。启动 RPC 成功表示接受准备事务，终态仍由关联 attempt 的状态决定。

QGC 31011 的全局停止和 trigger 内部按 attempt 的清理通过已有 `ControlCommand` 请求携带可选 `confirm_stop: true`：先执行原停止输出，再用 `PIDQUERY → PIDPARAM` 排空旧响应，随后等待最终 `STOPALL → STOPALL_OK` 确认固件已关闭电机、PID/校准/测试和进样。串口写入成功不能单独生成成功 ACK；清理/确认超时、异常或已锁存故障返回失败、mission 为 FAILED，保留原故障原因。原命令号、ACK 结构、串口指令及不带该标记的旧 ROS 调用兼容；部署须更新 pump 与 trigger。固件确认表示执行了停机逻辑，机械惯性/电气输出仍需台架测量。

Survey 启动 RPC 与 31011/31016 停止及收尾共用独立事务锁，STOP 成功前等待已发出的启动 RPC 返回并完成清理；等待期间 `state_lock` 保持可用，遥测与 owner 回调照常运行。每轮 Survey 调度持有独立 generation，旧调度不能向重启后的 Survey 分派采样或清除其状态。首轮前未取得泵 owner 时停止只收尾本地调度；不会用未拥有的 attempt 停止其他任务。已取得 owner 时仍按 acquisition 的 attempt 清理，并保留 superseded 隔离。

配置保存在 `sampling_config.json` 的 `pump_settings.preflight`：

```json
{"oil_axis": "A", "separation_turns": 2, "separation_rpm": 5}
```

默认圈数为 0（跳过分隔，油相轴可为空）；启用分隔时圈数为 0.01–10，轴可独立于 steps 中的相关泵选择。旧 `separation_rpm` 字段继续读写以兼容已有配置，不参与运动。设备 `out_min` 范围为 0.1–5 rpm，`out_max` 为 1–20 rpm，且最低值须不大于最高值；固件默认值为 1/6 rpm，以实际 `PIDPARAM` 为准。`R` 使用 `computePIDDirect`，大误差时可达当前 `out_max`，未使用另一条平滑启动算法；ROS 不覆盖设备 PID 参数，起转能力与负载下的最低速度仍需台架确认。零点复用 `calibration.json` 的 `offsets`，可通过 `GET/POST /api/calibration/offsets` 读取/保存原始角度，范围为 `[0, 360)`。新增 `configured_axes` 区分未设零与显式保存的 0°；无此标记的旧文件只兼容非 0° 的已保存 offset，默认 0° 无法证明曾标定，须在 Web 明确保存确认。读旧文件不会重新设零或改写文件。未保存过相关轴零点时任务失败，不回退到绝对 0°。泵节点可用私有参数 `~calibration_file` 指定同一零点文件。启动和保存请求的部分 `pump_settings` 先递归合并，再校验完整配置，未提供的 PID、进样及分隔参数保持原值；无效请求回滚且不分派启动。

任一步骤 ACK/PID 超时、角度无效/断流、owner 丢失、watchdog 或串口故障均走现有停泵和终态清理；清理失败继续锁存故障。PID 控制算法、安全租约与串口恢复协议保持原有行为；GPS 仅作为可选记录位置，详见下文。分阶段限时需要同步更新 ROS 与 DetFirmware；前端无需为此重建。

回归：`python -B -m pytest tests/test_preflight_stop_races.py tests/test_preflight_review_regressions.py tests/test_automation_preflight.py tests/test_preflight_web_contract.py tests/test_system_safety_contract.py tests/test_terminal_reasons.py tests/test_sampling_cleanup_ownership.py`；前端：`cd frontend && npm run test:automation-controls && npm run build`。实机仍需在 Jetson/ESP32 台架确认：非 0° 相对零点、补偿方向及液体位移、跨 0° 的多圈分隔、拔除角度传感器/冻结反馈后的有界停机、PID 速度上下限、分段衔接和小圈数容差、两轮 Survey 进样顺序、0.1° 定位稳定性、启动/停止并发与立即重启，以及每阶段停止/拔串口后的物理输出停止、STOPALL 确认延迟和 QGC 失败 ACK/FAILED 状态。

### launch 参数

`launch/usv_bringup.launch` 当前暴露的参数如下：

| 分组 | 参数 |
|---|---|
| 泵控 | `pump_port`、`pump_baudrate`、`pump_timeout`、`pid_mode`、`pid_precision`、`spectro_sample_wait_timeout` |
| Web | `web_host`、`web_port`、`web_ui` |
| MAVROS | `enable_mavros`、`mavros_fcu_url`、`mavros_gcs_url`、`mavros_tgt_system`、`mavros_tgt_component`、`mavros_fcu_protocol`、`mavros_respawn` |
| 采样状态机 | `mavros_timeout`、`hold_settle_time`、`stable_check_timeout`、`stable_speed_threshold`、`stable_yaw_rate_threshold`、`sampling_retry_count`、`sampling_on_fail` |
| MAVLink bridge | `mavlink_source_system`、`mavlink_source_component`、`mavlink_router_url` |
| 节点开关 | `enable_pump`、`enable_web`、`enable_system_health`、`enable_mavlink_trigger`、`enable_mavlink_bridge` |

示例：

```bash
roslaunch usv_ros usv_bringup.launch \
  pump_port:=/dev/ttyUSB0 \
  web_port:=5000 \
  hold_settle_time:=5.0 \
  sampling_retry_count:=1 \
  sampling_on_fail:=SKIP
```

## ROS 接口

### 主要 topic

| Topic | 类型 | 方向 | 说明 |
|---|---|---|---|
| `/usv/pump_command` | `std_msgs/String` | sub | 直接下发检测装置文本命令，如 `XEFR90.0P0.1` |
| `/usv/pump_step` | `std_msgs/String` | sub | 单步采样 JSON |
| `/usv/automation_steps` | `std_msgs/String` | pub/sub | Web 或 trigger 发布整套采样步骤 |
| `/usv/pump_angles` | `std_msgs/String` | pub | X/Y/Z/A 角度 JSON |
| `/usv/pump_status` | `std_msgs/String` | pub | 泵和自动化状态文本/结构化状态 |
| `/usv/automation_status` | `std_msgs/String` | pub | 自动化进度、步骤号、循环、PID 模式 |
| `/usv/pump_pid_complete` | `std_msgs/String` | pub | PID 完成通知 |
| `/usv/pump_pid_error` | `std_msgs/String` | pub | PID 误差 JSON |
| `/usv/injection_pump_status` | `std_msgs/String` | pub | 进样泵状态 JSON |
| `/usv/spectrometer_voltage` | `std_msgs/String` | pub | 分光数据 JSON，包含 `voltage`、`absorbance` 等；实验模拟下发布液滴事件聚合值 |
| `/usv/lab_sim/sample_event` | `std_msgs/String` | pub | 实验定点采样液滴事件明细：`event_id`、`mode`、`droplets[]`、`mean`、`valid_count` |
| `/usv/lab_sim/command` | `std_msgs/String` | sub | 实验仿真控制：`config`、`start`、`stop`、`waypoints` |
| `/usv/lab_sim/status` | `std_msgs/String` | pub | 虚拟船位、航向、运行状态（latched） |
| `/usv/lab_sim/waypoint_reached` | `std_msgs/String` | pub | 虚拟航点到达事件，不污染 `/mavros/mission/reached` |
| `/usv/detector_health` | `std_msgs/String` | pub | ESP32 主控健康 JSON，包含温度、heap、任务栈水位 |
| `/usv/system_health` | `std_msgs/String` | pub | Jetson、ROS 节点和 ESP32 聚合健康 JSON |
| `/usv/spectrometer_status` | `std_msgs/String` | pub | ADS 配置、采集中、错误等状态 |
| `/usv/spectrometer_raw` | `std_msgs/String` | pub | 原始分光包 |
| `/usv/spectrometer_absorbance` | `std_msgs/String` | pub | 吸光度数据 |
| `/usv/mission_status` | `std_msgs/String` | pub | 任务阶段：`IDLE`、`SAMPLING`、`RESUMING_AUTO` 等 |
| `/usv/trigger_status` | `std_msgs/String` | pub | 触发状态：开始、停止、暂停、校准等 |
| `/usv/bridge_diagnostics` | `std_msgs/String` | pub | router bridge 诊断 JSON |
| `/usv/radio_status` | `std_msgs/String` | pub | `RADIO_STATUS` 电台链路质量 |
| `/usv/payload_command` | `std_msgs/String` | pub/sub | 关联 QGC 命令，JSON 含 executor 与原始十元 values |
| `/usv/mavlink_cmd_rx` | `std_msgs/Float32MultiArray` | pub/sub | 飞控内部事件及无关联兼容 STOP |
| `/usv/mavlink_cmd_ack` | `std_msgs/Float32MultiArray` | pub/sub | trigger 请求 bridge 发送 `COMMAND_ACK` |

### 主要 service

| Service | 类型 | 说明 |
|---|---|---|
| `/usv/pump_stop` | `std_srvs/Trigger` | 紧急停泵 |
| `/usv/automation_start` | `std_srvs/Trigger` | 启动当前自动化步骤 |
| `/usv/automation_stop` | `std_srvs/Trigger` | 停止自动化并停泵 |
| `/usv/automation_pause` | `std_srvs/Trigger` | 暂停自动化 |
| `/usv/automation_resume` | `std_srvs/Trigger` | 恢复自动化 |
| `/usv/injection_pump_on` | `std_srvs/Trigger` | 打开进样泵 |
| `/usv/injection_pump_off` | `std_srvs/Trigger` | 关闭进样泵 |
| `/usv/injection_pump_get_status` | `std_srvs/Trigger` | 查询进样泵状态 |
| `/usv/pump_reconnect` | `std_srvs/Trigger` | 按最新 ROS 参数重连检测装置串口 |
| `/usv/spectrometer_start` | `std_srvs/Trigger` | 下发 `ADSSTART` |
| `/usv/spectrometer_stop` | `std_srvs/Trigger` | 下发 `ADSSTOP` |
| `/usv/i2c_map_apply` | `std_srvs/Trigger` | 下发当前 I2C 映射 |
| `/usv/trigger_sampling` | `std_srvs/Trigger` | 手动触发带 HOLD/稳定判定的采样流程 |

常用检查：

```bash
rosnode list
rostopic list | grep /usv/
rostopic echo /usv/pump_status
rostopic echo /usv/bridge_diagnostics
rosservice call /usv/pump_stop
```

## Web 控制台与 API

Web 后端是 `scripts/web_config_server.py`，默认监听 `0.0.0.0:5000`。它既提供前端静态文件，也提供
REST API 和 Socket.IO 实时事件。

### 常用 REST API

| API | 说明 |
|---|---|
| `GET /api/config`、`POST /api/config`、`POST /api/config/reset` | 读取、保存、重置运行配置 |
| `POST /api/mission/start|stop|pause|resume` | 控制采样任务 |
| `GET/POST /api/waypoint-sampling` | 航点采样配置 |
| `GET /api/mission-config/export`、`POST /api/mission-config/import` | 任务配置导入导出 |
| `POST /api/motor/command`、`POST /api/motor/stop` | 手动电机命令 |
| `GET/POST /api/pid/config`、`POST /api/pid/test` | PID 参数与测试 |
| `GET/POST /api/calibration/offsets`、`POST /api/calibration/zero|reset|start` | 相对零点与角度校准 |
| `GET /api/data/voltage`、`POST /api/data/voltage/clear` | 当前内存电压历史 |
| `POST /api/spectrometer/start`、`POST /api/spectrometer/stop`、`POST /api/spectrometer/baseline` | 分光采集启停；baseline 无请求体时沿用当前有效电压，也可通过 `reference_voltage` 写入稳定窗口的平均参考电压 |
| `GET /api/data/missions` | 历史任务文件列表 |
| `GET /api/data/mission/<id>` | 历史任务 JSON |
| `GET /api/data/mission/<id>/samples` | 任务下采样窗口列表，不返回 raw frames |
| `GET /api/data/mission/<id>/sample/<sample_id>` | 单个采样窗口元数据和摘要 |
| `GET /api/data/mission/<id>/sample/<sample_id>/raw` | 读取窗口 raw frames，支持 `limit`、`offset` |
| `GET /api/data/mission/<id>/sample/<sample_id>/raw.csv` | 下载单个采样窗口的完整原始帧 CSV |
| `GET /api/data/mission/<id>/raw.csv` | 下载全任务、跨采样窗口的高频原始分光 CSV |
| `POST /api/data/mission/<id>/sample/<sample_id>/manual-result` | 写入人工浓度、单位、方法、记录人和备注 |
| `GET /api/data/mission/<id>/csv` | 下载低频任务摘要 CSV（地图和趋势展示） |
| `GET /api/data/mission/<id>/archive` | 下载整任务 ZIP，包含任务 JSON、摘要 CSV、全任务原始 CSV、各窗口 JSONL 与原始 CSV |
| `GET /api/data/mission/<id>/geojson` | 历史污染物点位 GeoJSON；可带 `metric=concentration` 与 `download=true` |
| `GET /api/data/mission/<id>/surface` | 历史污染物 IDW surface；可带 `metric`、`size`、`power` 与 `download=true` |
| `GET /api/map/live` | 当前任务实时点位、轨迹、污染物 surface、走航门控状态 |
| `GET/POST /api/lab/config`、`GET /api/lab/status` | 实验模式配置读写与状态快照 |
| `POST /api/lab/start`、`POST /api/lab/stop` | 启停实验虚拟航行仿真 |
| `GET/POST /api/lab/water-area`、`POST /api/lab/mission` | 实验水域多边形与虚拟航线读写 |
| `POST /api/lab/route/auto-scan` | 生成/校验弓字形水域覆盖航线，返回双坐标航点与 `water_snapshot_hash`；`preview=true` 只生成不保存 |
| `POST /api/lab/mission/import-qgc` | 从飞控缓存航点 (`route_waypoints[].wgs84`) 导入为实验虚拟航线 |
| `GET /api/logs/files`、`GET /api/logs/<filename>` | 系统日志查看 |
| `GET /api/hardware/config`、`POST /api/hardware/config` | 硬件连接配置 |
| `GET /api/hardware/serial-ports` | 枚举串口 |
| `POST /api/hardware/test-pump-port` | 打开串口并执行检测装置握手 |
| `POST /api/hardware/apply` | 保存硬件配置并重连泵控节点 |
| `GET /api/diagnostics/link|history|events|export` | 链路诊断 |
| `GET /api/diagnostics/system` | 系统健康最新快照和最近历史 |

### Socket.IO 事件

后端会推送：

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
system_health
```

### 快速 API 验证

```bash
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/config
curl http://127.0.0.1:5000/api/hardware/serial-ports
curl http://127.0.0.1:5000/api/diagnostics/system
curl -X POST http://127.0.0.1:5000/api/injection-pump/on
curl -X POST http://127.0.0.1:5000/api/injection-pump/off
```

硬件配置热应用：

```bash
curl -X POST http://127.0.0.1:5000/api/hardware/apply \
  -H "Content-Type: application/json" \
  -d '{"pump_serial_port":"/dev/ttyUSB0","pump_baudrate":115200,"pump_timeout":1.0}'
```

## MAVLink 链路

### 端点

| 端点 | 默认值 | 用途 |
|---|---|---|
| Pixhawk UART | `/dev/ttyTHS1:921600` | Jetson 与飞控 TELEM2 物理链路 |
| MAVROS | `udp://127.0.0.1:14550@` | 状态、任务、模式切换 |
| bridge | `tcp:127.0.0.1:5760` | 自定义遥测、命令、ACK |

### 下行命令

`usv_mavlink_router_bridge.py` 从 router TCP 端点接收 `COMMAND_LONG`，筛选 `31010..31019`，转成
带执行实例的 `/usv/payload_command`（无关联兼容 STOP 仍走 `/usv/mavlink_cmd_rx`）；`mavlink_trigger_node.py` 执行后发布 `/usv/mavlink_cmd_ack`，再由 bridge 封装
`COMMAND_ACK`。

| 命令 | 含义 |
|---:|---|
| `31010` | 手动开始采样，不作为航线 mission item |
| `31011` | 停止采样 |
| `31012` | 暂停采样 |
| `31013` | 恢复采样 |
| `31014` | 调用 `calibration_start`，按保存零点发送最短相对 R/PID 回零 |
| `31015` | 开始走航采样 |
| `31016` | 停止走航采样 |
| `31017` | 设置分光 baseline，`param1=0` 使用最新有效电压 |
| `31018` | 启动分光检测器信号采集 |
| `31019` | 停止分光检测器信号采集 |

所有真实采样入口均允许无 GPS 启动：QGC 手动点采样（`31010`，`param2=0`）、Web 自动化、
FCU 采样触发、waypoint、走航和 lab_real。GPS 不再是载荷采样的启动条件；
旧 `automation_policy.require_gps`、请求 `require_gps`、`survey_require_gps` 和实验 `allow_no_gps` 均不生效，界面已移除对应开关。

有有效硬件定位时冻结开始位置；无定位、失锁或过期时创建空坐标的真实采样记录，
`position_source=<source>_no_gps`（手动为 `manual_no_gps`）、`simulated=false`。
不使用模拟船位、不从结束位置补写起点，无定位记录不进入地图。
走航没有可用定位时按时间间隔采样，仅在定位有效时检查最小距离；速度、分光及硬件预检仍按配置执行。
飞控导航自身的定位条件独立于载荷采样。

手动命令回执遵循飞控的 `MAV_RESULT` 定义：接受 `0`、忙碌暂拒 `1`、执行失败 `4`；
`5` 表示执行中，不能用于失败，否则 QGC 会继续等待最终回执直至超时。
离线回归：`python3 -B -m unittest discover -s tests -p 'test_manual_sampling_gps_policy.py'`。
跨入口回归：`python3 -B -m unittest discover -s tests -p 'test_sampling_without_gps.py'`。
更新后成套重启 ROS 服务，在无 fix 时点击 QGC 点采样，硬件预检通过后应看到 `sampling_started` 和
`/usv/mavlink_cmd_ack` 的 `[31010,0,...]`，记录坐标为 null；旧配置即使保留 `require_gps=true` 也不得拒绝启动。

航线定点采样使用飞控原生 `MAV_CMD_NAV_SCRIPT_TIME`：

- `param1=1`：USV 定点采样。
- `param2=1..255`：超时保护秒数，QGC 默认 255。
- `param3..param6=0`：v1 保留。

bridge 兼容飞控以 `NAMED_VALUE_FLOAT` 发出的原生任务触发：

- `USV_SMPL=<sample_id>`：触发一次定点采样；只有匹配 ID 的成功结果或明确 SKIP，bridge 才发送 `USV_DONE=<sample_id>`。

### FCU 采样结果与安全边界

- trigger 通过 `/usv/sampling_result`（`std_msgs/String` JSON，非 latched）发布 `source=fcu`、`sample_id`（整数 1..65535）、`outcome`、`reason`。
- `outcome=succeeded` 表示正常完成；`skipped` 仅由 FCU 采样失败且 `sampling_on_fail=SKIP` 产生。二者允许 bridge 发送匹配 ID 的 `USV_DONE`，但 SKIP 提示为 Skipped，不冒充成功。
- `failed` / `cancelled` 不发送 `USV_DONE`。HOLD/ABORT 失败策略、FCU 主动取消请求 HOLD；启动失败同样进入失败策略。FCU 忙碌拒绝只报告失败，不中断正在运行的其他采样。
- `sampling_stopped` 与 pump `finished` 不作为真实任务成功或飞控放行依据。Web 真实窗口等待匹配 `/usv/sampling_result` 最终结果后关窗，保留真实首因和清理结果。自动化引擎正常结束报 `finished`，直接停止报 `stopped`，失败报 `failed`。
- bridge 在下发内部命令前登记 ID；忽略最近同 ID 重复触发、重复结果、ID 不匹配结果、非 FCU 结果和非法结果。新 ID 会清除尚未发送的旧完成通知。
- FCU 采样仍不启用旧 waypoint 自动重试路径，避免重试丢失脚本 ID；成功完成不由 ROS 延迟切换模式。命令号保持不变，本轮 26 字段与关联协议需匹配新版固件和 QGC。
- **部署要求**：停止系统后，成套更新并重启 trigger、bridge 和 pump（自动化引擎），不要混用新旧进程。旧 bridge 仍会把 `sampling_stopped` 当成完成；新 bridge 配旧 trigger 则不会发送完成通知。
- **剩余边界**：HOLD 请求不是模式确认；飞控 `NAV_SCRIPT_TIME` 自身超时仍可结束等待，本修复不修改该 P1 行为。结果队列不保证端到端可靠送达；飞控重启/16 位 ID 回绕没有跨启动会话标识，重启后应协调重启 ROS 并重新确认任务，不将本修复视为完整的跨重启事务协议。

离线回归：`python3 -m unittest discover -s tests -p 'test_fcu_sampling_result.py'`。现场必须验证成功、启动失败、PID 超时、QGC/Web 停止、HOLD/ABORT/SKIP、重复和迟到消息，并同时采集 `/usv/sampling_result`、`/usv/trigger_status`、`/mavros/state` 与 MAVLink 日志。
- `USV_SURV=1/0`：开启或停止走航采样。

### 上行遥测

bridge 发送：

- `HEARTBEAT`：1 Hz。
- `NAMED_VALUE_FLOAT`：26 个字段按源/字段新鲜度发送，源回调停止后不以缓存续命；飞控按每链路独立游标在 2 Hz 调度中公平转发。

| 字段 | 说明 |
|---|---|
| `USV_VOLT` | 分光电压 |
| `USV_ABS` | 吸光度 |
| `PUMP_X`、`PUMP_Y`、`PUMP_Z`、`PUMP_A` | 保存零点下的相对角度，未校准为 -1；内部控制/诊断保留原始角度 |
| `USV_STAT` | 任务阶段编码 |
| `USV_PKT` | 载荷包计数 |
| `USV_STEP` | 当前自动化步骤号 |
| `USV_STOT` | 自动化总步骤数 |
| `USV_LOOP` / `USV_LTOT` | 当前轮次 / 总轮次 |
| `USV_SCNT` | 采样启动次数，不是成功次数 |
| `USV_ADS` | 采集状态：0 未知、1 停止已确认、2 启动中、3 采集中、4 错误、5 停止未确认 |
| `USV_PROTO` | 关联事件和明确采集状态协议能力版本 |
| `USV_PERR` | PID 误差 |
| `USV_PMOD` | PID 模式：0 空闲、1 运行、2 完成、3 错误 |
| `USV_BSET` | baseline 是否已设置，0/1 |
| `USV_REF` | baseline 参考电压 |
| `USV_BASE` | baseline 电压 |
| `USV_VLD` | 分光检测器当前采样是否有效，0/1 |
| `USV_JTMP` | Jetson CPU/SoC 温度，单位 °C；未知为 `-1` |
| `USV_ETMP` | ESP32 内部温度，单位 °C；未知为 `-1` |
| `USV_JCPU` | Jetson CPU 使用率，单位 `%`；未知为 `-1` |
| `USV_JMEM` | Jetson 内存使用率，单位 `%`；未知为 `-1` |
| `USV_EHEAP` | ESP32 可用 heap 百分比，单位 `%`；未知为 `-1` |

诊断：

```bash
rostopic echo /usv/bridge_diagnostics
rostopic echo /usv/mavlink_cmd_rx
rostopic echo /usv/radio_status
```

## 实验仿真与坐标 Schema v2

实验模式（Lab）在无飞控/检测装置硬件时做半实物仿真，源码集中在 `scripts/lib/lab_sim/`、`scripts/lab_sim_node.py`、`scripts/mavlink_trigger_node.py` 与 `scripts/web_config_server.py`，不改变 MAVLink 命令号和 `USV_SMPL/USV_DONE` 语义；当前载荷字段清单为上文 26 项。lab_real 不能使用仿真或机械调试豁免绕过真实测量门禁。

### 坐标 Schema v2 与 CRS 边界

- 实体统一携带双坐标对：`{"coordinate_schema_version": 2, "wgs84": {lat,lng,alt?}, "gcj02": {lat,lng,alt?}}`。
- WGS-84 是计算、持久化、航行、距离、ENU、污染场和 surface 的唯一真源；GCJ-02 仅用于高德底图显示。
- Web 点击事件 `event.latlng` 标记 `input_crs=GCJ02` 提交，后端是唯一转换源（迭代式 GCJ-02 -> WGS-84 反算），响应同时返回 `wgs84` 与 `gcj02`。
- QGC 导入读取航点的 `route_waypoints[].wgs84`；旧版裸 `{lat,lng}` 按 GCJ-02 迁移到 schema v2，迁移幂等。
- 桂林默认区域 WGS-84 -> GCJ-02 -> WGS-84 round-trip 地面误差 `<=0.5 m`；图面点击/保存航点/船位 marker 中心距离 `<=2 CSS px`。

### 有界液滴采样事件

- 模拟数据源下，每个采样事件生成 `droplet_count` 个液滴（默认 12，范围 `3..64`），聚合为单个地图采样点。
- 持久化粒度：一个事件 = 一次 mission JSON 写入 = 一个 `data_point`，不做逐液滴持久化，也不开原始帧流；`data_point` 携带 `sample_event_id` 与 `droplet_count`。
- `/usv/spectrometer_voltage` 继续发布聚合电压/吸光度/浓度；液滴明细经专用话题 `/usv/lab_sim/sample_event` 发布。
- 采样模式：waypoint / survey / manual_route / auto_scan。

### 自动扫描航线 auto-scan

- `POST /api/lab/route/auto-scan` 生成/校验弓字形水域覆盖航线：内缩水域多边形、最大航点上限、版本化 `water_snapshot_hash`，支持 `preview` 预览与应用两种模式。
- 该接口只生成/校验航线，不自动启动任务。

### 科研 surface 与图件导出

- `scripts/lib/lab_sim/surface.py` 在版本化水域快照的 ENU 网格上生成 truth/reconstruction/error/voltage/absorbance/risk 六层 surface，使用水域 polygon bbox（不是采样点 bbox），polygon 外严格 mask。
- `scripts/lib/lab_sim/figure_export.py` 的 `export_surface_figure()` 导出 300 DPI PNG/TIFF、SVG/PDF 和 metadata JSON；DPI 限 `72..1200`，尺寸限 `0.1..20` inch，缺 matplotlib 时抛 `FigureExportError`。
- 这些计算只在 ROS/Web 承载；ArduPilot、DetFirmware、QGC 不参与液滴生成、surface 计算或科研绘图。

### 实验仿真验收命令

```bash
cd ~/usv_ws/src/usv_ros
python3 -m py_compile scripts/*.py scripts/lib/lab_sim/*.py
python3 -m unittest discover -s tests -p 'test_*.py'
cd frontend && npm run build
```

`surface`/`figure_export` 及其测试额外需要 `numpy`（surface）与 `matplotlib`（图件导出）。

## 检测装置串口协议

默认泵控串口为 `/dev/ttyUSB0`，`115200 8N1`，文本命令以 `\r\n` 结尾。

连接时 `pump_control_node.py` 会：

1. 打开串口并释放 `DTR=False`、`RTS=False`，避免主动复位 ESP32。
2. 发送 `HELLO?\r\n` 和 `DET?\r\n`。
3. 必须收到 `DET_ID:USV_DETECTOR*`，否则关闭串口并报错。

Web 的 `POST /api/hardware/test-pump-port` 使用同一握手逻辑，不能只用“串口可打开”判断连接成功。

常用下行命令：

| 命令 | 示例 | 说明 |
|---|---|---|
| PID 闭环 | `XEFR90.0P0.1` | X 轴正转 90 度，精度 0.1 |
| 开环角度 | `XEFV5J90.000` | X 轴正转，5 RPM，90 度 |
| 连续转动 | `XEFV5JG` | X 轴连续正转 |
| 停止 | `XDFV0J0` | X 轴停止 |
| PID 参数 | `PIDCFG:0.14,0.015,0.06,1.0,8.0` | 配置 PID |
| 进样泵 | `PUMP:ON`、`PUMP:OFF`、`PUMP:SET:60` | 开关和速度 |
| I2C 映射 | `I2CMAP:X=0,Y=3,Z=4,A=7,SPEC=2` | TCA 通道映射 |
| ADS 配置 | `ADSCFG:CH=2,ADDR=0x40,AIN=AIN0,REF=AVDD,GAIN=1,DR=90,MODE=CONT,PR=20` | 分光 ADC |
| ADS 启停 | `ADSSTART`、`ADSSTOP` | 分光采集 |
| 校准 | `CALXYZA` | 四轴校准 |

上行二进制包：

| 包头 | 内容 |
|---|---|
| `0x55 0xCC` | X/Y/Z/A 角度 float |
| `0x55 0xAA` | PID 数据 |
| `0x55 0xBB` | PID 测试结果 |
| `0x55 0xDD` | 分光数据：时间戳、通道、状态、raw code、电压 |
| `0x55 0xEE` | ESP32 健康数据：温度、heap、任务数、任务栈水位 |

## 数据与日志

运行时目录都在工作空间根目录，不在本仓库目录内：

| 路径 | 内容 |
|---|---|
| `~/usv_ws/.usv_run/` | PID 与运行日志根目录 |
| `~/usv_ws/.usv_run/logs/roscore.log` | ROS Master 日志 |
| `~/usv_ws/.usv_run/logs/mavlink_router.log` | router 日志 |
| `~/usv_ws/.usv_run/logs/usv_system.log` | roslaunch 主日志 |
| `~/usv_ws/.usv_run/logs/boot_check.log` | 开机自启自检日志 |
| `~/usv_ws/config/sampling_config.json` | Web 采样配置 |
| `~/usv_ws/config/calibration.json` | 校准 offset |
| `~/usv_ws/data/missions/mission_*.json` | 任务采样数据和 `sample_windows[]` 摘要 |
| `~/usv_ws/data/missions/raw/<mission_id>/<sample_id>.jsonl` | 采样窗口原始分光帧，一行一帧 |

原始分光帧独立于地图/前端摘要写入，配置和运行时上行频率均强制不低于 20 Hz；前端图表可按需降采样，但应从数据中心的“高频原始分光 CSV”下载完整帧。

`web_config_server` 默认使用上述任务目录，也可通过 roslaunch 参数
`data_dir:=/absolute/path/to/missions` 显式覆盖；直接运行 Web 节点或独立模式时也可使用
环境变量 `USV_MISSION_DATA_DIR`。
任务运行期间持续增量写盘；正常结束标记为 `completed`，Web 服务重启后发现未结束任务会标记为
`interrupted`，避免把异常中断误认为完整任务。监控页“导出缓存 CSV”只包含浏览器已接收的数据，
正式处理应从“数据中心”下载 Jetson 本地任务包。

查看状态：

```bash
./src/usv_ros/scripts/status_usv_all.sh
tail -f ~/usv_ws/.usv_run/logs/usv_system.log
curl http://127.0.0.1:5000/api/logs/files
curl "http://127.0.0.1:5000/api/logs/usv_system.log?lines=100"
```

导出任务数据：

```bash
curl http://127.0.0.1:5000/api/data/missions
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/archive > mission.zip
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/csv > mission.csv
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/samples
curl http://127.0.0.1:5000/api/data/mission/<mission_id>/sample/<sample_id>/raw
curl "http://127.0.0.1:5000/api/data/mission/<mission_id>/geojson?metric=concentration&download=true" > mission.geojson
curl "http://127.0.0.1:5000/api/data/mission/<mission_id>/surface?metric=concentration&size=80&power=2&download=true" > mission_surface.json
curl http://127.0.0.1:5000/api/map/live > live_map_snapshot.json
```

污染物地图现场证据包至少保存：

| 字段 | 说明 |
|---|---|
| `mission_id` | Web 任务 ID，对应 `~/usv_ws/data/missions/mission_*.json` |
| `pollutant_name` / `unit` | 单污染物线性模型名称与单位 |
| `calibration_id` / `work_curve_id` | Web 当前校准或工作曲线标识 |
| `sample_total` / `csv_rows` | 原始任务点数与 CSV 行数 |
| `valid_gps_points` / `excluded_points` | 进入 GeoJSON/IDW 的有效 GPS 点数与被质量门控剔除点数 |
| `surface_grid` | IDW 网格大小，如 `80x80`，以及 `power` 参数 |
| `screenshot_path` | Web 地图 16:9 桌面截图路径 |
| `USV_SMPL/USV_DONE` | bridge 或 roslaunch 日志中 mission script 采样闭环片段 |

## 验证

### 代码逻辑验证

在仓库目录执行：

```bash
cd ~/usv_ws/src/usv_ros
python3 -m py_compile \
  scripts/pump_control_node.py \
  scripts/system_health_node.py \
  scripts/web_config_server.py \
  scripts/mavlink_trigger_node.py \
  scripts/usv_mavlink_router_bridge.py \
  scripts/lib/automation_engine.py \
  scripts/lib/command_generator.py
python3 -m py_compile scripts/lib/lab_sim/*.py
python3 -m unittest discover -s tests -p "test_*.py"
```

实验仿真 schema v2 验收阈值与对应单元测试（坐标 round-trip `<=0.5 m`、图面 `<=2 CSS px`、100 事件 => 100 点 + 100 次写入）见工作空间 `docs/current/70_verification.md`。

前端验证：

```bash
cd ~/usv_ws/src/usv_ros/frontend
npm run lint
npm run build
```

污染物地图离线验证：

```bash
cd ~/usv_ws/src/usv_ros
python3 scripts/verify_pollution_workflow.py --mock --evidence .omo/evidence/pollution-workflow-verify.json
```

该脚本只创建 mock mission 并调用 Web API，不启动 `mavlink-routerd`，不访问 Pixhawk 或检测装置串口。

### 运行时基线检查

完整启动后：

```bash
cd ~/usv_ws
./src/usv_ros/scripts/status_usv_all.sh
rosnode list
rostopic echo -n 1 /mavros/state
rostopic echo -n 1 /usv/bridge_diagnostics
rostopic echo -n 1 /usv/system_health
curl http://127.0.0.1:5000/api/ui/debug
curl http://127.0.0.1:5000/api/diagnostics/system
```

通过判据：

- `status_usv_all.sh` 显示 `roscore`、`mavlink_router`、`usv_system` 运行。
- `ros_nodes` 至少包含 `/pump_control_node`、`/web_config_server`、`/mavlink_trigger_node`、
  `/system_health_node`、`/usv_mavlink_bridge`、`/mavros`。
- `/mavros/state` 中 `connected: True`。
- `/usv/bridge_diagnostics` 的 `tx_named_value`、`pkt_count` 持续增长。
- `/usv/system_health` 包含 `jetson`、`detector`、`ros_nodes` 和 `health`。
- Web 控制台可访问，`/api/ui/debug` 返回 `dist_index_exists=true` 或明确回退到静态页面。

更多现场测试流程见 [TESTING.md](TESTING.md)。

## 故障排查

| 现象 | 优先检查 |
|---|---|
| Web 页面空白 | `GET /api/ui/debug`，确认 `dist_index_exists`、`ui_mode` 和 `/assets/*` |
| 5000 端口不通 | `status_usv_all.sh`、`ss -ltn | grep 5000`、`usv_system.log` |
| 泵控节点启动失败 | 串口路径、权限、ESP32 供电、`DET_ID:USV_DETECTOR` 握手 |
| Web 串口测试失败 | `POST /api/hardware/test-pump-port` 返回的 `identity` / 错误信息 |
| 分光无数据 | `/usv/spectrometer_status`、`ADSSTART`、I2C 映射、检测装置固件状态 |
| 系统健康无数据 | `/usv/system_health`、`/usv/detector_health`、`/api/diagnostics/system` |
| MAVROS 断开 | 飞控串口线序、`SERIAL2_PROTOCOL=2`、`SERIAL2_BAUD=921`、`mavlink_router.log` |
| QGC 指令不到 ROS | `/usv/mavlink_cmd_rx`、router TCP 端点、QGC target sys/comp |
| QGC 面板无遥测 | `/usv/bridge_diagnostics` 的 `tx_named_value`、定制 ArduPilot/QGC 是否匹配 |
| stop 后担心误杀 | 当前脚本会校验 PID 命令行，只停止 `roscore`、`roslaunch`、`mavlink-router`、USV 相关 Python |

## 开发约束

- MAVLink 改动必须先看飞控源码，尤其是 `Rover/GCS_MAVLink_Rover.cpp`、`Rover/sensors.cpp` 和
  `libraries/GCS_MAVLink/`。不要只凭 README 或接口文档改命令/字段。
- `src/usv_ros/` 是独立 Git 仓库；提交时进入本目录执行 Git 操作，不要在总管理仓库根目录提交子仓库改动。
- Commit message 使用 `Feat:`、`Fix:`、`Refactor:`、`Docs:` 前缀。
- Windows 上通常只做代码编辑和逻辑验证；Jetson、Pixhawk、ESP32 的真实运行验证需要现场硬件。
- 更新 Web API、ROS topic/service 或 MAVLink 字段后，同步更新本 README、[TESTING.md](TESTING.md) 和工作空间
  `docs/current/40_interfaces.md`。

## License

`package.xml` 声明本仓库使用 MIT License。当前仓库未单独提交 `LICENSE` 文件；发布或对外分发前建议补齐。
