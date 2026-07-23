---
name: sky-html-to-ios
description: 将可运行的移动端 HTML 高保真原型转换为可编译、可运行、可视觉验收的 iOS 原生页面。适用于用户要求把 HTML、网页原型、移动端效果图页面还原为 SwiftUI 或 Swift UIKit，接入现有 Xcode 工程，复刻页面布局、样式、资源、导航与基础交互，或修正已有 HTML 转 iOS 结果。必须通过浏览器提取真实计算样式和布局，生成 UI IR，遵循项目现有规范，并执行构建、模拟器截图和视觉差异验证。不要用于把网页简单嵌入 WKWebView，也不要用于仅凭截图生成页面。
---

# HTML to iOS Native

将可运行的移动端 HTML 原型转换为真实 iOS 原生页面。优先保证目标设备上的视觉保真、原生结构、工程兼容和可验证性。

## 核心原则

1. 使用浏览器实际渲染结果，不凭源码或肉眼猜测最终尺寸。
2. 先生成 UI IR，再生成 SwiftUI 或 UIKit 代码，保持来源可追溯。
3. 遵循现有项目架构、组件和依赖；不要强制引入 MVVM、Router 或第三方库。
4. 使用真实原生 View。禁止用整页截图、整块截图或 `WKWebView` 冒充原生实现。
5. 保留可合法访问的本地图片、图标和字体资源；资源缺失时才使用明确标记的占位内容。
6. 必须验证编译结果。环境允许时，还必须执行 HTML 截图、模拟器截图和视觉差异检查。
7. 纠偏时只修改产生差异的节点或组件，避免重写无关页面。
8. 响应式页面必须从多宽度计算结果推断原生约束；禁止用运行时整页缩放代替 Auto Layout。

## 支持范围

主要技术栈：

- SwiftUI
- UIKit + Swift

默认支持：

- 单页或多页移动端 HTML
- Flex、Grid、普通文档流、绝对定位和常见响应式布局
- 文本、图片、SVG、按钮、输入框、列表、滚动、Tab、弹窗和基础动画
- CSS transition/keyframes、伪元素、常见遮罩滤镜和多状态视觉验收
- 页面 push、sheet、full-screen cover、overlay、back、dismiss 和外部链接
- pop/pop-to-root、popover、alert、Tab/Split/Page 容器和 child ViewController containment
- 本地 CSS、JavaScript、图片和字体资源
- 已有 Xcode 工程接入；目标目录无 iOS 工程时可创建 SwiftUI/UIKit Xcode App 工程

默认不补全：

- 未在原型中定义的接口、认证、支付和后端业务逻辑
- 复杂游戏、WebGL、Canvas 应用或不可解析的第三方网页组件
- 仅凭静态截图推断完整页面结构
- Objective-C；仅在用户明确要求且项目确实使用 Objective-C 时作为兼容扩展处理

## 决策规则

### 技术栈

按以下顺序决定，不要机械地每次询问：

1. 用户明确指定 SwiftUI 或 UIKit 时，使用用户指定项。
2. 现有模块明显使用 SwiftUI 时，沿用 SwiftUI。
3. 现有模块明显使用 UIKit 时，沿用 UIKit。
4. 新项目且用户未指定时，默认 SwiftUI。
5. 仍无法判断且选择会显著改变产物时，再向用户确认。

### 目标设备

优先使用用户指定的机型、逻辑尺寸、方向和外观。没有指定时：

- 从 HTML viewport、手机容器或现有项目测试配置推断。
- 仍无依据时，以 `393 x 852 pt`、竖屏、浅色作为视觉基线，并在交付说明中标注该假设。
- 不要同时声称使用 393pt 目标宽度，却按固定 375pt 计算比例。

### 尺寸换算

先判断 HTML 类型：

- 响应式 HTML：直接以目标设备逻辑宽度渲染。通常使用 `1 CSS px = 1 iOS pt` 的目标坐标系，不再二次整体缩放。
- 固定宽度稿：只用 `scale = targetWidthPt / sourceAppRootWidthCssPx` 做一次设计 token 归一化，原生页面仍使用约束布局。
- 物理像素稿：只有在 viewport、容器尺寸或用户信息能够证明倍率时，才按倍率换算。

