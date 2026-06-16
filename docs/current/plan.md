# 实施计划: 离线地图分发 + 实验功能改造
Updated: 2026-05-31T06:30:00+08:00

源自一次设计访谈定稿。分两阶段交付: 先地图缓存(低风险/内聚), 后实验功能(跨前后端+多节点)。

## 阶段一: 离线地图分发 (优先) — 已完成

目标: 不再单纯依赖 ROS 设备联网下载瓦片; 支持外部联网设备导出瓦片包, 离线传入 ROS 合并。

状态: 已实现并自测通过 (导出->导入闭环, 校验失败拒绝, 离线模式无回源, 路径穿越防护)。

### 决策摘要
- 分发载体: 瓦片包(tar) + manifest.json, 非裸 Git。传输通道任选(Git LFS/U盘/scp)。
- 包格式: `tiles.tar` + `manifest.json`(bbox, zoom_min/max, tile_count, sha256 校验, provider, styles, created_at)。
- 工具形态: 导出以独立 CLI 为主(外部设备仅需 Python 标准库); ROS 端导入支持 Web 按钮 + CLI 双入口。
- 导出来源: 默认按 bbox 直接联网下载打包; `--from-cache` 可选导出现有缓存目录。
- 导入策略: manifest 校验(数量/sha256/provider-style 匹配/防损坏) -> 临时目录 -> 原子合并进 `~/usv_ws/map_cache`; 单一合并语义, 失败不动现有缓存; 返回摘要(新增/跳过/覆盖 bbox)。
- 运行时降级: 新增手动"离线模式"开关, 开启后 `get_tile(allow_remote=False)` 跳过回源, 消除弱网 8s 超时卡顿; 状态持久化到 map 配置。

### 影响文件 (估算)
- `scripts/map_resources/map_pack_export.py` (新增, ~120 行): 复用 `map_tile_cache.py` 的 `enumerate_tiles`/端点/下载; 默认下载, `--from-cache` 导出现有缓存; 产出 tar+manifest。
- `scripts/map_resources/map_pack_import.py` (新增, ~100 行): 校验 manifest -> 临时解压 -> 原子合并 -> 摘要。
- `scripts/map_resources/map_tile_cache.py` (改, ~+30 行): 抽出可复用的下载/枚举供 CLI 调用; `get_tile` 接入离线模式; 新增 import_pack/export_pack 辅助(供 Web 复用)。
- `scripts/web_config_server.py` (改, ~+60 行): `/api/map/cache/import`(上传包) + `/api/map/offline-mode`(读写开关); map 配置新增 `offline_mode` 字段。
- `frontend/src/pages/Map.tsx` (改, ~+50 行): "导入离线包"上传按钮 + 摘要展示; "离线模式"开关。

### 验收门槛
- CLI 导出包可被 import 校验通过并合并; sha256/provider 不匹配时拒绝且不动缓存。
- 离线模式开启后无任何回源请求(网络断开地图不卡顿)。
- 前端导入按钮返回新增/跳过数量。

## 阶段二: 实验功能改造 — 已完成

目标: 把实验功能从"孤立的手动差速积分器"改造为"完整虚拟任务仿真器", 可选真实采样设备; 解决"用户不知道在实验什么"。

状态: 已实现并自测通过 (web 规范化 -> lab_sim 制导 -> 到达事件 -> trigger 模拟电压链路打通; 污染模型近源高吸光度; 前端 build/eslint 通过)。

### 决策摘要
- 定位: A+B 融合 — 完整虚拟任务仿真 + 可选真实采样设备。真实螺旋桨驱动本期放弃(移除前端 disabled 占位)。
- 航点来源(C): Web 内置地图航点编辑器(画点/连线/设采样参数) + 可导入 QGC 已上传航点(复用 `route_waypoints`)。
- 自动巡航(C+C2): `lab_sim_node` 内置差速制导跑航线; 到点发布 `/usv/lab_sim/waypoint_reached`; `mavlink_trigger_node` 在 lab_mode 下订阅该话题, 复用现有"到点->HOLD->稳定->采样->恢复"状态机。
- 数据源(C): 实验页"数据源"开关 — 模拟生成 / 真实设备。
- 模拟数据生成(B): 由 `mavlink_trigger_node` 在 lab_mode 到点时伪造电压, 走 `/usv/spectrometer_voltage` 真实通路, 与真实数据格式一致。
- 模拟污染源: 两种模式用户自选 — 默认航线包络中心单源(距离衰减+噪声), 可选地图手动放置(可调强度/半径)。
- 引导形态(B): 单页 + 顶部"当前阶段"状态横幅(未配置航点/航行中/采样中/已完成) + 空状态引导文案。
- GPS 隔离: lab_mode 启用时 `position_source=lab_sim` 优先, 忽略真实 GPS 回调写入 `current_position`。
- 配置隔离: 实验虚拟航点采样参数独立存储于 `lab_mode.mission`, 不污染真实 `waypoint_sampling`。

### 影响文件 (估算)
- `scripts/lab_sim_node.py` (改, ~+90 行): 新增 `mission` 指令(航点列表); 差速制导(朝下一点转向+前进); 到点判定与 `/usv/lab_sim/waypoint_reached` 发布。
- `scripts/mavlink_trigger_node.py` (改, ~+70 行): lab_mode 下订阅虚拟到达话题驱动采样状态机; 模拟数据源时伪造电压注入 `/usv/spectrometer_voltage`; 从 `lab_mode` 配置读污染源算模拟浓度。
- `scripts/web_config_server.py` (改, ~+80 行): `/api/lab/*` 扩展(航点下发/导入QGC/数据源/污染源); `lab_mode.mission` 存储; lab_mode 下 `_gps_cb` 忽略写入。
- `frontend/src/pages/Lab.tsx` (改, 大改): 地图航点编辑器 + 导入QGC + 数据源开关 + 污染源设置(双模式) + 状态横幅; 移除真实推进 disabled 占位。
- `config/usv_params.yaml` (改, ~+10 行): `lab_mode.mission` 与污染源默认值。

### 验收门槛
- Web 画航点/导入 QGC 后, 虚拟船自动沿航线巡航并到点触发采样状态机。
- 模拟数据源: 无硬件时地图出采样点+热力图; 真实数据源: 到点驱动真泵真光谱仪。
- lab_mode 下地图船位仅来自 lab_sim, 无 GPS 跳变。
- 实验航点配置不影响真实 `waypoint_sampling`。

## 回退
- 阶段一: 新增 CLI 为独立文件, 可直接删除; web/前端改动单提交回退。
- 阶段二: lab_sim/trigger 改动以 `lab_mode.enabled` 为总闸, 关闭即回退到现有真实任务链路。
