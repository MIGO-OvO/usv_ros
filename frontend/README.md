# USV 水质监测系统 - 前端界面

基于 React + TypeScript + Vite 开发的无人船 (USV) 水质监测系统前端界面。

## 项目特点

- **实时监控**: 实时显示传感器数据和系统状态。
- **任务管理**: 配置和管理自动化采样任务。
- **现代化 UI**: 使用 Tailwind CSS 和 Shadcn UI 构建的 Apple 风格界面。
- **响应式设计**: 适配桌面端和移动端访问。

## 开发指南

### 启动开发服务器

```bash
npm install
npm run dev
```

### 构建生产版本

```bash
npm run build
```

## 目录结构

- `src/components`: UI 组件
- `src/pages`: 页面视图 (监控、自动化、数据、设置)
- `src/store.ts`: 全局状态管理 (Zustand)
- `public/usv-logo.svg`: 项目图标