位置和尺寸优先取浏览器 `getBoundingClientRect()` 结果。保留百分比、内容驱动、容器驱动和 min/max 约束语义，不要把所有数值都变成固定 frame。

## 执行流程

### 总控入口

默认先读取 `references/orchestration.md`，从 Agent 当前工作目录运行：

```bash
python3 scripts/run_html_to_ios.py --html <prototype.html>
```

已经具备校验通过且无未决交互的 UI IR 时，重复传入 `--ir`。总控负责工作目录工程发现、输入预检、必要时创建 App、逐页 IR 构建、文本标定、多尺寸响应式分析、HTML 状态基准、代码生成、target 接入、新工程入口接线、`xcodebuild`、XCUITest 状态截图和确定性视觉门禁。除非正在定位单个阶段故障，否则优先使用总控，不要求用户手工串联脚本。HTML 模式只有 required states 完整且视觉门禁通过时才返回顶层 `completed`；仍须检查 `qualityGates` 和 review bundle，不能把 pending/failed 状态说成高保真验收通过。

- `empty-no-ios-project`：默认创建 App；用户未指定技术栈时使用 SwiftUI。
- 一个 Xcode 工程：自动选择；target、scheme 或技术栈无法唯一判断时返回 `needs-input`。
- 多个 Xcode 工程：必须用 `--project` 明确，禁止猜测。
- 只有 Swift Package：确认需要独立宿主后使用 `--create-package-host-app`，禁止默认污染 Package workspace。
- 无效 IR 或未解决交互必须在创建工程前停止。
- 用户传入的 `--interaction-overrides` 是只读确认契约；自动发现的新草稿必须另存，禁止覆盖。
- 新工程自动接生成入口；现有工程不覆盖 App/SceneDelegate/Router，未接入口时报告 `generated-needs-entry-integration`。
- `--dry-run` 只给出工程决策且不写文件；正式执行默认构建。

### 1. 校验输入

确认：

- HTML 文件、目录或可访问 URL
- 是否存在对应 iOS 工程
- 目标页面范围
- 用户已经明确指定的技术栈、设备、最低 iOS 版本和资源要求

只询问会实质改变结果且无法从文件推断的信息。路径不存在、HTML 无法运行或目标页面不明确时停止生成并说明问题。

先读取 `references/html-authoring-contract.md`。总控在浏览器发现前运行 `scripts/validate_html_authoring_contract.py`，将输入分为 L0 推断、L1 结构化或 L2 确定性三个等级。普通 HTML 没有 `data-ios-*` 时允许以警告继续；重复稳定 ID、非法枚举、缺少或不存在的 action target 必须停止。有效显式契约优先于语义 HTML/ARIA 与运行时推断，但显式契约和实际行为冲突时必须产生待确认项，不能静默覆盖。

### 2. 检查 iOS 工程

总控会运行工程检查与组件发现；单阶段调试时可手动运行，并读取发现的项目规范文件：

```bash
python3 scripts/inspect_ios_project.py <ios-root> --out ios-project-report.json
python3 scripts/discover_ios_components.py <ios-root> --out ios-component-index.json
```

检查：

- SwiftUI/UIKit 使用比例
- deployment target、Swift 版本、scheme 和 target
- SwiftPM、CocoaPods、Carthage 和现有 UI 依赖
- Xcode 16 同步文件夹或传统 `.xcodeproj`
- 颜色、字体、路由、基类、Design System 和现有同类页面
- 可复用 SwiftUI/UIKit 组件、Cell、UIControl、Router、设计令牌和资源

规范优先级：用户当前指令 > 目标模块现有模式 > 项目规则文件 > 本技能默认规则。

读取 `references/ios-project-conventions.md` 和 `references/project-component-discovery.md` 合并生效规范。不要仅凭依赖存在就决定目标模块必须使用该依赖，也不要仅凭名称相似强制复用组件。

当状态是 `empty-no-ios-project` 且用户需要完整 App 时，运行 `scripts/create_ios_project.rb` 创建工程；用户未指定技术栈时默认 SwiftUI。状态是 `swift-package-only` 时先判断 Package 是否为目标 UI 模块；需要独立 App 时再创建工程并接入。创建器检测到现有工程会拒绝覆盖。创建后重新运行两项检查。

随后读取 `references/sdk-availability-policy.md`，运行：

