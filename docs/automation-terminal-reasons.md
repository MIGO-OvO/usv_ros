# 自动化终止原因契约（Automation Terminal Reasons）

Updated: 2026-09-29

现场不再需要 SSH 判断任务为何停止。`pump_control_node` 对每次自动化终止记录唯一的
结构化 `terminal_reason`，通过 `/usv/automation_status` 与 Web `status` 事件下发，
并在停止时输出一行结构化日志。

## 枚举与触发条件

| terminal_reason | 触发条件 | 安全语义 |
|---|---|---|
| `completed` | 引擎自行跑完全部步骤（`finished`） | 正常结束 |
| `operator_stop` | 操作员停止，且此前无故障原因 | 正常结束 |
| `operator_pause` | 操作员暂停（保留给暂停终态使用） | 正常结束 |
| `pid_timeout` | 固件 `PID_TIMEOUT:`，或引擎 PID 等待 60s 超时 | 任务失败 |
| `pid_fail` | 固件 `PID_FAIL:` | 任务失败 |
| `spectrometer_start_failed` | 分光启动事务在 I2CMAP/ADSCFG/START 阶段失败 | 任务失败 |
| `spectrometer_frame_timeout` | START ACK 后 `measurement_timeout` 内没有有效帧 | 任务失败 |
| `spectrometer_stale` | 采集中有效帧中断超过 `measurement_timeout` | 任务失败 |
| `watchdog_tripped` | 固件 `WATCHDOG_TRIPPED` / `WATCHDOG_ERR:` | 安全停止 |
| `owner_lost` | `/usv/sampling_owner` 心跳超过 5s 未续约 | 安全停止 |
| `serial_disconnected` | 串口读线程异常或 keepalive 写失败 | 安全停止 |
| `controller_fault` | 指令发送失败等控制器级故障 | 任务失败 |
| `configuration_failed` | 无步骤或引擎无法启动 | 启动失败 |
| `cleanup_failed` | 停止清理中泵停止失败 | 高风险，需人工确认 |
| `unknown_error` | 未分类的引擎错误 | 任务失败 |

根因优先：`_set_terminal_reason` 只记录首次原因，后续停止调用不会覆盖真正的故障原因。

## 状态字段

`/usv/automation_status`（JSON）关键字段：

```json
{
  "status": "stopped",
  "terminal_reason": "watchdog_tripped",
  "last_error": "...",
  "controller_fault": "watchdog_tripped",
  "automation_step": 2,
  "automation_total": 4,
  "current_loop": 1,
  "total_loops": 5,
  "pending_motors": ["X"],
  "spectrometer_state": "stale",
  "spectrometer_txn_phase": "wait_frame",
  "spectrometer_last_txn_error": "no valid spectrometer frame after start",
  "spectrometer_age_s": 3.4,
  "owner_age_s": 0.4,
  "serial_connected": true,
  "sampling_context": {"attempt_id": "..."}
}
```

## 结构化日志

```
[AUTOMATION TERMINAL] reason=owner_lost attempt_id=... source=web step=2/4 loop=1/5 \
pending_motors=['X'] spectro_state=acquiring spectro_txn_phase=running \
spectro_age=3.40s owner_age=5.31s controller_fault=owner_lost serial_connected=True
```

## PID 语义契约

固件是 PID 完成状态的唯一权威来源，三种消息语义严格区分：

```
PID_DONE:X    -> 该电机完成（从 pending 移除，流程继续）
PID_TIMEOUT:X -> 自动化失败，terminal_reason=pid_timeout，立即终止
PID_FAIL:X    -> 自动化失败，terminal_reason=pid_fail，立即终止
```

`PID_TIMEOUT` / `PID_FAIL` 不允许映射为“电机完成”。固件超时也不再静默继续流程。

## 分光新鲜度契约

- `ADS_OK:START` 只证明设备接受启动命令，**绝不**写入 `_last_valid_spectro_at`。
- 启动阶段（`spectro_state == 'starting'`）的过期判定使用 `_spectro_start_ack_at`，
  超时映射为 `spectrometer_frame_timeout`。
- 运行阶段的有效性只由真实有效帧（`_emit_spectro_average`）推进，
  中断超时映射为 `spectrometer_stale`。

## Watchdog 契约

```
connect -> handshake -> WATCHDOG:ARM -> 立即启动独立 keepalive 线程
        -> I2CMAP / ADSCFG / ADSSTART / 首帧事务
```

keepalive 线程每 0.5s 发送 `WATCHDOG:KEEPALIVE`，不依赖 ROS 主循环、不持有
`_control_lock`，因此配置事务等待 ACK 时不会饿死硬件看门狗租约。旧连接的
keepalive 线程在重连后自行退出，不会写入新会话。

## Web 自动化 GPS 策略

- 持久化位置：`sampling_config.json` 的 `automation_policy.require_gps`（默认 `true`）。
- `GET /api/config` 返回该字段；`POST /api/config` 与任务配置 import/export 保留该字段。
- Automation 页面 mount 时用服务器值初始化开关，切换即持久化；页面切换、刷新、
  Web 服务重启后保持。
- `POST /api/mission/start` 的 `require_gps` 字段仍为本次任务的显式覆盖值，
  未提供时回退到持久化默认值。
- 该策略只影响 Web 自动化；FCU NAV_SCRIPT_TIME、航点采样、survey 采样与实船
  SampleRecord 的严格 GPS 门控不变。
