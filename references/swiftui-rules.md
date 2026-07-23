# SwiftUI Rules

仅在选择 SwiftUI 时读取。先遵循项目现有 SwiftUI 风格和最低系统版本。

## 结构选择

先读取 UI IR 的 `semanticType` 与 `nativeMapping.swiftUI`。置信度高于或等于 `0.7` 时以该原生语义为起点，再根据项目组件库调整；低于 `0.7` 时先复核，不要仅凭视觉改成具体系统控件。

- 纵向/横向普通流：`VStack`、`HStack`。
- 层叠和局部绝对定位：`ZStack`、`overlay`、`background`。
- 长列表：`LazyVStack`、`LazyHStack`；仅在系统 List 行为符合原型时使用 `List`。
- Grid：按列定义选择 `Grid` 或 `LazyVGrid`。
- 固定底栏：优先 `safeAreaInset(edge:)`。
- sticky header：`LazyVStack(pinnedViews:)`。

不要给所有节点固定 `.frame(width:height:)`。优先保留容器、内容和比例约束。

Button、Toggle、TextField、Picker 等必须保留原生语义。自定义外观时清理默认 style 后重建视觉，不要退化成 `Text/Rectangle + onTapGesture`。

## Modifier 顺序

SwiftUI modifier 会逐层包裹 View。按 CSS 盒模型安排：

```text
content
→ typography/content behavior
→ content sizing
→ internal padding
→ background
→ clipping/mask
→ border overlay
→ shadow
→ transform/opacity
→ external layout and margin
→ interaction/accessibility
```

注意：

- 背景应覆盖 padding 时，先 `.padding` 再 `.background`。
- shadow 应作用于圆角外形时，在 shape/clip 之后添加，但不要被外层 `clipped()` 裁掉。
- 边框圆角必须与裁剪圆角使用同一组 corner radii。
- margin 通常由父 Stack spacing 或最外层 padding 表达。

## 文本

- `Text` 使用明确 font size 和 weight。
- 精确 line-height 时，根据实际字体 metrics 计算额外 line spacing；不要把 CSS line-height 直接传给 `.lineSpacing`。
- line clamp 使用 `.lineLimit`，截断使用 `.truncationMode`。
- 富文本优先使用 `AttributedString`；复杂 TextKit 排版可局部桥接 UIKit。
- 自定义字体必须确保已加入 target 并在 Info 配置中注册。

## 圆角、边框与阴影

- 四角一致使用 `RoundedRectangle`。
- 四角不同且最低系统版本支持时使用 `UnevenRoundedRectangle`；否则用自定义 Shape。
- 多重、inset 或带 spread 的阴影可用 Canvas、CALayer 或局部 UIKit bridge。
- 不要对有外部阴影的容器直接 `.clipped()`。

## Safe Area

- HTML 模拟的系统状态栏与 Home Indicator 应在 IR 阶段移除。
- 全屏背景可以 `.ignoresSafeArea()`，内容仍通过 safe area 或 `safeAreaInset` 避让。
- 自定义应用导航栏不要同时再显示 `NavigationStack` 默认导航栏。
- 底部自定义 Tab Bar 与 Home Indicator 间距只计算一次。
- `ScrollView` 使用父容器提供的完整尺寸；禁止先用 `GeometryReader` 计算 `proxy.size - safeAreaInsets`，也禁止把来源 HTML 的状态栏/Home Indicator 高度再次从 frame 中扣除。
- 系统管理页面使用 `safeAreaInset(edge:spacing:)` 放置自绘 top/bottom bar。`safeAreaInset` 已改变可用内容区域时，不得再给滚动内容添加同一栏位高度的 `.padding`。
- 全屏背景可以越过安全区，但背景越界不等于内容越界。仅在架构计划标记 `immersive-content` 时让主内容忽略 Safe Area，并由该页面一次性负责标题、按钮和底栏的可点击安全距离。
- 原生 `TabView`、NavigationStack toolbar 和系统 sheet 自己拥有相应安全区；升级为这些系统容器后，删除 HTML 模拟栏位及其 inset。

## 状态与导航

- 静态局部状态使用 `@State`。
- 复杂状态按项目现有 Observation、ObservableObject、TCA 等模式处理。
- 优先接入项目 Router/Coordinator；没有时才使用局部 `NavigationStack`、sheet 或 fullScreenCover。
- 不为每个页面新建全局 Router。

## UIKit 局部降级

以下情况允许通过 `UIViewRepresentable` 局部处理：

- `UIVisualEffectView` 毛玻璃
- TextKit 精确富文本
- 特殊 mask、shadowPath 或 `CATransform3D`
- 项目已有 UIKit 组件

降级范围必须局部，不能把整个页面桥接成 UIKit 或 WebView。

## 可测试性

对 Button、输入框、链接、可点击卡片和可切换控件设置 UI IR node ID：

```swift
.accessibilityIdentifier("home.search.button")
```

必要时为自定义点击容器补充 `.contentShape`，但点击区域应与 HTML 命中区域一致。