```bash
python3 scripts/inspect_ios_sdk.py \
  --minimum-ios <deployment-target> \
  --out ios-sdk-report.json
```

以 Apple 当前文档、本机 iPhoneOS SDK 和工程 deployment target 共同决定 API。禁止把技能编写时的某个固定 SDK 当成永久最新版。

### 3. 在浏览器中渲染 HTML

先读取 `references/multi-page-routing.md`。对入口运行 `scripts/discover_html_routes.cjs`，生成 `html-route-graph.json`。静态多页、History/Hash SPA 路由、单文档中的 `.page[id]`/tabpanel/`data-page` 虚拟页面和显式 `data-ios-action` 都进入图；原型展示导航标记为 discovery-only，不误当成 App 业务导航。不任意点击可能产生副作用的按钮。每个 screen 使用同一 screen ID 单独提取 render tree，无法确认的动态边保留为 `unresolvedTarget`。

随后读取 `references/dynamic-interaction-discovery.md`，运行 `scripts/discover_html_interactions.cjs` 生成 `interaction-state-graph.json` 与 `html-to-ios.overrides.json`，再运行 `scripts/validate_interaction_graph.py`。必须结合 JavaScript AST 与隔离浏览器 probe 识别 addEventListener、间接函数调用、局部状态、弹层、计时完成和动态页面跳转；不能只用正则扫描源码。源 HTML 保持只读，歧义写入带 SHA-256 指纹的旁路覆盖文件。只向用户询问会改变原生所有权或核心流程的未解析项。

再读取 `references/html-extraction.md`。使用 `scripts/extract_render_tree.cjs` 在固定 viewport 中运行每个页面，输出：

- 基准截图
- DOM 与稳定节点 ID
- `getComputedStyle()` 结果
- `getBoundingClientRect()` 坐标
- 伪元素、滚动、裁剪、层级和可见性
- 图片、SVG、字体与背景资源引用
- 链接、表单、内联事件和可识别交互
- 疑似手机画板容器
- motion pass 中的 transition、animation、keyframes 和 timing
- `::before`/`::after` synthetic nodes 及估算边界
- 文字 Range 行框、行数、字体加载状态和裁剪信息
- 内联 SVG markup、图片 URL 与 CSS background 资源详情

等待字体和页面稳定后再提取。默认阻止与页面来源无关的远程请求；不得让不可信 HTML 访问用户敏感文件或凭据。

### 4. 选择页面根容器

不要用“检测到 0 或 1 个候选就一定取整个页面”的简单规则。

- 页面本身就是移动端页面：使用 `body` 或应用根节点。
- 大展示板中包含一个或多个手机画板：只选择画板内部应用内容。
- 去除纯手机外壳、模拟刘海、展示标签和背景板装饰。
- 多个候选且无法可靠确定时，展示候选的 selector、尺寸和截图位置，请用户确认。

### 5. 生成并校验 UI IR

先运行 `scripts/build_ui_ir.py`，将 `extract_render_tree.cjs` 的输出转换为可审查的 UI IR 草稿：

```bash
python3 scripts/build_ui_ir.py render-tree.json \
  --out ui-ir.json \
  --screen-id home \
  --ui-stack swiftui \
  --route-graph html-route-graph.json \
  --interaction-graph interaction-state-graph.json \
  --interaction-overrides html-to-ios.overrides.json \
  --sdk-report ios-sdk-report.json \
  --minimum-ios 16.0 \
  --target-width 393 \
  --target-height 852
```

脚本会使用手机候选中的 `recommendedRootRuntimeId`；用户已确认具体容器时，传 `--root-runtime-id` 或 `--root-selector`。提供三份图契约后，脚本按 route graph 排除其他虚拟页面子树、保留共享控件，将动态交互和状态合并到当前 screen IR，并应用指纹一致的原生所有权 resolution。此时无行为证据的普通 button 不得生成为 `unknown` 业务动作。随后按 `references/ui-ir-schema.md` 审查和补充 UI IR。IR 必须包含：

- 页面元数据、目标 viewport 和 system chrome
- 节点树、稳定 ID、来源 selector 和来源矩形
- 布局语义、样式、内容、资源和交互
- 页面导航图和状态变化
- 支持级别、降级项和警告
- 每个节点的 SwiftUI/UIKit 控件建议、样式策略、置信度和映射理由
- 每个候选 API 的 SDK 核验状态、最低版本和降级路径
- motions、关键帧采样点和 visual state matrix
- 动态交互的多节点来源、跨 screen 副作用、自动迁移和 prerequisite interaction sequence

