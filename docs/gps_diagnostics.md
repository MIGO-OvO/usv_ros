# GPS / GNSS 现场诊断

连接 Jetson 热点后打开 Web **系统监控**。GPS / GNSS 面板位于标题下方；自动化页不再显示旧 GPS 卡。

## 数据与控制边界

- `mavlink-routerd` 继续独占 TELEM2 `/dev/ttyTHS1`；MAVROS 使用 UDP 14550，自定义 bridge 使用现有 TCP 5760。14551 路由端点不变。
- `usv_mavlink_router_bridge.py` 只接受目标 FCU（默认 1/1）的 HEARTBEAT、GPS_RAW_INT、GLOBAL_POSITION_INT、SYS_STATUS、STATUSTEXT 和 AUTOPILOT_VERSION。机载电脑/GCS 心跳不计为飞控心跳。
- bridge 每 0.5 秒发布 `/usv/gps_diagnostics`（`std_msgs/String` JSON）。ROS 回调不访问 MAVLink socket。控制命令、ACK、USV_SMPL / USV_DONE / USV_FAIL 语义不变。
- 每 5 秒至多一个只读请求，轮询 GPS1_TYPE、GPS_AUTO_CONFIG、SERIAL3_PROTOCOL，以及缺失的 AUTOPILOT_VERSION。参数约 20 秒刷新，60 秒后不用于推断启用状态。不发送 PARAM_SET，不改变消息速率。
- Web 不打开飞控串口，不再实现 MAVLink parser。诊断不可用时不会阻塞 bridge 控制循环。
- `/api/gps` 保持原有严格 NavSatFix 接口；`/api/gps/diagnostics` 为新增只读接口，禁止 HTTP 缓存。

## 状态优先级

| 条件（按顺序） | 状态 | 颜色 |
|---|---|---|
| 目标 FCU heartbeat 缺失或超过 3 秒 | 飞控通信中断 | 红 |
| 新鲜 GPS1_TYPE == 0 | GPS未启用 | 灰 |
| 从未收到 GPS_RAW_INT | GPS原始数据缺失 / 未收到 GPS_RAW_INT | 橙 |
| 原始帧超过 freshness threshold（默认 2 秒） | GPS数据已过期 | 橙 |
| fix_type == 0 | 已启用但未识别接收机 | 橙 |
| fix_type == 1 / 2 | 已识别尚未定位 / 2D Fix | 黄 |
| 坐标不合法 | GPS坐标无效 | 红 |
| fix_type >= 3 且原始坐标合法 | GPS 3D FIX（另列 DGPS/RTK Float/RTK Fixed） | 绿 |

MAVROS connected 独立显示；状态消息超过 3 秒显示未知/已过期，不以 MAVROS 代替 FCU 心跳。绿色仅代表原始 GNSS 条件满足，并不授予采样许可。

## API 示例（缺原始帧，节选）

```json
{
  "schema_version": 1,
  "overall": {"state": "raw_missing", "severity": "orange", "message": "GPS原始数据缺失 · 未收到 GPS_RAW_INT"},
  "fcu": {"heartbeat_valid": true, "system_id": 1, "component_id": 1, "heartbeat_age_s": 0.2, "mavros_connected": false},
  "gps_config": {"gps1_type": 1, "auto_config": 1, "serial_protocol": 5, "fresh": true},
  "gps_raw": {"available": false, "age_s": null, "fix_type": null, "satellites": null, "latitude": null, "longitude": null, "altitude": null},
  "global_position": {"available": true, "latitude": 30.0, "longitude": 120.0, "altitude": -900.0, "age_s": 0.1},
  "navsat": {"available": true, "status": -1, "valid_navsat_fix": false},
  "sampling_position": {"valid": false, "reason": "gps_no_fix", "policy": "strict_navsat_freeze_position"},
  "map_position_valid": false
}
```

`global_position` 是直接 GLOBAL_POSITION_INT，`navsat` 是 MAVROS NavSatFix，避免混淆不同源和高度基准。缺失测量为 null；卫星 255、DOP 65535、精度 0 等未知哨兵不伪装成测量值。age 使用同一 Jetson 启动周期的单调时钟，Unix 接收时间仅用于显示。原始帧 age 是消息接收年龄，不是独立 GNSS 测量时间证明。

