# usv_ros Frontend Agent Rules

## OVERVIEW

React 19 + Vite 7 Web 控制台，构建产物写入 `../static/dist` 并由 `scripts/web_config_server.py` 提供。

## WHERE TO LOOK

| 任务 | 位置 |
|---|---|
| 路由/页面入口 | `src/App.tsx`、`src/pages/` |
| 实时监控 | `src/pages/Monitor.tsx`、`src/components/system-health-card.tsx` |
| 手动控制 | `src/pages/Manual.tsx`、`src/components/injection-pump-card.tsx` |
| 自动化 | `src/pages/Automation.tsx` |
| 数据中心 | `src/pages/Data.tsx` |
| 地图/污染物 surface | `src/pages/Map.tsx` |
| 实验航线 | `src/pages/Lab.tsx` |
| 通用 UI | `src/components/ui/` |
| 全局状态 | `src/store.ts` |

## CONVENTIONS

- API 和 Socket.IO 字段以 `../scripts/web_config_server.py` 为准。
- 系统健康字段来自 `/usv/system_health` 与 Socket.IO `system_health`。
- 污染物地图第一阶段只在 Web 端承载，不同步到 QGC 热力图。
- 修改前端后运行 `npm run build`，确认产物落到 `../static/dist`。
- 页面是现场工具，优先密集、可扫读、低装饰；不要做营销式首页。

## ANTI-PATTERNS

- 只改前端字段名，不同步 Web API 和测试。
- 把运行说明、快捷键说明写成大块可见文案。
- 让按钮/卡片文本在窄屏溢出。

## COMMANDS

```bash
npm run build
```