运行 `scripts/validate_ui_ir.py`。自动映射置信度低于 `0.7` 的节点必须人工复核；IR 未通过校验时不得开始生成代码。

随后读取 `references/text-calibration.md`、`references/responsive-auto-layout.md` 和 `references/page-regions-and-system-chrome.md`，运行 `scripts/build_text_calibration.py` 和 `scripts/analyze_responsive_layout.cjs`。固定缩小画板只允许将设计 token 一次性归一化到基准设备；原生页面始终使用 Auto Layout/SwiftUI layout，不能运行时整体缩放。文字行数、baseline、截断和富文本 range 进入专项验收。顶部栏、底部栏、浮动操作和 presentation 必须进入 screen regions，不得只靠 class 名在代码生成阶段临时猜测。

多页面项目保留独立 `html-route-graph.json` 和 `interaction-state-graph.json` 作为跨 screen 契约。每个 screen 的 UI IR 负责页面内部结构；路由图负责 screen 集合，交互图负责 push/present 候选、sheet、popover、Tab、返回/关闭、计时跳转和局部状态。覆盖文件中的已确认原生所有权必须合并进 IR，禁止把所有页面压成一个 screen IR。

### 6. 规划原生结构

读取 `references/common-mapping-rules.md`、`references/control-mapping-matrix.md`、`references/native-component-catalog.md`、`references/interaction-rules.md`、`references/navigation-presentation-containment.md`、`references/page-regions-and-system-chrome.md`、`references/custom-component-fallback.md`、`references/motion-and-effects.md`、`references/edge-case-policy.md`、`references/multi-page-routing.md`、`references/project-component-discovery.md`、`references/text-calibration.md` 和 `references/responsive-auto-layout.md`，再按技术栈读取：

- SwiftUI：`references/swiftui-rules.md`
- UIKit：`references/uikit-rules.md`

先输出内部映射计划，再写代码：

- screen → 原生页面
- 重复结构 → 可复用组件或 cell
- HTML 节点 → 原生 View 类型
- 页面关系 → 现有 Router/Coordinator/NavigationStack
- Web 状态 → 原生局部状态
- 不可直接映射的 CSS → CALayer、Core Graphics 或局部 UIKit fallback
- 系统无对应控件 → 项目组件、组合 View、自定义 UIControl/View 或在确有生命周期需要时自定义 ViewController

控件映射必须先判断语义，再选择原生控件，最后还原外观。不要仅按 HTML tag 映射，也不要为了视觉方便把 Button、输入框和选择控件退化成无语义的普通 View。

不要按 DOM 层级机械生成一层层无意义容器，也不要为了代码少而抹平重要布局边界。

只有所选技术栈的 availability 已核验且具备旧版本 fallback 后，才开始生成对应代码。`addChild`/`removeFromParent` 必须遵循完整 containment 生命周期，不能当成普通显示隐藏。

### 7. 处理资源

读取 `references/asset-and-font-rules.md` 和 `references/resource-conversion.md`，运行 `scripts/prepare_ios_assets.py` 生成 Asset Catalog 暂存目录和转换 manifest。

- 复用已有 Asset Catalog 中相同资源。
- 将可访问的本地图片、矢量图和字体按项目规则接入。
- 只有视觉与语义都匹配时才用 SF Symbols 替换图标。
- 远程资源无法取得时保留尺寸、裁剪语义和明确占位标记。
- 不要未经确认下载或嵌入来源不明、许可不明的资源。

### 8. 生成原生代码

先读取 `references/code-generation-and-incremental-update.md` 和 `references/generated-source-layout.md`。UI IR 校验通过且交互不存在未决原生所有权后，使用 `scripts/generate_ios_from_ir.py` 生成可编译原生基线：

```bash
python3 scripts/generate_ios_from_ir.py \
  --ir page1-ui-ir.json \
  --ir page2-ui-ir.json \
  --out-dir <source-root>/Generated/HTMLToIOS \
  --ui-stack swiftui
```

