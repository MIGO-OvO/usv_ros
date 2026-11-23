# USV Web 控制台

控制台使用 React、TypeScript 和 Vite。页面服务于现场操作，优先保证按钮和状态可扫读，并检查窄屏上的控制区与数据表格。

## 开发和构建

在 `frontend/` 安装依赖并启动开发服务器。

```bash
npm install
npm run dev
```

前端改动完成后执行 `npm run build`。Vite 将产物写到 `../static/dist/`，由 Flask 提供。

## 源码入口

| 改动 | 入口 |
|---|---|
| 路由与公用组件 | `src/App.tsx`、`src/components/ui/` |
| 监控与健康状态 | `src/pages/Monitor.tsx`、`src/components/system-health-card.tsx` |
| 载荷手动控制与自动化 | `src/pages/Manual.tsx`、`src/pages/Automation.tsx` |
| 数据中心、地图与实验航线 | `src/pages/Data.tsx`、`src/pages/Map.tsx`、`src/pages/Lab.tsx` |
| 全局状态 | `src/store.ts` |

API 和 Socket.IO 的字段以 `../scripts/web_config_server.py` 为准，修改时同步核对前端调用和测试。系统健康由 `/usv/system_health` 汇总，经 Socket.IO `system_health` 送到前端。污染物热力图由 Web 地图页面展示。
