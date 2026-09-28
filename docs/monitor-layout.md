# 外场监控布局

监控页按「系统摘要 → 定位 → 全宽波形 → 基线任务 → 系统状态 → 高级诊断」组织。
使用现有 React/Tailwind/Button/Canvas 体系，无新增依赖。GPS 轮询共用一个 hook；
原 Socket.IO 订阅、RingBuffer、命令回调、基线/毛刺测试流程不变。

## 定位显示与采样安全

- 正常：原始 Fix 有效，显示坐标有效且新鲜。
- 降级：有有效显示坐标，但原始 Fix 缺失或坐标超过后端新鲜度阈值。
- 不可用：没有有效显示坐标，或全部候选坐标超过显示层严重超时阈值（默认 10 秒，不小于后端新鲜度阈值）。
- 请求失败/尚未收到诊断：灰色未知，不声称定位正常。

显示坐标优先选择有效 NavSatFix，其次 GLOBAL_POSITION_INT、有效 GPS_RAW_INT；
新鲜候选优先于旧候选。显示来源与实际采样准入分开标注。
**GLOBAL_POSITION_INT 可显示不等于采样已获准**：后端严格 NavSatFix 校验没有变更。
原始报文、坐标、Fix、卫星数、精度、配置、时间、固件、日志与警告全部保留在底部诊断。

## 验证

在 frontend 运行：

```sh
npm run build
npm run lint
node --test scripts/test-monitor-gps.ts scripts/test-time-series.ts scripts/test-automation-controls.ts
npm run test:lab-coordinates
```

浏览器回归：启动 `npm run dev -- --host 127.0.0.1 --port 5178`，运行
`node scripts/smoke-monitor.mjs`。需要可用的 Playwright 与 Edge；可以用环境变量
`USV_PLAYWRIGHT_PATH` 指向现有 Playwright 的 index.mjs，无需向项目添加依赖。
测试拦截所有 API 和 Socket.IO，使用合成状态，绝不连接硬件。

覆盖 1440/820/390/320px、单图全宽、Tab 历史保留、固定图高、暂停视图持续接收、
CSV、清空、sticky 控制、基线开始/取消、诊断折叠/展开、暗色、三级定位及断连。
截图写到忽略目录 `.omo/monitor-layout/`。

2026-09-28：构建/类型检查通过，27 项自动测试通过，浏览器回归通过。
lint 无错误；Data.tsx 与 system-log-viewer.tsx 各有一个既有 hook dependency 警告。
构建仍有既有大 chunk 与 Browserslist 数据陈旧提示。
Canvas 仍使用像素预算 min/max 降采样，只挂载当前波形，不增加数据订阅。
未进行 Jetson/ROS/实船硬件联调；发布后应核对真实遥测持续更新与真实控制响应。