- 多页面重复传入 `--ir`，第一份是默认根页面。
- 默认生成目录必须与人工源码隔离，并保留 `.html-to-ios-generation.json`。
- 输出路径必须以 `Generated/HTMLToIOS` 结尾。内部按业务模块生成 `<Module>/Screens|Controllers|Views`，App 级 Navigation/Tab 放入 `Core/Navigation`，通用契约和运行时放入 `Core`，资源放入 `Resources`；禁止把全部文件平铺在一个目录。
- 只有确认目标工程的生成目录规范无法采用标准路径时，才可显式使用 `--allow-nonstandard-output`，并保持等价职责分层和独立生成所有权。
- 生成器拒绝 `requiresResolution=true` 的交互；禁止用 `--allow-unresolved` 掩盖正式交付中的歧义。
- 用户修改过的生成文件不得覆盖；候选版本进入 `<out-dir>.conflicts`，由 Agent 做节点级合并。
- 生成的 JSON 载荷必须作为 target resource 接入，不能内联成超大 Swift 字符串。
- 通用运行时只是原生基线；发现现有 Router、Design System、Cell 或控件时，按映射计划替换为项目组件。
- 保持项目命名、目录、访问控制和状态管理风格。
- 静态页面不强制创建 ViewModel。
- 仅在具有独立职责、重复使用或明显降低复杂度时拆组件。
- 每个可交互节点使用 UI IR 中的稳定 ID 作为 `accessibilityIdentifier`。
- 保持 source node → IR node → native view 的追溯关系。
- Safe Area、状态栏、导航栏和 Home Indicator 只计算一次。
- 自绘顶部栏和底部操作栏必须从滚动内容拆出；普通文档 footer 保持随内容滚动。
- 容器轴向优先取浏览器 computed style 的 `display` 与 `flex-direction`；`layout.mode` 只作为缺失 computed style 时的回退。`row-reverse`/`column-reverse` 必须同步原生子节点顺序，不能因元素是 absolute/fixed 就丢失其内部 Flex 语义。
- 浏览器测得的 `preferredWidth` 不能无条件套到每层 SwiftUI 容器。结构容器由父级 Stack、Grid 和可用宽度分配；叶子节点保留理想宽度，Button、输入和选择类原生控件在来源明确时保留最小宽度，避免文字内在尺寸把等宽操作栏压缩。
- SwiftUI 尺寸 Modifier 只有存在有效参数时才能生成；禁止批量输出全为 `nil` 的 `.frame(...)`，也禁止在父子结构容器上叠加相互反馈的有限 `maxWidth`。
- `@Published` 集合状态用于删除、隐藏、选择或展开时必须产生可观察的新值；交互不能只改变临时局部副本或依赖不确定的原地集合变更。
- 背景图按容器 background 渲染并保留 cover/contain、position、repeat；图标保留测量尺寸，不统一夹成固定小号。
- 不实现原型中不存在的业务逻辑。

### 9. 接入 Xcode

读取 `references/xcode-integration.md`。

- SwiftPM 和 Xcode 同步文件夹：放入正确 target 路径。
- 传统 `.xcodeproj`：运行 `scripts/integrate_generated_sources.rb --project <project> --target <target> --generated-dir <dir>`，再解析验证 Compile Sources 和 Copy Bundle Resources；不得用正则修改 `project.pbxproj`。
- SwiftUI 生成入口是 `HTMLToIOSGeneratedRootView`，UIKit 是 `HTMLToIOSGeneratedRootViewController`。读取现有启动和路由结构后再接入；脚本不会擅自替换 App 根入口。
- 无法安全修改工程文件时，保留源码并准确报告未关联状态，不要用正则直接修改 `project.pbxproj`。

### 10. 构建与视觉验证

读取 `references/visual-validation.md`。

