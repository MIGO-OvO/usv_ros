# 后续开发规划
Updated: 2026-04-11T14:00:00Z

> 基于 v0.2.0-stable（三端通信链路稳定版本）的后续工作规划。
> 本文档包含概要规划和细化实施方案。

## 基线版本
- `ardupilot-usv` : `v0.2.0-stable` (56741bb0fa)
- `src/usv_ros`   : `v0.2.0-stable` (63ae83ec)
- `QGC`           : `v0.2.0-stable` (af6c56478)

---

## 本批次任务（v0.3.0 目标）

| 编号 | 名称 | 端 | 优先级 |
|---|---|---|---|
| #1 | stop 脚本安全加固 | ROS | P0 |
| #2 | MAVROS 参数下载检测 | ROS | P0 |
| #4 | 航点自动采样闭环 | ROS | P1 |
| #5 | 采样数据存储与导出 | ROS + Web | P1 |
| #6 | QGC 采样数据图表页 | QGC | P1 |
| #8 | 数传电台链路监控 | ROS + Web | P2 |
| #10 | Web 日志远程查看 | ROS + Web | P2 |

---

## #1 stop 脚本安全加固

### 现状
`common_env.sh` 的 `stop_pid_file()` 仅用 `kill -0` 检查 PID 是否存在就直接 kill，不校验该 PID 是否仍属于目标进程。`cleanup_port_process()` 按端口杀进程不区分进程类型。异常退出后 PID 残留 + PID 复用 = 误杀 VSCode/SSH。

### 改动文件
- `src/usv_ros/scripts/common_env.sh`

### 实施细节
1. 新增 `is_pid_owned_by()` 函数：读取 `/proc/$pid/cmdline`，校验是否包含预期关键字（roscore/roslaunch/mavlink/python）
2. `stop_pid_file()` 在 kill 前调用 `is_pid_owned_by "$pid" "$process_name"`，不匹配时跳过并清理 PID 文件
3. `cleanup_port_process()` 增加进程名过滤：只杀 `python|roslaunch|roscore|mavlink` 相关进程

### 验证
```bash
# 模拟场景：创建假 PID 文件指向 sshd
echo $PPID > ~/usv_ws/.usv_run/roscore.pid
./src/usv_ros/scripts/stop_usv_all.sh
# 预期：日志输出"跳过 kill"，SSH 不断
```

---

## #2 MAVROS 参数下载检测

### 现状
MAVROS 启动后参数下载需 1-3 分钟。此期间 `status_usv_all.sh` 显示 `CONNECTED` 但系统实际未就绪，用户误以为可以操作。

### 改动文件
- `src/usv_ros/scripts/status_usv_all.sh`

### 实施细节
在 `print_ros_nodes()` 的 MAVROS 连通检查之后，增加参数就绪检测：

1. 优先尝试 `rosparam get /mavros/param/count` 和 `/mavros/param/received` 比较
2. 备用方案：检查 `usv_system.log` 最后 5 行是否有 `params still missing`
3. 输出格式：
   - `mavros_params: OK (548/548)`
   - `mavros_params: DOWNLOADING (123/548)`
   - `mavros_params: DOWNLOADING (日志仍有参数超时)`

### 验证
```bash
# 启动后立刻执行
./src/usv_ros/scripts/status_usv_all.sh
# 预期：显示 DOWNLOADING
# 等 2-3 分钟后再执行
# 预期：显示 OK
```

---

## #4 航点自动采样闭环

### 现状
`mavlink_trigger_node.py` 的 `_handle_completion()` 采样完成后无条件调用 `set_mode("AUTO")`，飞控无任务时拒绝（日志 `No Mission. Can't set AUTO.`）。

### 改动文件
- `src/usv_ros/scripts/mavlink_trigger_node.py`

### 实施细节
修改 `_handle_completion()` 方法：

```python
def _handle_completion(self):
    self.is_sampling = False
    self._publish_status("sampling_stopped")
    rospy.sleep(1.0)
    # 检查是否有航点任务再决定切模式
    try:
        from mavros_msgs.srv import WaypointPull
        rospy.wait_for_service('/mavros/mission/pull', timeout=3.0)
        resp = rospy.ServiceProxy('/mavros/mission/pull', WaypointPull)()
        if resp.success and resp.wp_received > 0:
            self.set_mode("AUTO")
        else:
            rospy.loginfo("No mission, staying HOLD")
            self._publish_status("hold_no_mission")
    except Exception:
        self._publish_status("hold_no_mission")
```

同样修改 `_stop_sampling_sequence()` 中的 `set_mode("AUTO")` 为相同逻辑。

### 验证
- 有航点任务时采样完成 → 自动切 AUTO 继续执行
- 无航点任务时采样完成 → 保持 HOLD，日志输出 `hold_no_mission`
- 不再出现 `No Mission. Can't set AUTO.` 警告

