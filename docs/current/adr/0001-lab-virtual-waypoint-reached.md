# ADR 0001: 实验模式虚拟航点到达事件经独立话题接入采样状态机
Date: 2026-05-31
Status: Accepted

## 背景
实验功能改造目标是让虚拟船像真实任务一样, 沿航线自动巡航并到点触发采样。
现有"到点->HOLD->稳定->采样->恢复"状态机已在 `mavlink_trigger_node.py` 成熟运行,
其触发源是真实飞控的 `/mavros/mission/reached` (`WaypointReached`)。
`lab_sim_node.py` 是独立差速积分器, 不监听航点, 与该状态机无任何联动。

需要决定: 虚拟船到点后, 如何把"到达事件"接入已有采样状态机, 以复用而非重写整套逻辑。

## 决策
新增独立话题 `/usv/lab_sim/waypoint_reached`:
- `lab_sim_node` 内置差速制导跑航线, 到点时发布该话题(携带 wp_seq)。
- `mavlink_trigger_node` 在 `lab_mode.enabled` 时订阅该话题驱动采样状态机,
  非实验模式下仍只用真实 `/mavros/mission/reached`。

## 备选方案
- C1: `lab_sim` 直接发布到 `/mavros/mission/reached`, 伪装成飞控, trigger_node 无感。
- A(下游另起): lab_sim 自带完整采样逻辑, 不复用 trigger_node 状态机。
- SITL: 跑真正的 ArduPilot SITL 飞 AUTO 航线, lab_sim 不参与。

## 理由
- 不污染 MAVROS 命名空间: 向 `/mavros/mission/reached` 注入伪事件会与真实飞控数据
  混淆, 难以排查来源, 也可能干扰其他订阅该话题的节点。独立话题来源清晰可追溯。
- 最大化复用: 采样状态机零重写, 实验/真实仅在"到达事件来源"一处分叉。
- 拒绝 SITL: 引入 SITL 构建/运行环境过重, 与"Web 自闭环演示"目标不符。

## 后果
- `mavlink_trigger_node` 需新增一个订阅与 lab_mode 分支(数行), 是可接受的耦合。
- 实验模式总闸为 `lab_mode.enabled`; 关闭即回退到纯真实任务链路, 改动可控可回退。
- 模拟数据源时, 同一状态机在采样步骤分叉为"伪造电压"(详见 plan.md 阶段二)。