1. 使用明确的 scheme、destination 和 DerivedData 路径运行 `xcodebuild`。
2. 修复由本次生成引起的编译错误。
3. HTML 总控模式默认已经运行 `scripts/build_visual_state_manifest.py`，从 UI IR 生成 HTML actions 与 iOS accessibility actions；先复用报告中的 manifest。
4. HTML 总控模式默认已经运行 `scripts/capture_html_states.cjs` 捕获 required HTML states；只有产物缺失或正在单阶段调试时才手动重跑。
5. 总控默认使用 `scripts/prepare_visual_ui_tests.rb` 创建隔离且带 ownership 标记的 `HTMLToIOSVisualTests` target，再由 `scripts/capture_ios_states.py` 执行 XCUITest、导出 xcresult 附件并归一化到目标逻辑 viewport。现有同名非托管 target 不得覆盖；单阶段调试时才手动运行这两个脚本。
6. 对移除、隐藏、展开、选择和路由类交互，视觉 manifest 应携带可推导的后置状态断言；XCUITest 必须先验证目标节点消失、出现、选中或路由到达，再截图。只完成 tap 而页面状态未变化不得算作有效状态捕获。
7. 运行 `scripts/build_visual_review_bundle.py` 检查精确尺寸、全局 mismatch、平均差异、critical region 和文本 edge mismatch。任一 required state 超阈值必须重新生成和截图，不能由多模态评语改成通过。
8. 判断当前 Agent 的实际图像查看能力，并以 `available`、`unavailable` 或 `auto` 传给 review bundle；不要根据模型名称猜测。脚本生成每个状态的像素报告、comparison、heatmap、overlay、regions 和能力门控状态。
9. 能力为 `available` 时读取 `references/visual-agent-review.md`，实际打开图片并检查 failed-threshold 状态；能力为 `unavailable` 时标记 `not-run`；`unknown` 时先尝试打开一张图片，不能把 unknown 当成完成。
10. 按 UI IR node 局部纠偏，重新构建并回归所有受影响状态；默认最多 3 轮。
11. 至少复核首屏、有意义的长页末端、弹层和切换状态。动画 0/50/100 帧只有具备原生确定性采样钩子时才设为 required，否则作为 advisory，不能用三张相同静态图冒充动画验证。
12. 在项目支持的 320/375/393/430pt 或实际设备宽度上验证 Auto Layout、文字换行、边距和横向溢出；使用 `scripts/compare_text_calibration.py` 核对 iOS 文字测量结果。

对于大展示板中的固定手机画板，状态捕获使用 HTML 源 viewport 执行动作，再按 UI IR 的目标 viewport 生成归一化对比图；这是验收图片的单次设计归一化，不得转化为 iOS 运行时整页缩放。归一化方式和原始尺寸必须写入 captures report。

如果环境无法启动模拟器，编译验证仍是必需项，并明确报告未完成的视觉验证及原因。

模型不支持图像时，仍须执行状态矩阵和确定性像素检查，并把多模态阶段标记为 `not-run`，不能假装完成视觉走查。

## 交付要求

最终报告必须包括：

- 生成和修改的文件
- 代码生成清单、生成目录、冲突候选和入口符号
- 技术栈、目标设备和关键假设
- Xcode target 关联及构建结果
- HTML 基准截图、iOS 截图和视觉差异报告路径
- 多页面路由图、交互状态图、歧义覆盖文件、工程组件索引和未解析项
- 文字校准、响应式约束分析和资源转换 manifest
- 已实现的页面与交互
- 资源替换、降级实现和未覆盖能力
- 需要人工确认的少量高风险差异

## 红线检查

交付前逐项确认：

- 没有整页截图、功能卡片截图或 `WKWebView` 伪装原生页面。
- 没有用统一绝对坐标替代本可结构化表达的布局。
- 没有把响应式页面再次整体缩放。
- 没有重复计算 HTML 模拟系统栏和 iOS Safe Area。
- 没有把自定义图标随意替换成不相似的 SF Symbol。
- 没有无视现有项目架构而强制引入 MVVM、Router 或依赖。
- 没有只看首屏而遗漏滚动末端、弹窗或切换状态。
- 没有修改源 HTML 来迎合提取，也没有把未运行/失败的动态探测写成已验证。
- 没有覆盖人工修改过的生成文件，也没有把带未决交互的 IR 当成正式代码输入。
- 生成 Payload 已加入正确 target 的 Bundle Resources，入口已按现有 App 架构接入。
- 构建成功，或已准确区分环境问题与本次代码问题。

## 纠偏规则

收到视觉反馈时：

1. 确定截图差异区域。
2. 映射到 UI IR 节点和原生 View。
3. 判断差异来自字体、布局、资源、Safe Area、渲染顺序还是交互状态。
4. 只修改相关组件。
5. 重新构建、截图和对比。
6. 报告本次修改范围及是否影响其他页面。
