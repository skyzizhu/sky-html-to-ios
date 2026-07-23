# Multi-page Routing

多页面转换先建立路由图，再逐页生成 UI IR 和原生页面。不得只转换入口页，也不得从文件名猜测完整导航关系。

## 发现流程

对本地入口执行：

```bash
NODE_PATH=<workspace-node-modules> node scripts/discover_html_routes.cjs \
  --html <entry.html> \
  --out html-route-graph.json
```

对可访问 URL 使用 `--url`。默认仅遍历同源页面并阻止无关远程请求；只有资源来源可信且确有需要时才使用 `--allow-remote`。

脚本安全读取：

- `a[href]` 及 History/Hash 形式的同源路由
- `form` action 与 method
- `data-ios-action`、`data-ios-target` 和 `data-ios-presentation-style`
- `.page[id]`、`[role=tabpanel]`、`data-page`/`data-screen` 控制的单文档虚拟页面
- 页内锚点和外部 URL

route discovery 阶段不任意点击普通按钮，不提交表单，也不执行可能修改数据的业务动作。随后必须按 `dynamic-interaction-discovery.md` 运行交互发现器，用 AST 和隔离 probe 补齐按钮、函数调用、计时器与 class 状态产生的动态边。无法确认的目标保留为 `unresolvedTarget` 或 interaction graph 的 `unresolved`。

## 图语义

- `screens`：静态页面或可单独访问的 SPA 路由状态。
- `virtual-screen-state`：同一文档内需要 activation selector 才显示的移动页面；原型展示导航仅用于发现，不自动生成 App 业务导航。
- `prototype-document-shell`：承载手机画板、说明和导航的展示外壳，`includeInNativeConversion=false`，不生成 iOS 页面。
- `edges`：来源屏幕、来源 selector、动作、目标屏幕和置信度。
- `targetScreenId`：已经关联的原生目标页。
- `targetAnchor`：页内滚动位置，不生成新 ViewController。
- `externalURL`：外部链接，映射到系统打开 URL 能力。
- `unresolvedTarget`：预期是原生页面或弹层，但尚未找到目标。

本地临时服务器 URL 只用于提取；生成代码和验收记录应优先使用 `route`、`localPath` 和 screen ID。

## 原生映射

为每个 screen 独立提取 render tree 和 UI IR，并保持 route graph screen ID 一致。然后按工程已有架构映射：

- SwiftUI：现有 Router/NavigationStack/NavigationPath、sheet、fullScreenCover、popover 或 alert。
- UIKit：现有 Coordinator/Router、UINavigationController、present、dismiss、child containment 或系统 URL 打开方式。
- 浏览器 history back：映射为 pop 或 dismiss，取决于目标页面的呈现关系。
- Tab/Split/Page 容器：作为容器关系表达，不错误地生成连续 push。

同一目标可由多个入口到达；不要复制页面实现。参数化路径、查询参数和 hash 参数需要保留为 route payload，不能合并成无参数页面。

`switchTo('page2')`、显示 class 切换或同文档虚拟页面只证明 Web 状态发生变化，不自动等价于 iOS push。结合项目 Router、返回关系和 presentation 层级选择原生动作；不能唯一确定时使用带源指纹的 override 文件。

## 未解析路由

先搜索 HTML 中的模板、路由配置和显式 action，再检查同目录页面。仍无法确认时：

1. 创建带 TODO 的路由契约或局部占位目标，不编造业务页面。
2. 在交付报告中列出来源 screen、selector、action 和 target hint。
3. 若该边属于核心验收路径，必须向用户确认后才能宣称多页面转换完成。

## 验收

- 每个 required screen 都能从入口到达。
- 每条 required edge 的正向动作和返回/关闭动作都可执行。
- 深链、参数、Tab 状态和弹层关闭后状态符合原型。
- 截图矩阵至少覆盖每个 screen 的 initial state 和每种呈现方式。