`sampling_position` 原样执行 `freeze_position()`，保留 `gps_missing`、`gps_no_fix`、`gps_stale`、`gps_invalid_coordinates`、`gps_missing_timestamp` 语义。它不使用诊断坐标替代采样坐标。Web 现有显式台架 `require_gps=false` 豁免保留，默认开启 GPS 要求，不影响 FCU 航点准入；无定位台架记录不进入地图。

地图准入更保守：严格 NavSatFix 校验 + 新鲜有效原始3D Fix + FCU heartbeat。无效消息不更新当前位置、不追加 live track；保留的点明确显示“最后有效 GPS 位置”和 age。Lab 模拟位置继续走独立既有路径。

## 现场验收与限制

1. 无 GPS_RAW_INT 但有全局坐标：橙色缺失，不能显示 GPS 正常。
2. fix_type=1、SAT=0：黄色未定位；3D + 合法坐标：绿色；2D、DGPS、RTK 分别标注。
3. MAVROS 断开而 FCU 心跳持续：FCU 正常 / MAVROS 异常；FCU 心跳停止超过 3 秒：红色。
4. NavSatFix status=-1、陈旧、无时间戳、越界：不移动地图，不追加轨迹，不放宽严格采样校验。
5. GPS_RAW_INT 停止超过默认 2 秒：过期；bridge 停止时 Web 仍按缓存原接收时间判旧。
6. 恢复有效数据后地图继续更新；确认任务控制、USV_DONE 闭环及 Lab 模拟无回归。

固件依据：`ardupilot-usv/libraries/AP_GPS/AP_GPS.cpp` 的 `send_mavlink_gps_raw()`：GPS_TYPE_NONE 不发原始帧，发送状态与 last_fix_time；`libraries/GCS_MAVLink/GCS_Common.cpp` 支持 REQUEST_MESSAGE / AUTOPILOT_VERSION，git hash 为 ASCII 字符。消息缺失也可能由消息流/路由造成，不能据此断言接收机损坏。SYS_STATUS GPS 位是汇总状态，不是 GPS1 driver 身份。最近 12 条 GPS 相关 STATUSTEXT 仅是历史线索，不据此认定当前 driver。

M9 FIX 灯、天线馈电/接头、遮挡、多路径、模块硬件故障仍需现场核对。软件可定位到链路/配置/原始数据/Fix/融合/采样层，但不能远程证明天线正常。固件版本不可用不阻塞页面。

离线复现：`python -m unittest discover -s tests -p test_gps_diagnostics.py`；UI 合成数据：`python tests/gps_diagnostics_fixture_server.py`，仅监听 `127.0.0.1:5087`，不连接硬件。

## 本次验证（Windows，2026-09-28）

- GNSS 专项 17 项通过：A–F、错误来源心跳过滤、参数未知/陈旧、未知哨兵、版本解码、严格采样拒绝原因、地图门控和既有 bridge TCP 接入。
- 回归：524 passed、1 skipped、90 subtests passed；4 deselected 分别为以下两项环境限制和两项既有失败。没有宣称全量零失败。
- Windows Bash/WSL 调用挂起：`test_addr_skips_jetson_usb_and_docker_bridge_addresses`、`test_status_flags_internet_wifi_band_mismatch`。按用户要求跳过，留待 Jetson 验证。
- 既有失败：`test_web_map_config_serves_offline_tile_proxy_without_amap_key` 仍预期 amap，当前主干实现是 google；`test_frontend_declares_runnable_map_smoke_script` 仍断言已不存在的 `MAP_TILE_NATIVE_MAX_ZOOM = 18`。首次回归已实际运行并确认失败，与本次 GPS 修改无关，未改动底图实现或削弱断言。
- `npm run build` 通过；`npm run lint` 无错误、2 条既有 Hook 警告；前端 automation-controls / lab-coordinates / time-series 共 23 项通过。build 保留既有 bundle-size 和 Browserslist 提示。
- Python compileall 通过；390px 手机与 1366px 桌面浏览器验证未见横向溢出，原始帧缺失时即使显示融合坐标仍明确橙色告警，详情可展开。
- 真实 ROS、router 串口链路、MAVROS 断连恢复、M9 卫星捕获/天线及在航采样闭环尚未实机验证。