---

## #5 采样数据存储与导出

### 现状
- `pump_control_node.py` 发布 `/usv/spectrometer_voltage` JSON（含 voltage, absorbance, timestamp_ms）
- `web_config_server.py` 有内存中的 `voltage_history` 列表和 `/api/data/voltage` API
- 前端 `store.ts` 维护 `voltageHistory: VoltagePoint[]`（最多 150 点）
- 前端有 `Data.tsx` 页面但功能有限
- **无持久化存储**：重启后数据丢失

### 改动文件
- `src/usv_ros/scripts/web_config_server.py` — 新增 CSV 存储 + 导出 API
- `src/usv_ros/frontend/src/pages/Data.tsx` — 历史数据页面重构
- `src/usv_ros/frontend/src/store.ts` — 增加历史数据 fetch 方法

### 实施细节

#### 后端（web_config_server.py）
1. 采样数据自动写入 CSV 文件
   - 存储目录：`~/usv_ws/data/sampling/`
   - 文件命名：`sampling_YYYYMMDD_HHMMSS.csv`
   - 列：`timestamp, voltage, absorbance, pump_x, pump_y, pump_z, pump_a, status`
   - 在 `_voltage_cb` 中当 `automation_running=True` 时追加写入

2. 新增 API：
   - `GET /api/data/sampling/list` — 列出所有 CSV 文件（名称、大小、行数）
   - `GET /api/data/sampling/<filename>` — 返回指定文件的 JSON 数据
   - `GET /api/data/sampling/<filename>/download` — 下载原始 CSV
   - `DELETE /api/data/sampling/<filename>` — 删除指定文件

#### 前端（Data.tsx）
1. 文件列表卡片：显示所有采样记录文件，支持点击查看、下载、删除
2. 数据查看器：表格 + 曲线图（voltage 和 absorbance 双 Y 轴）
3. 使用 recharts 库（已在项目前端依赖中）绘制曲线

### 验证
```bash
# 执行一次采样后
ls ~/usv_ws/data/sampling/
curl http://127.0.0.1:5000/api/data/sampling/list
curl http://127.0.0.1:5000/api/data/sampling/<filename>/download > test.csv
```

---

## #6 QGC 采样数据图表页

### 现状
QGC 有三个 USV 自定义 QML 面板：
- `USVPayloadPanel.qml` — 飞行视图叠加层（状态 + 按钮）
- `USVPayloadDetailPanel.qml` — 详情面板（泵角度 + 诊断）
- `USVPayloadSummaryStrip.qml` — 摘要条

均为数值显示，无历史曲线图。

### 改动文件
- `WQ-USV-QGroundControl/custom/res/USVSamplingChartPanel.qml` — 新建
- `WQ-USV-QGroundControl/custom/custom.qrc` — 注册新 QML
- `WQ-USV-QGroundControl/custom/src/USVPlugin.cc` — 注册到飞行视图

### 实施细节

#### 新建 USVSamplingChartPanel.qml
1. 独立的全高度面板（类似 QGC 的分析视图风格）
2. 数据来源：`USVPayloadFactGroup` 的 `voltage` 和 `absorbance` Fact
3. 使用 QML `ChartView` + `LineSeries` 组件：
   - X 轴：时间（滚动窗口，最近 60 秒）
   - Y 轴左：电压 (V)
   - Y 轴右：吸光度 (AU)
4. 底部信息栏：当前值、最大/最小值、平均值
5. 工具栏：暂停/恢复滚动、清空、导出截图

#### 数据缓存
在 `USVPayloadFactGroup` 或 QML 层维护滚动数据队列：
```qml
property var voltageHistory: []    // [{time, value}, ...]
property var absorbanceHistory: []
property int maxPoints: 120        // 60s × 2Hz

Timer {
    interval: 500
    repeat: true
    running: _linkOk
    onTriggered: {
        var now = new Date()
        voltageHistory.push({time: now, value: _voltageFact.value})
        if (voltageHistory.length > maxPoints) voltageHistory.shift()
        // absorbance 同理
    }
}
```

#### 入口注册
在 `USVPlugin.cc` 中通过 `QmlComponentInfo` 注册新面板，或在 `USVFlyViewCustomLayer.qml` 中通过按钮切换显示。

### 验证
- QGC 连接飞控后，打开图表面板能看到电压/吸光度实时曲线
- 曲线平滑滚动，数据与面板数值一致
- 断连后曲线停止，重连后恢复

---

## #8 数传电台链路监控

### 现状
ArduPilot 自动通过 `RADIO_STATUS` (msgid=109) 上报电台状态。QGC 内建 `VehicleFactGroup` 有 `RADIO_STATUS` 处理但未暴露到 USV 面板。Web 端无此信息。

