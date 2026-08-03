# System-First Native Control Selection

系统控件优先是转换硬约束，但“系统优先”不等于保留系统默认皮肤，也不等于强行使用不合适的控件。转换器必须同时验证语义、行为、视觉和 SDK 可用性。

## 决策顺序

1. 读取 HTML 原生标签、ARIA、表单属性和 `data-ios-*` 契约，确定语义。
2. 读取 JavaScript 和运行时交互图，确认 tap、change、focus、drag、submit、navigation 与 presentation 行为。
3. 提取 `getComputedStyle()`、真实矩形、状态样式和资源，评估系统控件的官方配置能力。
4. 核验目标工程已有组件、最低 iOS 版本与本机 SDK。
5. 按顺序选择：项目已确认组件 → 系统控件官方配置 → 系统控件加原生包装层 → 多个系统控件组合 → 自定义 `UIControl`/View → unsupported。

不得因为默认外观不同就放弃 `UIButton`、`UITextField`、`UITextView`、`UISwitch`、`UISlider`、`UISegmentedControl`、`UIDatePicker`、`UIMenu`、`UIAlertController`、系统 Sheet 或对应 SwiftUI 控件。先清理默认皮肤，再使用 configuration、appearance、style、CALayer 或不截获交互的布局包装层还原视觉。

## UI IR 决策契约

每个节点的 `nativeMapping.nativeControlDecision` 记录：

- `policy`: 固定为 `system-first-visual-fit-gated`。
- `decision`: `system-control`、`system-control-with-native-wrapper`、`system-view`、`system-container`、`native-composition`、`native-view`、`project-component` 或 `unsupported`。
- `systemCandidate` 与 `candidate.swiftUI/uiKit`: 系统候选。
- `semanticFit`、`behaviorFit`、`visualFit` 和 `visualComplexity`。
- `interactionActions`、`interactionTriggers`: HTML/JS 行为证据。
- `blockers`、`customization` 和 `fallbackChain`。
- `requiresCustomControl`: 是否确实需要自定义交互控件。
- `preserveSystemSemantics`: 封装后是否仍由系统子控件承担交互与无障碍。

## 视觉适配规则

- 背景、字体、content inset、统一边框/圆角、tint 和普通状态：保留系统控件并配置。
- 外阴影、渐变、变换、非对称边框或圆角：优先用不截获交互的包装 View，内部仍为系统控件。
- clip-path、filter、backdrop-filter、inset/multiple shadow：记录 blocker；优先局部 Layer/Core Graphics 包装，不把系统控件替换成手势 View。
- checkbox、radio、复杂品牌 Tab 等没有跨栈直接系统外观时，使用系统语义子控件或自定义 `UIControl` 组合，并保留 enabled/selected/focused/accessibility。
- 系统 Sheet/Popover/Alert 的可配置几何满足原型时必须使用系统 Presentation；无法满足时才升级为自定义原生 Presentation Controller。

## 生成约束

- 系统候选的 typed wrapper 使用普通 `UIView`/SwiftUI `View` 布局，内部交互继续由系统控件承担。
- 只有 `requiresCustomControl=true` 才允许自定义 `UIControl` 成为交互所有者。
- 包装层不得制造重复无障碍元素、target-action 或冲突手势。
- 纠偏优先修改系统 configuration、布局和 UI IR，禁止直接修改生成后的 Swift 文件。

## 必须复核

- 语义证据与 JavaScript 行为冲突。
- 多个系统候选均合理但会产生不同交互。
- blocker 需要牺牲键盘、焦点、VoiceOver 或原生导航生命周期。
- 自定义实现只带来很小视觉收益，却显著增加维护成本。
