# Page Regions and System Chrome

页面还原前先把 screen 拆成 `topBar`、`content`、`bottomBar`、`floatingAction` 和 `presentation`。区域判定必须同时使用几何、滚动、交互和语义证据，不能只查 class/id 名。

## 顶部区域

候选证据包括：靠近应用根顶部、宽度通常超过根宽 72%、高度约 32–160pt、`fixed/sticky`、`header/nav` 语义、横向布局、包含返回或操作按钮。判定后区分：

- 系统导航栏：原型表达标准标题、返回和 toolbar 语义，且项目使用系统导航；映射为 `UINavigationBar` 或 SwiftUI navigation toolbar。
- 自绘顶部栏：视觉结构、背景、标题位置或按钮布局明显定制；保留为 `topBar`，放在 Safe Area 下方。
- 沉浸式顶部内容：图片延伸到状态栏时不是导航栏；内容可以忽略顶部 Safe Area，但文字和操作仍需单独避让。

禁止同时显示 HTML 自绘导航栏和系统导航栏。HTML 模拟的状态栏、刘海和信号电池只用于基准裁剪，不生成应用 View。

### NavigationBar 决策规则

按以下顺序决定，不允许仅凭顶部矩形的位置直接升级为系统导航栏：

1. 显式 `data-ios-navigation-style=native|custom|hidden|immersive`；
2. 目标工程已有 Router/NavigationStack/UINavigationController 所有权；
3. 标准返回、标题和 toolbar 语义及其几何证据；
4. 视觉差异是否可由 `UINavigationBarAppearance`、large/inline title、scroll edge 和 toolbar placement 表达。

`native` 必须生成系统导航容器，并将 HTML 返回/操作节点提升为 leading、principal、trailing 或 primary toolbar item；原 HTML top bar 不再重复渲染。`custom` 保留为 safe-area 内自绘区域，系统导航栏隐藏。`immersive` 允许背景越过顶部 Safe Area，但标题、返回和交互点击区仍要安全避让。返回按钮的 `system|custom|hidden` 与页面进入方式分别建模，不能以隐藏系统返回按钮代替正确的 pop/dismiss 所有权。

导航容器所有权、导航栏绘制方式和滚动行为必须分开记录。页面可以处于 `UINavigationController`/`NavigationStack` 中，但视觉上使用 custom top bar；也可以使用系统栏并通过 large-title collapse、scroll-edge appearance 或 hide-on-swipe 响应滚动。只有浏览器滚动探测确认 region 随内容移出时才实现 scroll-away；确认先移动后吸附时才实现 sticky；确认透明度、transform 或高度变化时分别映射 hide/collapse/appearance-change。无法分类时保持 fixed 并进入必检状态，禁止为了“像导航”就强行使用系统置顶栏。

## 底部区域

候选证据包括：贴近应用根底部、宽度通常超过 72%、高度约 36–180pt、固定或吸底、横向排列，以及包含一个或多个交互控件。根据语义分为：

- `tab-bar`：通常有 3–5 个同级目的地，切换后保留各 Tab 导航状态。
- `bottom-action-bar`：结算、提交、下一步、编辑工具等当前页面操作。
- `bottom-toolbar`：多个对当前内容生效的工具命令。
- 普通 footer：位于长页面文档末端并随内容滚动，不应吸底。

生成时将持久底栏从滚动内容拆出，使用 `safeAreaInset` 或 Auto Layout 约束到 `safeAreaLayoutGuide.bottomAnchor`。底栏背景是否延伸到 Home Indicator 区域与按钮内容是否避让要分别处理，不能简单额外加一遍 34pt。

### TabBar 决策规则

- 只有显式 Tab 标注，或至少两个稳定主目的地且交互为 `switch-tab/select-tab` 时，才创建 `TabView`/`UITabBarController`；按钮数量多不能作为唯一证据。
- 每个 Tab 必须有稳定 `tab-id`、标题、目标 screen 和选中状态；图标、选中图标、badge、role、重复点击行为与 push 后可见性进入 IR。
- 每个主 Tab 拥有独立 `NavigationStack` path 或 `UINavigationController`，切换 Tab 不得清空其他 Tab 的导航状态。
- `reselect=pop-to-root` 回到该 Tab 根页；`scroll-to-top` 回根并滚到首屏；`keep` 不改变栈或滚动位置。
- `visibility=hide-on-push` 只在进入非 Tab 根 screen 时隐藏；底部操作栏、页面内 segmented control 和普通 footer 永远不得升级为 app-level Tab。
- `role=search` 等新 SDK 语义先检查最低部署版本；低版本使用视觉和选择行为等价的普通 Tab fallback，不得为使用最新 API 擅自提高 deployment target。

## 浮动操作

靠近右下角、尺寸约 36–96pt、绝对/固定定位且可点击的紧凑节点可判定为 `floatingAction`。它覆盖内容但不能改变 ScrollView 的内容高度；需要避开底栏、键盘和 Safe Area。

## 背景与媒体

- `background-size: cover` → 填满容器并裁剪；`contain` → 完整显示并允许留白。
- `background-position` 和 `object-position` 决定裁剪焦点，必须进入 IR。
- `background-repeat` 为 repeat 时使用 tile/image paint；目标栈不能精确表达时标记 fallback 并截图复核。
- 背景图属于容器装饰，不得被误生成为占据文档流的独立 Image。
- 图片和图标保留各自测量尺寸。仅当确认是可伸缩 artwork 时才允许自适应放大；不得把所有 icon 统一夹到 18–32pt。

## 底部弹层

底部遮罩、顶部圆角面板、grabber、内容高度和向下关闭手势共同构成 sheet 证据。记录 `detents`、grabber、交互关闭、背景 dim、圆角、滚动归属和键盘行为。优先使用系统 sheet；只有系统 detent、背景和转场无法表达原型时才使用自定义 presentation controller。

## 置信度与验收

区域 IR 保存 `nodeId`、`kind`、`confidence` 和 `evidence`。低置信度区域不自动升级为 app-level Tab 或系统导航。视觉状态至少覆盖：初始页、顶部栏、滚动到底部、每个主 Tab、底部操作栏、sheet 的每个关键 detent、键盘弹出和交互关闭。