### 改动文件
- `src/usv_ros/scripts/usv_mavlink_router_bridge.py` — 接收 RADIO_STATUS 并发 ROS topic
- `src/usv_ros/scripts/web_config_server.py` — 订阅并推送到前端
- `src/usv_ros/frontend/src/components/link-diagnostics-card.tsx` — 显示电台指标
- `src/usv_ros/frontend/src/store.ts` — 增加 RadioStatus 接口

### 实施细节

#### ROS 端（bridge）
在 `_receive_mavlink_messages()` 中增加 RADIO_STATUS 解析：
```python
if msg_type == "RADIO_STATUS":
    radio_msg = String()
    radio_msg.data = json.dumps({
        "rssi": msg.rssi,
        "remrssi": msg.remrssi,
        "noise": msg.noise,
        "remnoise": msg.remnoise,
        "rxerrors": msg.rxerrors,
        "fixed": msg.fixed,
        "txbuf": msg.txbuf
    })
    self._radio_status_pub.publish(radio_msg)
```

新增 Publisher：`/usv/radio_status` (std_msgs/String)

#### Web 端
1. `web_config_server.py` 订阅 `/usv/radio_status`，推送 Socket.IO 事件 `radio_status`
2. `store.ts` 增加 `radioStatus` 状态
3. `link-diagnostics-card.tsx` 增加电台信号强度显示：
   - RSSI 进度条（0-254, 绿/黄/红）
   - 远端 RSSI
   - 噪声底
   - TX 缓冲区使用率

### 验证
```bash
rostopic echo /usv/radio_status
# 预期：每秒输出 JSON 含 rssi, remrssi, noise 等字段
```
Web 端链路诊断卡片显示信号强度指示器。

---

## #10 Web 日志远程查看

### 现状
ROS 日志在 Jetson 本地 `~/usv_ws/.usv_run/logs/` 目录，需 SSH 登录才能查看。Web 前端有 `logs: LogEntry[]` 数据结构和 Socket.IO `log` 事件，但仅推送节点级别日志，不包含系统日志文件内容。

### 改动文件
- `src/usv_ros/scripts/web_config_server.py` — 新增日志 API
- `src/usv_ros/frontend/src/pages/Settings.tsx` 或新增 `SystemLog.tsx` — 日志查看 UI
- `src/usv_ros/frontend/src/store.ts` — 增加系统日志 fetch

### 实施细节

#### 后端 API
```python
# 获取日志文件列表
GET /api/logs/files
# 返回: [{"name": "usv_system.log", "size": 12345, "modified": "..."}, ...]

# 获取日志最后 N 行
GET /api/logs/<filename>?lines=100
# 返回: {"success": true, "data": ["line1", "line2", ...]}

# 实时日志流（通过现有 Socket.IO）
# 新增事件 'system_log_line'
```

实现方式：
1. `/api/logs/files`：扫描 `LOG_DIR`（`~/usv_ws/.usv_run/logs/`）
2. `/api/logs/<filename>`：使用 `collections.deque` 读取文件最后 N 行（高效 tail）
3. Socket.IO 实时推送：后台线程 `tail -f` 日志文件，逐行推送

#### 安全约束
- 只允许读取 `LOG_DIR` 下的 `.log` 文件
- 文件名白名单：`usv_system.log`、`roscore.log`、`mavlink_router.log`
- 不允许 `../` 路径穿越

#### 前端 UI
在 Settings 页面新增"系统日志"标签页：
- 顶部：日志文件选择下拉框 + 自动滚动开关
- 主体：等宽字体日志行显示（含 WARN/ERROR 高亮）
- 底部：手动刷新按钮 + 行数设置

### 验证
```bash
curl http://127.0.0.1:5000/api/logs/files
curl "http://127.0.0.1:5000/api/logs/usv_system.log?lines=20"
```
Web 端能实时看到系统日志更新。

---

## 未纳入本批次的任务

| 编号 | 名称 | 原因 |
|---|---|---|
| #3 | 开机自启动 systemd | 需现场测试启动顺序和依赖关系 |
| #7 | 多点采样任务编排 | 依赖 #5 完成后评估 |
| #9 | 固件 OTA 更新 | 复杂度高，优先级低 |
| #11 | 水质数据可视化大屏 | 依赖 #5 数据存储基础 |

## 版本里程碑

| 版本 | 内容 | 目标时间 |
|---|---|---|
| v0.2.0 | ✅ 三端通信稳定 | 已完成 |
| v0.3.0 | #1 #2 #4 #5 #6 #8 #10 | 3-4 周 |
| v0.4.0 | #3 #7 #11 | 5-7 周 |
| v1.0.0 | 全部完成 + 实船验收 | 8-10 周 |
