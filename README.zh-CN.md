# sky-html-to-ios

[English](README.md) | **简体中文**

将可运行的移动端 HTML 高保真原型转换为可编译、可运行、可视觉验收的 iOS 原生页面。

当前仓库是第一个可用版本。它面向 Codex Agent 使用，通过浏览器提取 HTML 的真实渲染结果，建立中间 UI IR，再生成 SwiftUI 或 UIKit + Swift 代码。它不会使用整页截图或 `WKWebView` 冒充原生实现。

## 目录

- [它解决什么问题](#它解决什么问题)
- [核心目标](#核心目标)
- [支持的输入与输出](#支持的输入与输出)
- [主要能力](#主要能力)
- [六层原生架构](#六层原生架构)
- [控件与视图支持](#控件与视图支持)
- [导航、弹层与页面容器](#导航弹层与页面容器)
- [布局与 UI 适配](#布局与-ui-适配)
- [样式、文字与资源](#样式文字与资源)
- [交互与动画](#交互与动画)
- [工程识别与代码结构](#工程识别与代码结构)
- [视觉验证](#视觉验证)
- [运行环境](#运行环境)
- [安装与使用](#安装与使用)
- [HTML 编写建议](#html-编写建议)
- [产物说明](#产物说明)
- [支持边界](#支持边界)
- [质量状态](#质量状态)
- [常见问题](#常见问题)

## 它解决什么问题

高保真 HTML 原型通常已经包含页面布局、视觉样式、交互状态和多页面流程，但不能直接作为 iOS 原生代码使用。手工重写时容易出现：

- HTML 与 iOS 尺寸体系不一致；
- 图标、文字、间距和控件内部顺序发生偏差；
- Safe Area、导航栏、Tab Bar 和固定底栏重复计算；
- 横向列表变成整页横向滚动；
- 输入框、按钮和选择控件被错误降级成普通 View；
- 为了匹配截图堆叠固定 frame，破坏原生布局合理性；
- 只检查首屏，没有验证滚动末端、弹层和交互状态。

本 Skill 将这些问题拆成可追溯的转换和验证流程：

```text
HTML/CSS/JavaScript
        ↓
浏览器真实渲染与交互探测
        ↓
Render Tree / Route Graph / Interaction Graph
        ↓
UI IR
        ↓
原生架构、命名、资源和控件映射
        ↓
SwiftUI 或 UIKit + Swift
        ↓
Xcode 构建、Simulator 截图、几何与像素差异报告
```

## 核心目标

1. **真实原生实现**  
   生成 `View`、`UIControl`、原生输入控件、原生导航和原生页面容器，不使用整页 WebView。

2. **尽可能高的视觉还原度**  
   从浏览器计算样式、实际矩形、文字行盒、资源和状态中获取证据，不仅依靠 HTML 源码或截图猜测。

3. **合理的 iOS UI 架构**  
   页面、导航、容器、可复用 View、Cell、状态和资源按职责组织，不为单张效果图牺牲代码结构。

4. **尽量减少用户操作**  
   总控脚本负责工程发现、提取、生成、接入和验证；只有技术栈、工程归属或交互目标确实不明确时才要求确认。

5. **可验证、可纠偏**  
   每个 HTML 节点保留稳定 ID，能够从视觉差异追溯到 UI IR 和原生节点，支持局部修正。

## 支持的输入与输出

### 输入

- 本地可运行的 HTML 文件；
- HTML + CSS + JavaScript 高保真原型；
- 单页面或多页面原型；
- 响应式移动页面；
- 大展示板中的固定手机画板；
- 已完成并通过校验的 UI IR；
- 已有 Xcode 工程，或没有工程的空目录。

HTML 可以引用本地 CSS、JavaScript、图片、SVG 和字体。远程资源能否转换取决于访问权限、网络和资源许可。

### 输出语言与技术栈

| 输出 | 支持状态 | 说明 |
|---|---|---|
| SwiftUI + Swift | 支持 | 可生成页面、组件、状态、导航和资源载荷 |
| UIKit + Swift | 支持 | 使用 Auto Layout、UIKit 控件和 ViewController |
| SwiftUI/UIKit 混合工程 | 条件支持 | 按目标模块和现有工程结构选择，不按全仓库多数文件猜测 |
| Objective-C | 非默认 | 当前生成器不直接生成 Objective-C；仅可作为既有项目兼容扩展人工处理 |

默认最低系统版本是 iOS 16，可通过参数调整。使用较新 SDK API 前会检查 availability，并要求提供低版本降级路径。

## 主要能力

### 1. HTML 页面识别

- 识别明确的移动端画板；
- 识别响应式移动页面；
- 区分 PC 展示区域和移动页面区域；
- 对没有明确手机画板的响应式页面进行多宽度探测；
- 对无法确定页面根节点的输入停止自动转换，避免随意截取某个卡片；
- 识别固定、粘性、随内容滚动和滚动消失的页面区域。

### 2. 多页面与路由发现

- 扫描页面入口、页面容器和可见状态；
- 构建 `html-route-graph.json`；
- 构建 `interaction-state-graph.json`；
- 自动识别同一页面的菜单、sheet、alert、overlay、局部展开和 Cell 左滑等重复状态画板；
- 为状态画板建立独立 IR，并生成通用的新增、删除、替换、样式/内容和几何差分，不依赖案例名称枚举；
- 将增量原生子树合并到唯一 owner 页面，不额外生成业务 Screen 或 ViewController；
- 将差分执行为原生条件子树、节点替换、Presentation 或条目上下文操作；操作按钮可以嵌套在状态子树的任意层级；
- 保护交互源节点及其祖先，抑制证据不足的误删除，并通过 `state-delta-review.json` 输出需人工核对的归属和策略；
- 区分页面跳转、局部状态、弹层和外部链接；
- 保留 prerequisite interaction sequence；
- 对 owner 得分接近或低置信度路由给出警告，不擅自猜测；可用 `data-ios-state-owner` 明确归属。

### 3. 工程发现

- 检测 `.xcodeproj`、`.xcworkspace` 和 `Package.swift`；
- 检测目标模块使用 SwiftUI 还是 UIKit；
- 发现 Router、Coordinator、Navigation、Design System、Cell、View 和资源；
- 读取 target deployment target；
- 一个工程时自动选择；
- 多工程或多 target 无法唯一判断时要求明确指定；
- 没有工程时可创建 SwiftUI 或 UIKit App；
- 只有 Swift Package 时不会自动创建宿主 App，除非明确允许。

### 4. UI IR

UI IR 保存：

- 页面和目标 viewport；
- 节点树与稳定 ID；
- source selector 和浏览器矩形；
- Flex、Grid、文档流、absolute/fixed/sticky 等布局证据；
- computed style；
- 文字行数、baseline、line-height 和字体解析状态；
- 滚动轴和 scroll/client 尺寸；
- 原生控件建议和映射理由；
- 页面路由、局部状态和 presentation；
- 动画关键帧；
- 资源引用；
- SDK availability 和降级信息。

生成代码前必须通过 `validate_ui_ir.py`。存在未解决的关键交互时，正式生成会停止。

## 六层原生架构

代码生成不会把 DOM 直接翻译成扁平 View 树。`native-architecture-plan-1.1` 按顺序完成六层决策：

1. **Application Container**：现有 Router/Coordinator、`NavigationStack`、`UINavigationController`、`TabView` 或 `UITabBarController`。
2. **Screen Container**：SwiftUI `View`、`UIViewController`，或具备生命周期证据的自定义/Child Controller。
3. **Screen Regions**：系统/自定义顶部栏、内容区、底部栏、浮动区、Overlay 和 Presentation 所有权。
4. **Content Container**：静态 View/Stack、`ScrollView`/`UIScrollView`、`List`/`UITableView`、Lazy Grid/`UICollectionView` 或组合布局。
5. **Reusable Section and Item**：Section、Header、Footer、Item Template、Cell 策略、顺序和滚动轴所有权。
6. **Leaf Component**：最终的 `UIView`、`UIImageView`、`UILabel`、`UIButton`、输入控件、状态控件、Shape、Layer 或项目组件，并保留映射证据。

少量固定内容使用静态布局；长单列同构内容使用 Table/Lazy 容器；网格、轮播、数据表和异构 Section 使用 Collection。Table/Collection 自己拥有滚动轴，不再套同轴页面根 ScrollView。

每个 screen 还会生成强类型布局契约，保存子节点视觉顺序、轴向、对齐、分布、换行、间距、尺寸策略、宽高比和抗压缩证据。输入控件、显式/项目组件、特殊媒体和稳定业务控件会生成强类型叶子 View；普通文本、SVG 内部路径、装饰节点和自动编号 DOM 叶子继续由公共运行时处理，不会形成一节点一 Swift 文件。

## 控件与视图支持

支持状态分为三类：

- **直接生成**：核心生成器已经具有 SwiftUI 和 UIKit 实现；
- **映射/组合**：能够识别语义，可能根据样式选择系统控件、组合 View 或自定义控件；
- **项目接线**：能够规划原生方案，但需要权限、业务逻辑、系统框架或项目已有组件。

### 基础视图与布局

| HTML/语义 | SwiftUI | UIKit | 状态 |
|---|---|---|---|
| 普通容器 | `VStack` / `HStack` / `ZStack` | `UIView` / `UIStackView` | 直接生成 |
| Flex row/column | Stack + spacing/alignment | `UIStackView` + priority | 直接生成 |
| CSS Grid | `LazyVGrid` / Grid | `UICollectionView` 或组合布局 | 直接生成/映射 |
| Overlay/absolute | `ZStack` | Overlay container + constraints | 直接生成 |
| 滚动容器 | `ScrollView` | `UIScrollView` | 直接生成 |
| 横向轮播 | horizontal `ScrollView` | `UIScrollView`/`UICollectionView` | 直接生成/映射 |
| 分隔线 | `Divider` / Shape | `UIView` | 直接生成 |
| Spacer/弹性间距 | `Spacer` | hugging/constraint spacer | 直接生成 |
| 长列表 | `List` / Lazy stack | `UITableView`/`UICollectionView` | 映射/组合 |
| 分组列表 | Section/Lazy stack | table/collection sections | 映射/组合 |
| 多列数据表 | Grid/自定义布局 | compositional collection | 映射/组合 |

### 文本与图片

| 语义 | SwiftUI | UIKit | 状态 |
|---|---|---|---|
| 单行/多行文本 | `Text` | `UILabel` | 直接生成 |
| 标题 | `Text` + heading 语义 | `UILabel` | 直接生成 |
| 富文本 | `Text`/`AttributedString` | `NSAttributedString` | 直接生成 |
| 只读可选择文本 | selectable Text | readonly `UITextView` | 直接生成 |
| 图片 | `Image` | `UIImageView` | 直接生成 |
| SVG/图标 | Asset/Shape/SF Symbol | Asset/CAShapeLayer/SF Symbol | 直接生成/转换 |
| 复杂插画 | 矢量或位图资源 | Asset + `UIImageView` | 资源转换 |
| 视频 | `VideoPlayer` | `AVPlayerViewController` | 项目接线 |
| 音频 | 项目播放器 | AVFoundation | 项目接线 |
| 地图 | `Map` | `MKMapView` | 项目接线 |
| PDF/文件预览 | Quick Look 接入 | `QLPreviewController` | 项目接线 |

### 按钮与链接

- 普通按钮；
- 图标按钮；
- 图标 + 文字复合按钮；
- HTML `role=button`；
- 内部页面链接；
- 外部 URL；
- Menu item；
- Tab item；
- pressed、focused、disabled、selected 等视觉状态；
- target-action、SwiftUI action 和 accessibility identifier。

复合按钮不会被压成一段字符串。图标、角标、计数、文字和尾部信息会按浏览器最终几何顺序保留。

### 输入控件

| HTML/语义 | SwiftUI | UIKit | 状态 |
|---|---|---|---|
| text/email/url/tel | `TextField` | `UITextField` | 直接生成 |
| password | `SecureField` | secure `UITextField` | 直接生成 |
| search | `TextField`/`.searchable` | `UISearchTextField`/`UISearchController` | 直接生成/映射 |
| number | `TextField` + 数字键盘 | `UITextField` | 直接生成 |
| textarea | `TextEditor` | `UITextView` | 直接生成 |
| readonly textarea | readonly TextEditor 包装 | `UITextView(isEditable=false)` | 直接生成 |
| date/time/datetime | `DatePicker` | `UIDatePicker` | 直接生成 |
| file input | Button + importer 入口 | Document Picker 入口 | 入口直接生成，Picker 需接线 |

输入映射会处理：

- placeholder 和 placeholder 样式；
- value；
- editable/readonly/disabled；
- secure；
- 单行与多行；
- maxlength；
- keyboard type；
- text content type；
- return key/submit label；
- autofocus；
- autocapitalization；
- autocorrection；
- content inset；
- 键盘与滚动容器的单一避让所有权。

### 选择、数值和状态控件

| HTML/语义 | SwiftUI | UIKit | 状态 |
|---|---|---|---|
| switch/toggle | `Toggle` | `UISwitch` | 直接生成 |
| checkbox | 自定义 Toggle style | `UIButton`/`UIControl` | 直接生成 |
| radio | 自定义 option | `UIButton`/`UIControl` | 直接生成 |
| segmented control | segmented `Picker` | `UISegmentedControl` | 直接生成 |
| select/picker | `Picker`/`Menu` | `UIMenu`/Picker | 直接生成 |
| multi-select | 多选 Menu/List | Menu/list | 直接生成/组合 |
| slider | `Slider` | `UISlider` | 直接生成 |
| stepper | `Stepper` | `UIStepper` | 直接生成 |
| color picker | `ColorPicker` | `UIColorWell` | 直接生成 |
| progress/meter | `ProgressView` | `UIProgressView` | 直接生成 |
| loading | indeterminate `ProgressView` | `UIActivityIndicatorView` | 映射/组合 |
| disclosure | `DisclosureGroup`/自定义 | 自定义 `UIControl` | 映射/组合 |
| page indicator | page Tab/custom | `UIPageControl` | 映射/组合 |
| refresh | `.refreshable` | `UIRefreshControl` | 映射/项目接线 |

### 系统控制器与能力

以下能力可以被识别并规划，但不会凭 HTML 自动补齐权限和业务逻辑：

- `UIActivityViewController` 分享；
- `UIDocumentPickerViewController` 文件选择；
- `PHPickerViewController` 图片/视频选择；
- 相机与录像；
- `UIColorPickerViewController`；
- `UIFontPickerViewController`；
- 打印；
- Quick Look；
- `SFSafariViewController`；
- 联系人选择；
- 邮件和短信；
- 日历事件。

使用这些能力前必须核对隐私权限、entitlement、设备能力、系统框架和最低 iOS 版本。

## 导航、弹层与页面容器

### 页面跳转

- `push`；
- `pop`；
- `pop-to-root`；
- replace/set flow state；
- back；
- 外部链接；
- Tab 切换；
- Tab 重选后的 pop-to-root 或 scroll-to-top。

### Presentation

- sheet；
- 半屏 `UISheetPresentationController`；
- full-screen cover；
- modal；
- popover；
- overlay；
- alert；
- confirmation/action sheet；
- dismiss。

### 页面容器

| 场景 | SwiftUI | UIKit |
|---|---|---|
| 层级导航 | `NavigationStack` | `UINavigationController` |
| 主 Tab | `TabView` | `UITabBarController` |
| 主从结构 | `NavigationSplitView` | `UISplitViewController` |
| 翻页 | page-style `TabView` | `UIPageViewController` |
| 自定义复合页面 | 组合 View | custom container `UIViewController` |
| Child 页面 | 组合结构 | `addChild` 完整 containment 生命周期 |

页面容器不会按普通 `div` 逐节点生成。只有页面关系、生命周期和布局证据成立时才使用 Controller。

## 布局与 UI 适配

### 响应式 HTML

响应式页面在目标 viewport 直接提取，通常使用：

```text
1 CSS px = 1 iOS pt
```

转换后使用 SwiftUI Layout 或 Auto Layout，不执行运行时整页缩放。

### 固定手机画板

固定画板只做一次设计 token 归一化：

```text
designScale = targetWidthPt / sourceAppRootWidthCssPx
```

归一化后的间距、字号、圆角和资源尺寸作为基准，页面仍使用约束布局适配其他设备。

### 支持的布局证据

- leading/trailing/top/bottom；
- center；
- 固定尺寸与 intrinsic size；
- min/max width/height；
- aspect ratio；
- Flex grow/shrink/basis；
- gap；
- `space-between`；
- auto margin；
- Grid columns；
- row/column reverse；
- absolute/fixed/sticky；
- overflow 和真实 scroll/client 度量；
- viewport 固定栏；
- 内容驱动高度；
- 多行文字高度。

### Safe Area

- 每个 Screen 只有一个 Safe Area owner；
- 滚动容器使用父容器完整 bounds；
- 不从容器宽高重复减去 Safe Area；
- 系统管理时使用 SwiftUI Safe Area 或 `adjustedContentInset`；
- 自定义导航栏/底栏只追加自身高度一次；
- 状态栏、Home Indicator 和 HTML 模拟系统栏不会重复生成。

### 导航栏、Tab Bar 和底部操作栏

- 区分系统导航栏与 HTML 自绘导航；
- 区分固定底栏和随内容滚动的普通 footer；
- viewport 级栏位由父容器宽度决定；
- 不让栏内按钮的设计稿理想宽度反向撑开页面；
- `flex-grow` 子项可等分或按比例伸展；
- 小图标、角标和文字仍保持自身尺寸；
- 滚动消失、sticky、collapse、hide-on-scroll 通过真实滚动探测判断。

### 多尺寸验证

默认分析宽度：

- 320 pt；
- 375 pt；
- 393 pt；
- 430 pt。

原生验证必须使用真实 Simulator。工具会校验原始截图与声明 viewport 是否符合 1×、2× 或 3× Retina 倍率，防止把一台设备的截图缩放后伪装成另一尺寸。

## 样式、文字与资源

### 样式

可处理：

- 背景色；
- 线性和径向渐变；
- 圆角；
- 单边/整体边框；
- 阴影；
- opacity；
- transform；
- overflow clip；
- padding；
- margin；
- gap；
- z-index；
- object-fit/content mode；
- pseudo element；
- mask、filter、blur 的原生或降级策略；
- pressed/focused/disabled/selected 状态样式。

复杂 CSS 没有直接系统对应时，按顺序选择：

1. 项目已有组件；
2. 系统控件组合；
3. 自定义 SwiftUI View；
4. 自定义 `UIView`/`UIControl`；
5. `CALayer`/Core Graphics；
6. 合法的局部资源降级；
7. 标记 unsupported。

### 文字

- 保存字体候选、实际解析字体和 fallback 状态；
- 支持 system、serif、monospace、rounded 设计；
- 支持 100–900 字重；
- 保留 font style、字号、颜色和 letter spacing；
- 按原生字体度量校准 CSS line-height；
- 保存 first/last baseline；
- 校验中英文、数字、Emoji 和混合字号；
- 保留浏览器确认的多行与合法行断点；
- 多行文字使用可扩展最小高度，不写成固定高度；
- 按区域定位纵向累计漂移，不用整页 offset 掩盖问题。

Web Font 无法合法嵌入时会使用明确 fallback，并在报告中记录风险。

### 资源

- 图片；
- SVG；
- CSS background image；
- inline SVG；
- 本地字体；
- Asset Catalog；
- SF Symbols 候选；
- cover/contain；
- object position；
- repeat；
- placeholder 和资源转换 manifest。

只有视觉和语义都匹配时才会使用 SF Symbols 替换来源图标。许可不明确的远程资源不会被擅自下载或嵌入。

## 交互与动画

### 交互发现

- click/tap；
- change/input；
- submit；
- 展开/收起；
- 选择与取消；
- 删除/隐藏；
- 页面跳转；
- 弹层显示/关闭；
- 定时迁移；
- 多节点来源；
- prerequisite sequence；
- 动态内容变体。

低置信度交互会进入 overrides 草稿，需要确认后才能正式生成。

### 动画

- CSS transition；
- transform；
- opacity；
- color/background/border；
- scale/rotate/translate；
- keyframes；
- spring 的原生语义映射；
- loading/pulse 等循环动画；
- 页面和 presentation 转场；
- 手势驱动动画的规划与降级。

视觉验证可以在 0%、50%、100% 或关键帧采样。只有存在确定性原生采样钩子时，动画帧才作为 required gate。

同一业务页面的重复状态画板会逐状态独立截图。每个状态拥有自己的 HTML 根选择器、原生激活状态、几何节点和校验区域；iOS 端仍执行真实的点击、左滑或 Presentation 动作。

不自动向工程增加 Lottie、GSAP 等 Web 依赖。项目已有依赖或用户明确同意时才复用。

## 工程识别与代码结构

### 新工程

没有 Xcode 工程时：

1. 必须明确选择 SwiftUI 或 UIKit；
2. 创建原生 App 工程；
3. 使用默认 `Sky` 类型前缀；
4. 自动接入生成根页面；
5. 按验证模式构建或运行。

### 已有工程

- 识别目标模块技术栈；
- 复用项目稳定前缀；
- 发现已有组件、Router、Coordinator 和 Design System；
- 不覆盖 App、SceneDelegate、登录、Deep Link、Tab 或 Router；
- 生成源码关联到明确 target；
- 大型已有项目默认先询问是否 build/visual，不擅自启动。

### 默认生成目录

```text
<Target Source Root>/
└── Generated/
    └── HTMLToIOS/
        ├── Application/
        ├── Core/
        │   ├── Data/
        │   ├── Models/
        │   ├── Navigation/
        │   └── Runtime/
        ├── Home/
        │   ├── Models/
        │   ├── Screens/ 或 Controllers/
        │   ├── Sections/
        │   ├── Cells/
        │   └── Views/
        ├── List/
        │   ├── Models/
        │   ├── Screens/ 或 Controllers/
        │   ├── Sections/
        │   ├── Cells/
        │   └── Views/
        └── Resources/
            ├── Assets/
            └── Payload/
```

页面按业务模块分目录，并拥有自己的强类型 UIContract、Section、Cell 和 View。六层架构计划会进入真实 Swift 源码：UIKit 注册生成的 `UITableViewCell`/`UICollectionViewCell` 子类，SwiftUI 将复用节点路由到强类型 Item View。Navigation、Tab 和跨页面公共运行时进入 `Core`；资源进入 `Resources`。不会把所有文件平铺在同一个目录。

### 增量更新

- `.html-to-ios-generation.json` 记录生成所有权；
- 未修改的生成文件可安全更新；
- 用户修改过的生成文件不会被静默覆盖；
- 新候选进入 conflict 目录；
- 保留 source node → UI IR → native view 追溯关系。

## 视觉验证

### 验证链路

1. 捕获 HTML required states；
2. 生成隔离的 XCUITest target；
3. 执行 iOS actions；
4. 校验目标出现、消失或路由到达；
5. 导出 Simulator 截图；
6. 导出节点几何；
7. 统一到逻辑 viewport；
8. 执行像素、区域、文字边缘和几何对比；
9. 生成 comparison、overlay、heatmap 和 regions；
10. 多模态能力可用时进行人工视觉走查。

### 验证内容

- 精确图片尺寸；
- 全局 mismatch；
- mean absolute difference；
- 导航栏/底栏等 critical region；
- text edge mismatch；
- top/middle/bottom 纵向漂移；
- 节点宽高和位置；
- 关键节点几何采集率；
- 根页面横向溢出；
- 横向列表轴向所有权；
- 多设备响应式布局；
- required interaction state 是否缺失；
- presentation 是否实际出现。

### 几何采集

几何采集只在 UI Test 启动参数下扩展 accessibility tree，正常 App 的辅助功能结构不受影响。

报告区分：

- 全部可见源节点采集率；
- validation region 关键节点采集率；
- 横向滚动合法拥有的越界；
- 根页面非预期越界；
- 容器 union frame 和装饰节点。

### 验证模式

| 模式 | 行为 |
|---|---|
| `auto` | 新建工程执行 visual；已有工程停下询问 |
| `ask` | 只生成和接入，等待确认 |
| `build` | 只执行构建 |
| `visual` | 构建、截图和视觉门禁 |
| `none` | 明确跳过验证，不得声称已编译或高保真通过 |

## 运行环境

推荐环境：

- macOS；
- Xcode 和 iOS Simulator；
- Python 3.9+；
- Ruby；
- Node.js；
- Playwright + Chromium；
- Pillow；
- 可写的 Xcode 工程目录；
- 本地 HTML 可以正常运行。

Skill 会优先使用显式 `--node`、`CODEX_NODE`、PATH 或 Codex bundled Node runtime。Xcode 视觉验证只适用于 macOS。

## 安装与使用

### 作为 Codex Skill 安装

将仓库安装到当前 Codex 环境的 Skills 目录。常见位置是：

```bash
git clone https://github.com/skyzizhu/sky-html-to-ios.git \
  ~/.codex/skills/sky-html-to-ios
```

不同 Codex 环境的 Skills 根目录可能不同，应以当前环境配置为准。

在 Codex 中可以直接提出：

```text
使用 $sky-html-to-ios，把当前目录中的 mobile-prototype.html
转换为 SwiftUI 原生页面。
```

也可以指定：

```text
使用 $sky-html-to-ios，把 prototype.html 接入当前 UIKit 工程，
先生成，不要自动运行。
```

### 总控命令

Agent 应将 Skill 根目录解析为绝对路径：

```bash
SKILL_ROOT="$HOME/.codex/skills/sky-html-to-ios"
```

在用户工程目录运行：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --html /absolute/path/prototype.html \
  --ui-stack swiftui \
  --verification-mode visual
```

UIKit：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --html /absolute/path/prototype.html \
  --ui-stack uikit \
  --verification-mode visual
```

已有工程只生成并等待确认：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --html /absolute/path/prototype.html \
  --project /absolute/path/App.xcodeproj \
  --target App \
  --verification-mode ask
```

使用已有 UI IR：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --ir page1-ui-ir.json \
  --ir page2-ui-ir.json \
  --verification-mode build
```

仅检查决策，不写工程：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --html /absolute/path/prototype.html \
  --dry-run
```

查看所有参数：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" --help
```

## HTML 编写建议

Skill 可以处理普通 HTML，但结构和语义越清楚，自动还原越稳定。

推荐：

- 使用正确的 `button`、`input`、`textarea`、`select`、`nav`；
- 使用 `role` 和 `aria-*` 表达自定义控件语义；
- 每个移动页面有稳定根节点；
- 多页面具有可识别的显示/隐藏或路由关系；
- 按钮和交互节点具有稳定 id/class；
- 明确 viewport meta；
- 避免同一个选择器同时指向多个不同业务控件；
- CSS 动画有明确触发状态；
- 本地资源路径可访问；
- 页面初始状态可重复；
- 原型脚本不要依赖无法提供的后端环境；
- 自定义页面区域可使用 `data-ios-*` 契约消除歧义。

不建议：

- 只提供一张静态截图；
- 将移动页面隐藏在不可识别的 PC 展示层中；
- 所有交互都写成无目标的匿名 click；
- 使用随机 DOM 结构或每次加载变化的 ID；
- 用 Canvas/WebGL 绘制整个应用；
- 依赖跨域且无法访问的远程资源；
- 修改 HTML 去迎合某一次 iOS 截图。

详细契约见 `references/html-authoring-contract.md`。

## 产物说明

总控报告默认位于：

```text
<workspace>/.html-to-ios/
```

主要产物：

| 文件 | 内容 |
|---|---|
| `orchestration-report.json` | 总控状态、步骤和质量门禁 |
| `project-generation-decision.json` | 工程、技术栈和验证决策 |
| `html-route-graph.json` | 多页面路由图 |
| `interaction-state-graph.json` | 交互状态图 |
| `ios-project-report.json` | iOS 工程扫描结果 |
| `ios-component-index.json` | 可复用组件索引 |
| `ios-sdk-report.json` | SDK API 和 availability |
| `native-naming-plan.json` | 文件名、类型名前缀和冲突 |
| `native-architecture-plan.json` | Controller、导航、滚动、Safe Area 和 presentation |
| `screens/<id>/render-tree.json` | 浏览器渲染树 |
| `screens/<id>/ui-ir.json` | 页面 UI IR |
| `screens/<id>/text-calibration.json` | 文字校准 |
| `screens/<id>/responsive-layout.json` | 响应式分析 |
| `screens/<id>/scroll-region-behavior.json` | 滚动区域行为 |
| `screens/<id>/visual-state-manifest.json` | 状态截图契约 |
| `screens/<id>/state-delta-review.json` | 状态归属、策略置信度、受保护节点和被抑制删除 |
| `screens/<id>/visual-states/html/` | HTML 状态截图 |
| `screens/<id>/visual-states/ios/` | iOS 状态截图与几何 |
| `screens/<id>/visual-review/` | diff、overlay、heatmap 和报告 |

## 支持边界

### 不自动补全

- API 请求和真实业务数据；
- 登录、认证和权限流程；
- 支付；
- 服务端校验；
- 数据库；
- 分页接口；
- 推送业务；
- 埋点平台业务配置；
- 未在 HTML 中出现的页面；
- 未定义的错误处理；
- 真实相机、联系人、邮件等权限配置。

### 默认不支持

- 仅凭截图生成完整可维护页面；
- 整页 WKWebView 转换；
- 整页或功能卡片截图拼接；
- WebGL 应用；
- 复杂 Canvas 应用；
- 浏览器插件；
- 不可解析的第三方嵌入页面；
- DRM/复杂播放器业务；
- 复杂游戏引擎；
- 自动生成 Objective-C；
- 宣称任意 HTML 都能自动达到 100% 像素一致。

### 高风险输入

- 大量运行时生成 DOM；
- Shadow DOM 封闭组件；
- 跨域 iframe；
- CSS Houdini、复杂 shader/filter；
- 依赖 hover 才能操作的移动界面；
- 无法稳定复现的随机动画；
- 页面根和目标 breakpoint 不明确；
- 字体文件缺失；
- 远程资源不可访问；
- 现有工程存在多个同名 target/scheme。

这些情况会要求确认、局部降级或标记 unsupported。

## 质量状态

第一个可用版本已经完成：

- SwiftUI 真实工程构建；
- UIKit 真实工程构建；
- 当前开发版审计通过 107 项自动化测试；
- UI IR、工程决策、命名和生成器测试；
- 多页面和交互状态测试；
- 视觉差异和节点几何测试；
- 真实 Simulator 多尺寸矩阵；
- Skill 结构校验；
- Python/Ruby 语法检查。

这里的“可用”表示流程能够生成、接入、构建和验证原生页面，并具备系统化纠偏能力，不表示任何复杂 HTML 都能一次达到 100% 像素一致。复杂页面通常仍需要根据 review bundle 做少量节点级修正。

## 常见问题

### 1. 为什么新工程必须选择 SwiftUI 或 UIKit？

新工程没有历史架构可推断。静默默认可能让整个输出方向错误，因此必须明确选择。

### 2. 为什么已有工程不会自动运行？

已有工程可能很大，也可能依赖登录、环境变量和后端。默认只生成并等待确认，避免浪费时间或破坏现有流程。

### 3. 为什么不直接按 HTML 宽高生成固定 frame？

固定 frame 可以暂时接近一张截图，但无法适配设备、文字和 Safe Area。Skill 使用浏览器尺寸作为约束证据，再转换成 SwiftUI Layout 或 Auto Layout。

### 4. 为什么像素对比没有达到 100%？

浏览器和 iOS 在字体栅格、阴影、渐变、控件实现和抗锯齿上存在差异。报告会把差异定位到区域和节点，帮助继续校准；不会把失败结果包装成通过。

### 5. HTML 没有移动画板怎么办？

如果页面在移动宽度下有稳定响应式布局，Skill 会按 320/375/393/430 等宽度探测。如果移动根节点和 breakpoint 仍无法确定，会要求确认。

### 6. HTML 控件系统没有直接对应组件怎么办？

优先复用项目组件，其次组合系统控件，再使用自定义 View/UIControl/CALayer。仍无法合理表达时标记 unsupported，不使用 WebView 或截图伪装。

### 7. 是否支持修改已有生成结果？

支持。生成清单会检测人工修改，避免静默覆盖。视觉反馈应回到 Skill 规则或 UI IR 做通用修正，而不是只在某个测试 Controller 中堆补丁。

### 8. 能否保证根页面不会左右滚动？

页面主滚动轴和嵌套横向滚动轴分别建模。响应式矩阵会检测不属于横向滚动容器的越界节点，但仍需对复杂自定义布局执行真实设备走查。

### 9. 能否使用项目已有组件？

可以。Skill 会扫描 SwiftUI View、UIKit View/Controller/Cell、Router、Coordinator、Design Token 和资源，优先沿用已有实现。

### 10. 如何判断转换完成？

至少检查：

- UI IR 通过；
- 无未解决关键交互；
- 生成文件已关联 target；
- Xcode 构建通过；
- required iOS states 无缺失；
- 响应式和滚动轴门禁通过；
- visual review 没有未说明的关键失败；
- 所有降级和待确认项已报告。

## 仓库

- GitHub: <https://github.com/skyzizhu/sky-html-to-ios>
- Skill 名称：`sky-html-to-ios`
- 生成语言：Swift
- 支持 UI 栈：SwiftUI、UIKit
