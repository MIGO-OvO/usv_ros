# Design

## Overview

ROS Web 控制台是 React 19 + Vite 7 + Tailwind CSS + Radix UI/shadcn 风格组件构成的产品工具界面。当前主入口为 `frontend/index.html`，路由在 `frontend/src/App.tsx`，构建产物输出到 `static/dist/` 并由 Flask `scripts/web_config_server.py` 提供。

## Visual Theme

界面采用浅色为主、可切换暗色的工程控制台风格。主结构是固定左侧导航、移动端底部导航、内容区 `max-w-7xl` 的页面容器，以及密集卡片、表单、图表和地图工作区。视觉目标是稳、清楚、可扫读，而不是品牌宣传。

## Color Palette

当前色彩由 `frontend/src/index.css` 的 HSL CSS 变量驱动，并在 `frontend/tailwind.config.js` 映射到 Tailwind token：

- `background` / `foreground`: 主页面背景与正文。
- `card` / `card-foreground`: 面板与卡片。
- `primary`: 主操作、当前导航项和关键选择态。
- `secondary`, `muted`, `accent`: 次级按钮、背景层、hover/focus 层。
- `destructive`: 危险或失败状态。
- `chart-1` 到 `chart-5`: 图表与数据可视化色。
- 现场状态色：emerald 表示在线/成功，red 表示断开/失败，amber 表示等待/警告，blue 表示链路或泵组在线。

后续设计应优先延续这些 token；新增颜色必须先确认语义角色，避免只为装饰加色。

## Typography

字体栈为 `Inter`, `Noto Sans SC`, `system-ui`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `Roboto`, `Helvetica Neue`, `Arial`, `WenQuanYi Micro Hei`, `sans-serif`。中文界面以 14px 左右的产品 UI 文本为主，页面标题通常为 `text-2xl` 或 `text-3xl`、`font-semibold`/`font-bold`、`tracking-tight`。

产品 UI 不使用展示字体，不使用流体巨型标题。表格、状态卡、表单标签和按钮保持固定 rem 尺寸，优先保证中文标签不溢出。

## Layout

- 桌面端：左侧 16rem 固定导航，主内容 `md:pl-64`。
- 移动端：隐藏侧栏，使用底部导航，主内容保留底部空间。
- 页面容器：常见为 `p-4 md:p-8 space-y-6 max-w-7xl mx-auto`。
- 工具页面：使用结构性 grid，例如监控页指标卡、自动化三栏配置、地图/实验室左侧控制栏加右侧地图。
- 地图和数据页需要稳定高度、`min-h-0` 和 overflow 控制，避免表格、地图或日志撑破视口。

## Components

现有通用组件位于 `frontend/src/components/ui/`，遵循 Radix/shadcn 风格：

- `Button`: default, destructive, outline, secondary, ghost, link；尺寸 default/sm/lg/icon。
- `Card`: `rounded-xl border bg-card text-card-foreground shadow`，常用于单个工具面板或状态面板。
- `Input`, `NumericInput`, `Label`, `Switch`, `DropdownMenu`, `AlertDialog`, `Toast`。
- 页面级组件包括系统健康、链路诊断、进样泵、航点采样、日志查看、地图控制等。

后续改动应复用这些组件。按钮优先使用 lucide 图标加简短中文标签；纯图标按钮必须具备 tooltip 或可访问名称。

## Interaction

产品动效保持 150-250ms，服务状态变化、hover/focus、弹窗或 toast，不做页面入场表演。禁用态、加载态和错误态必须明确，尤其是分光、采样、泵控、保存配置、导入导出和地图任务操作。

## Responsive Behavior

窄屏优先保证任务可完成：控制按钮允许换行，表格/日志/地图使用滚动容器，长中文标签不挤压数值。地图、实验室测试和数据中心应避免固定大宽度布局，必要时把侧栏控制面板改为纵向堆叠。

## Accessibility

所有交互控件需要键盘焦点态；状态色必须配文字；toast、对话框和下拉菜单继续使用 Radix 语义。图表和地图上的关键状态要在旁侧面板或摘要文本中可读，不能只依赖颜色或 hover。

## Design Risks To Watch

- 当前 README 提到 “Apple 风格界面”，但项目规则要求现场工具优先密集、可扫读、低装饰；后续设计应把 “Apple 风格” 理解为清晰和一致，而不是大留白、毛玻璃或营销感。
- `--radius: 1rem` 与部分卡片 `rounded-xl` 已接近产品 UI 上限；不要继续增大卡片圆角。
- `Card` 默认同时有 border 和 shadow，后续若做 polish 可考虑降低阴影或统一边框层级，避免工具界面显得松散。
- 地图、污染物 surface 和数据可视化承担核心任务，颜色必须表达数据语义，不应被全局品牌色吞掉。
