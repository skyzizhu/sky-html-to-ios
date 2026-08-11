---
name: sky-html-to-ios
description: 将可运行的移动端 HTML/CSS/JavaScript 高保真原型转换为可编译、可维护的 iOS 原生页面。适用于把网页原型还原为 SwiftUI 或 Swift UIKit，接入现有 Xcode 工程，并复刻布局、样式、资源、导航与基础交互。核心流程必须通过浏览器读取真实计算样式、几何、DOM 与脚本行为，生成 UI IR 和原生代码；构建是基础验证，截图、像素对比、多模态走查和自动纠偏仅是可选的验收兜底，不得作为转换能力或 Agent 多模态能力的前提。不要用于 WKWebView 包装或仅凭截图生成页面。
---

# HTML to iOS Native

将可运行的移动端 HTML/CSS/JavaScript 原型转换为真实 iOS 原生页面。核心任务是结构化读取网页布局、样式和行为并生成合理的原生 UI；视觉验证是生成后的可选兜底，不是输入方式或转换引擎。

## 核心原则

1. 使用浏览器实际渲染结果，不凭源码或肉眼猜测最终尺寸。
2. 先生成 UI IR，再生成 SwiftUI 或 UIKit 代码，保持来源可追溯。
3. 遵循现有项目架构、组件和依赖；不要强制引入 MVVM、Router 或第三方库。
4. 使用真实原生 View。禁止用整页截图、整块截图或 `WKWebView` 冒充原生实现。
5. 保留可合法访问的本地图片、图标和字体资源；资源缺失时才使用明确标记的占位内容。
6. 必须验证编译结果。截图、像素对比和纠偏只在用户要求 `visual` 验证或 Agent/环境适合时执行；缺少多模态能力不得阻断提取、UI IR、代码生成或构建。
7. 纠偏时只修改产生差异的节点或组件，避免重写无关页面。
8. 响应式页面必须从多宽度计算结果推断原生约束；禁止用运行时整页缩放代替 Auto Layout。
9. 视觉复刻不得破坏原生 UI 架构。页面、容器、可复用 View/Cell、状态和路由按职责分层；状态变化通过数据模型驱动 Stack/Grid/Auto Layout，不在业务页面散落截图坐标或逐状态 frame。
10. 同一页面的重复画板必须先归并为 owner screen 的状态。去重依据是页面骨架、节点语义、文本、层级、几何与可选 `data-ios-state-key`，不能依赖 menu、sheet、Cell 左滑等案例枚举。状态画板必须生成通用 insert/remove/replace/update 差分并进入 owner 原生树，不得各自生成业务页面；归属存在歧义时使用 `data-ios-state-owner` 或旁路契约确认。
11. 状态差分必须由通用策略执行器落到原生结构：页面内变化使用条件子树或替换节点，覆盖层使用 presentation，条目操作使用上下文操作。不得只把状态记录在报告中而不生成可触发的原生行为。
12. 每个状态必须有独立的结构、交互与 owner 契约。只有进入 `visual` 验证时才要求独立 HTML/iOS 截图、几何区域和门禁结论。低置信度归属、被抑制的删除或目标不明确的上下文操作必须进入 `state-delta-review.json`，不得静默猜测。
13. 控件选择必须执行系统优先、视觉适配门禁：先用 Apple 系统控件及官方配置；系统本体视觉不足时保留系统控件并增加原生包装层；只有语义、行为或视觉能力确有阻断证据时才生成组合控件或自定义 `UIControl`/View。
14. `visual` 验证失败时生成节点级 `visual-correction-plan.json`。纠偏只允许修改 UI IR 和派生契约，禁止直接给生成后的 Swift 源码追加截图专用补丁；未运行视觉验收不等于核心转换失败。
15. 截图之前必须完成确定性结构验收。浏览器来源覆盖、parent-child 归属、视觉顺序、布局关系、scroll owner、原生叶子组件和 screen region 所有权必须进入 `layout-relation-graph.json` 与 `structural-fidelity-report.json`；结构门禁失败时不得生成 Swift。
16. Swift 生成后、Xcode target 接入前必须完成原生消费验收。生成器必须输出 `native-structure-manifest.json`，独立门禁必须输出通过的 `native-structure-validation.json`，证明实际 Swift/Payload 消费了关系图；禁止只验证计划而不验证生成结果。
17. 六层架构到 Swift 之间必须只有一份可执行布局契约。总控生成并校验 `native-layout-plan.json`，SwiftUI 与 UIKit 必须共同消费其中的容器顺序、尺寸策略、盒模型和复合控件槽位，不得各自重新猜测。
18. 布局契约必须区分 stack、wrapping stack、grid 与 positioned overlay，并保存 Grid 轨道、独立 row/column gap、Flex reverse/wrap、定位 containing block 和状态布局增量。百分比、`calc()`、viewport/font 相对单位必须保持可解析表达式；不得截取其中一个数字冒充固定尺寸。
19. 内容容器遵循最小充分原则：少量固定内容使用 Stack/Grid，长单列同构内容使用 Table/Lazy 容器，多列、横向、数据表或异构 Section 使用 Collection/组合布局。集合容器只有与页面主轴一致且在直接内容层中占主导时才能替换根 Scroll；嵌套横向 Collection 只拥有横轴，禁止夺走页面纵轴或产生同轴双重滚动。
20. Table/Collection 必须具有可执行的 Section 与 item sizing 契约。Header/Footer 从可复用 item 中分离；只有 `position: sticky` 或等价行为证据才能生成 pinned supplementary view。内容驱动的 Table 行使用自动高度，显式固定高度才生成固定 row/item；横向 item 必须保留来源宽度、宽高比与抗压缩语义。无法映射为 Screen Region 或原生 supplementary 的 sticky 节点必须在生成前阻断。
21. 初始隐藏或透明的节点只要拥有可执行 motion/keyframes，就必须保留并绑定动画。关键帧需保存真实 sample offset、translate、scale、rotation 与 opacity；`calc()` 中带空格的正负位移不得丢失符号。
22. SVG 内部节点、富文本子节点、系统选择标记等被原生 owner 合并时，必须在生成后清单中记录 strategy、owner node ID、source node IDs 和 native primitive。只捕获 SVG 当前 computed state 时必须标记交互降级，不能声称原 transition/keyframes 已实现。
23. 浏览器绘制顺序必须成为显式契约。提取阶段记录 DOM source order、`::before`/`::after` phase、stacking context owner、paint group 与 z-level；普通 Stack/Grid 继续按视觉布局顺序测量，发生重叠的 Overlay/ZStack 必须按 `paintOrderNodeIds` 绘制，SwiftUI 与 UIKit 不得各自按 DOM 或 z-index 单字段重新猜测。
24. 圆角与后代裁剪必须分离。`border-radius` 只决定背景、边框和自身内容的形状；只有 `overflow:hidden/clip`、clip-path、mask 或等价明确证据才能裁剪子树。`overflow:visible` 的伪元素、阴影和越界装饰必须允许绘制到圆角边界之外。
25. 滚动与区域归属必须形成独立的 `scroll-and-attachment-plan.json`。每个 Scroll、固定/粘性/随内容滚动区域、Safe Area 和键盘避让只能有一个 owner；计划和生成后的消费清单都必须通过确定性门禁，不能依赖截图发现整页双轴滚动。
26. 系统控件覆盖必须以当前本机 iPhoneOS SDK 审计，不使用永久硬编码的“最新控件”结论。公开 `UIControl` 子类及页面常用 UIKit 输入/选择/状态控件必须贯通识别、UI IR、架构、SwiftUI/UIKit 执行和编译测试；SwiftUI 对无直接等价物可使用局部 `UIViewRepresentable`，但禁止整页 UIKit/WebView 包装。
27. 系统控件内部视觉必须形成独立的 `native-control-configuration-plan.json`。浏览器实测的 `accent-color`、`appearance`、normal、highlighted/pressed、editing/focused、checked/selected、disabled/loading，以及 Switch 轨道/滑块、Slider/Progress 轨道与填充、Segmented 选中项、PageControl 当前页、content inset、item spacing、preferred style 和固有尺寸都要由 SwiftUI/UIKit 共同消费，并在生成后清单中证明；不得由两套生成器各自猜测。
28. 版本与设备兼容性必须形成 `native-api-fallback-plan.json` 和 `compatibility-matrix.json`。计划同时绑定本机 SDK、target deployment、SwiftUI/UIKit、来源实测宽度、Size Class 与 iPad 待验证项；低于运行时基线、必需能力无降级实现或生成器不认识降级策略时必须在代码生成前停止。`source-analyzed`、`pending-runtime-validation` 和视觉验收通过不得混为同一状态。
29. 多设备运行验收必须使用真实 Simulator App 窗口形成 `ios-runtime-compatibility-report.json`。手机 320/375/393/430pt、横屏、iPad Split View 和 regular width 分别作为 profile；截图像素尺寸、`XCUIApplication.frame`、方向、Size Class 推断和无主横向溢出共同作为证据。不得把一台设备截图缩放成其他尺寸，也不得把全屏 iPad 截图声明为 Split View 已通过。
30. 叶子节点和复合控件必须由 `native-layout-plan.json` 中统一的内容几何契约驱动。图标、图片、紧凑标签、数量徽标和单行文字要保存来源宽高、fixed/intrinsic/flexible/parent-relative 模式、宽高比、单行、抗压缩、媒体适配和对齐；复合控件还必须保存每个槽位的实测间距与弹性间距。SwiftUI/UIKit 不得分别重新推断这些属性。
31. 首次生成的视觉外观必须由计算样式直接形成可执行契约，不能等待截图纠偏。四角圆角必须分别保存水平/垂直半径，四边边框必须分别保存宽度、颜色和线型；禁止用最大值或单一代表值统一化。CSS 圆角使用圆弧/椭圆几何，不得默认替换为 Apple continuous corner；背景、透明度与后代裁剪也必须由同一节点契约驱动。截图和多模态只用于生成后的可选验收。
32. 六层只负责原生节点的所有权与包含关系，不新增“视觉属性层”。全局应用壳由 `native-application-plan.json` 唯一确定；布局、外观、系统控件配置、Presentation、交互与动画分别通过横向契约附着到六层节点，禁止同一事实在多个计划中各自推断。
33. 页面提取必须区分 Visual Root 与 Content Root。Visual Root 提供主题背景、祖先状态、共享导航/底栏和 viewport 几何；Content Root 决定当前业务页面及滚动内容。两者不得被压成一个 selector，也不得因为只截取 Content Root 而丢失外层深色主题、固定操作栏或全局浮层。
34. absolute/fixed/sticky 的定位参照必须来自浏览器事实。提取器记录 `offsetParent`、最近 scroll ancestor 及其矩形，UI IR 转换为稳定 Node ID，Native Layout Plan 再决定原生 owner；生成器禁止继续使用 DOM parent、零尺寸包装层或屏幕中心作为兜底参照。
35. 线性容器除整体 gap 外必须逐子项保存浏览器实测 `gapBeforePt`。UIKit/SwiftUI 以该契约还原不等距分组，并清除已经被间距契约消费的相邻主轴 margin；文字和动态内容继续使用 intrinsic/min-height，禁止为了消除纵向漂移固定整页高度。
36. 系统 Safe Area 只能应用一次。HTML 中模拟的状态栏/Home Indicator 被移除后，不得再把来源状态栏高度叠加到系统 safeArea、scroll contentInset 或容器 frame；来源 chrome 高度仅在明确的 immersive/custom chrome 所有权下使用。
37. 单行文字默认保持来源字号、字重和 tracking。HTML 的 nowrap/overflow 映射为原生单行、裁剪或省略，不得用 `minimumScaleFactor`/`adjustsFontSizeToFitWidth` 静默缩小字体。动画必须从页面出现后的零相位计时，不得用系统绝对时间随机进入中间关键帧。
38. 系统导航栏显隐必须由 Application Container 在创建、push 和 replace 时立即应用，不能只等待页面 `viewWillAppear` 或 SwiftUI 子视图生命周期。页面声明隐藏导航栏时，首帧不得短暂或永久出现系统标题、返回按钮和额外顶部高度。
39. CSS `background-clip:text` 或透明前景配合渐变背景属于文字填充，不属于 View 背景。原生不能把它绘制成文字外接矩形；若当前栈无法精确实现渐变字形遮罩，允许稳定降级为首个渐变色的文字，但不得生成色块或丢失文字。
40. 控件 focused/pressed/checked 状态采样不得污染页面基准状态。提取器在采样前记录 window 与全部可滚动祖先的 offset，采样后必须恢复并校验；恢复失败时禁止继续输出基线截图。
41. Screen Content Root 的响应式宽度由 Screen Container 持有，不能把浏览器单次采样宽度继续生成为根节点 fixed-width。非滚动静态内容以 Safe Area 顶部为起点并保留 intrinsic/measured height，底部只设上界；禁止同时固定根高度又把 top/bottom 强制等距钉满屏幕。滚动容器仍使用父容器完整 bounds 和系统自动 inset。
42. UIKit 的页面级 typed wrapper、模块 ContentView 和 section wrapper 必须显式关闭 autoresizing-mask constraints，再交给 Screen Container 约束。生成后首屏根 wrapper 的运行 frame 不得为零；禁止让内部固定尺寸子树在 `0×0` 根 View 外部依靠 overflow 偶然显示。
43. 子项固定尺寸只来自 authored 固定长度或明确的紧凑视觉契约。computed style 中解析后的 px 只是单次测量；authored `width:100%`、百分比和相对父容器的 `calc()` 必须生成填充/比例约束，禁止再叠加固定宽度与父级 leading/trailing 约束。
44. UI IR/Native Layout Plan 中带 `Pt` 的 border/content box、min/max 和槽位几何已经完成设计画板归一化，Payload 不得再次乘 `designScale`。百分比子项以父内容框为参照；UIKit 父 Stack 用 layout margins 表达 padding 时必须约束到 `layoutMarginsGuide`。
45. 一个语义文本节点包含普通 Text Node 与非交互 inline span 时，必须合成为同一个 `NSAttributedString`/`AttributedString`，按浏览器 content run 顺序保留颜色、背景、字体和行高；不能把 inline span 降成纵向 Stack 子 View，也不能让父级富文本在匿名 Text Item 中重复渲染。纵向 Stack 还必须消费父级 center/end 对齐，避免 intrinsic 胶囊和紧凑标签被 `.fill` 拉满。
46. 控件选择分为语义候选、上下文角色、系统候选、几何适配和最终决策。几何适配最多进行两轮有界解析；保留系统语义的 wrapper 不得改变控件的交互、状态机和无障碍所有权。
47. `native-appearance-plan.json` 负责节点外观，文字占位尺寸仍由 `native-layout-plan.json` 负责；`native-interaction-motion-plan.json` 负责每个动作和动画的唯一 owner 与 executor。生成器必须消费这些计划并在结构清单中记录哈希与消费状态。
48. 总控先原子化写入 canonical orchestration report，再向 stdout 输出摘要。stdout/日志消费者提前关闭导致的 `BrokenPipeError` 只能停止终端输出，禁止把已通过的转换和构建状态覆盖成 failed。
49. CSS padding 在原生层只能有一个 owner：普通容器由 Stack layout margins 消费；复合 `UIControl` 若 wrapper 已用边缘约束 inset 内容 Stack，内层 Stack 不得再次应用同一 padding。生成后出现图标/文字宽高被压成零属于硬失败。
50. 节点外观与系统控件状态外观必须使用独立契约和变量。控件 tint/track/thumb 等配置不得覆盖节点的背景、渐变、圆角、边框、阴影或 `clipsDescendants`；渐变控件有圆角且来源 overflow hidden/clip 时，渐变层必须随节点圆角裁剪。
51. 圆角背景/渐变与外阴影分层渲染：宿主 CALayer 保留阴影可见，背景资源或 `CAGradientLayer` 自身应用统一 corner radius 或逐角 mask。不得通过关闭全部裁剪留下矩形渐变，也不得裁剪宿主层吞掉外阴影。
52. 控件 normal/pressed/selected/disabled 状态新建的渐变层必须继承节点基础渐变的圆角和逐角 mask；状态切换不得用矩形状态层覆盖已经正确的圆角背景。
53. CSS 四角半径在原生 lowering 前必须执行重叠圆角缩减算法。`999px` 等胶囊写法按最终盒子宽高归一化，不能把超大半径原值直接交给 `CALayer`。
54. 父级系统控件的状态前景色只归父控件所有，不得递归覆盖具有独立计算色或富文本 run 的子节点。UIKit 渐变背景文字使用背景宿主层，富文本颜色在样式与状态安装完成后按节点所有权恢复。
55. 横向复合内容的单行判定同时使用浏览器实测槽位高度、字体行高和 `white-space`。单行文字保留 intrinsic size 与抗压缩优先级，不把一次测量宽度写成硬约束；`normal/flex-start` 的剩余宽度由尾部弹性槽吸收，不能拉伸文字并把相邻徽标推到末端。
56. 固定画板按 cover 归一化时必须保存 Visual Root 的上下/左右裁切量。viewport-fixed 顶栏、底栏和浮层按对应裁切量补偿锚点；视觉验证区域也必须使用 Visual Root 浏览器坐标，不能使用 Content Root 相对坐标。
57. 文字视觉行必须按字符 Range 的垂直重叠归并。光标、下划线、徽标背景和不同字号 inline fragment 不得单独制造新行；只有固定画板且逐行文字与完整渲染文本校验一致时，才把浏览器软换行固化到原生富文本，响应式来源继续由 Auto Layout 在运行宽度重排。
58. `native-layout-plan.json` 的间距字段 `gapPt`、`rowGapPt`、`columnGapPt` 和 `gapBeforePt` 必须统一为目标 iOS 点值：浏览器实测矩形差值直接使用，来源 CSS px 在计划层只换算一次。生成器及结构消费门禁必须按原值消费，禁止再次乘 `designScale`，避免纵向和横向间距逐段累计漂移。
59. 已成功提取并接入的来源 SVG/图片拥有图标外观的唯一所有权，此时不得同时生成近似 SF Symbol 作为静默运行时替代。SF Symbol 只用于无来源资源且轮廓、粗细、填充和语义通过适配门禁的节点；源资源接入失败必须显式失败或降级报告。
60. NavigationBar 与 App 级 TabBar 必须执行 system-first 视觉适配门禁。页面顶部出现返回按钮或页面级操作按钮时，默认直接映射为系统 NavigationBar 的 back/leading/trailing/primary item；标准标题、返回、少量 toolbar item 和稳定主 Tab 默认映射为 `NavigationStack`/`UINavigationController` 与 `TabView`/`UITabBarController`。HTML 的自绘声明只描述来源实现，不能单独迫使原生端自绘。只有页面没有导航语义，或搜索、多行复杂内容、异形/品牌背景、系统栏无法表达的布局及显式 `data-ios-force-custom-navigation|tab-bar=true` 才允许隐藏或自定义。系统容器接管后，来源栏及全部后代必须从页面内容树剥离，并在原生消费清单中记录 `system-chrome-merged`，禁止双栏和重复 Safe Area。
61. 对齐与间距必须先归一化再由两套原生栈共同消费。`flex-start/start/left/top`、`flex-end/end/right/bottom`、center、baseline、stretch 按容器轴映射，文本 `text-align` 不得覆盖父容器交叉轴对齐。每个相邻子项保存 signed 实测 border-box gap、CSS gap、前项尾 margin、当前项首 margin、残差和 fixed/flexible/overlap 模式；实测间距是首次生成的权威值，margin 只消费一次，`space-between/around/evenly` 不得退化成等量固定空白 View。
62. 系统 NavigationBar 接管后，系统 Safe Area 与状态栏 inset 是唯一顶部系统空间；不得继续叠加 HTML 模拟状态栏高度。来源导航栏底边到首个业务内容节点的实测间距可以保留为 `systemNavigationContentSpacing`，但不得包含已由系统栏消费的高度。宽容器填充、轴向对齐和 authored `aspect-ratio` 必须进入 UIKit/SwiftUI 共同的运行时能力门禁。
63. 节点 `widthFraction` 必须相对直接父容器的内容宽度计算，不得相对整屏计算；接近父宽的普通流节点映射为父级填充，absolute/fixed、Overlay、横向滚动 item、动画节点和紧凑文字继续保留各自尺寸语义。只有一个直接文本/图标槽且无子 View 的 CSS Grid 是单槽对齐容器，SwiftUI 使用填满父级的 Stack/ZStack，禁止为了 `display:grid` 生成会按内容收缩的 Lazy Grid。
64. 固定 border-box 只约束外框，不代表内部内容居中。盒内水平/垂直位置必须按容器 axis 联合消费 `justify-content`、`align-items`、Grid `justify-items`、文字 `text-align` 与复合槽位角色；可伸展文字槽必须在自身分配宽度内保持来源对齐。Screen Root 的实测高度只用于几何验收，Payload 必须清除根 `fixedHeight`，由内容 intrinsic height、Scroll owner 与 Screen Container 上界共同决定，禁止因 SwiftUI `.frame` 默认居中造成整页纵向漂移。
65. JavaScript/CSS 状态切换造成的 width/height、滚动轴或内容变化必须从隔离浏览器 probe 的 before/after 证据生成可逆 State Variant，不能只切换无视觉消费方的布尔标记。若页面 Content Root 位于被裁出原生树的 HTML Scroll 祖先内，Scroll ownership 必须转交给 Screen Root；展开后的屏外控件由同一原生 Scroll owner 负责到达。Sheet/Popover/Overlay 的原生宿主必须暴露来源面板节点 ID，并保留内部状态 ID 防重入，使交互、无障碍与视觉验收共享同一身份契约。
66. HTML 中带点击行为的 `span`、`div`、图片或组合文本即使视觉语义不是按钮，也必须由原生事件宿主承担点击，显示子视图不得截获触摸；原生控件、输入控件和滚动容器仍保留自身事件语义。弹层触发器的 anchor rect 与弹层自身 panel rect 必须分开保存：系统 popover 使用 anchor rect，自定义 overlay/popover 使用 panel rect 布局，禁止用触发器尺寸压缩弹层内容。
67. 每个线性容器必须在 `native-layout-plan.json` 中形成唯一的 `geometrySystem`：先解析父内容盒，再测量 intrinsic 子项、解析父相对尺寸、分配主轴剩余空间，最后处理交叉轴对齐。`equal-share` 只允许来自相等正 `flex-grow`、相等父相对比例，或无固定宽度且实测等宽并完整占满内容盒的强证据；显式固定宽度和 intrinsic 混排不得因为节点重复或尺寸碰巧接近而均分。SwiftUI/UIKit 必须消费同一分配结果，禁止运行时根据子 View 数量重新猜测 `.fillEqually`。
68. 一个滚动轴只能有一个原生 owner。来源主滚动节点已经 lowering 为 `ScrollView`/`UIScrollView` 时，Screen Container 禁止再次包装同轴外层 Scroll；根内容必须占满可用高度，滚动节点以 flexible/parent-relative 主轴策略接收固定导航、搜索栏或工具栏之外的剩余空间。只有 Screen Root 本身拥有滚动轴时才由 Screen Container 提供外层 Scroll。
69. 控件内部状态配置必须在 `native-control-configuration-plan.json` 中归一化。开关 thumb、分页点数量/当前项/选中与未选中颜色、分段选中项等可由直接子节点和通用 selected/active/checked/current 证据推导；基础 appearance 与 normal/selected/checked 状态 appearance 必须同步，禁止初始化正确后又被状态机旧值覆盖。
70. Skill 回归使用 L1 控件、L2 完整页面、L3 状态与弹层的固定 HTML 基准。每个案例分别检查构建、原生结构、系统控件比例/必需 Primitive 和视觉保真度；像素分数提升不得以降低原生架构合理性为代价，单个案例定向补丁不得进入通用转换规则。
71. 系统导航标题模式必须由显式契约或来源几何决定。`data-ios-title-mode` 优先；否则大字号且靠 leading 的页面标题映射 large title，居中紧凑标题、返回栏标题映射 inline，并把字号、中心偏差和 leading 证据写入 `renderingDecision`。禁止在无显式契约时把所有页面统一写死为 inline 或 large。
72. 系统控件必须同时消费语义和有界几何。计算布局已经证明控件固定宽高时，即使 CSS 未直接声明 `width/height`，原生控件或 wrapper 仍消费该尺寸，禁止被父 Stack 任意拉伸。Slider 等系统控件允许通过官方 tint、thumb image 和保留系统事件/无障碍的轻量子类适配来源内部尺寸；不得为视觉微调重写完整手势或状态机。

## 支持范围

主要技术栈：

- SwiftUI
- UIKit + Swift

默认支持：

- 单页或多页移动端 HTML
- Flex、Grid、普通文档流、绝对定位和常见响应式布局
- 文本、图片、SVG、按钮、输入框、列表、滚动、Tab、弹窗和基础动画
- 同页重复画板归并、局部增删/替换/属性态、条目左滑操作和原生 Sheet/Overlay 状态
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
4. 新项目没有历史架构可推断；必须让用户在 SwiftUI 与 UIKit + Swift 之间明确选择，不得静默默认。
5. 现有项目按目标 source root/业务模块检测，不以整个仓库的多数文件代替模块结论；混合或低置信度时再向用户确认。

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

### 路径约定

先将当前 `SKILL.md` 所在目录解析为绝对路径 `SKILL_ROOT`。Agent 的 shell 工作目录保持用户工程目录；所有 `references/...` 和 `scripts/...` 都相对 `SKILL_ROOT` 读取或执行，不能假设用户工程中存在本 Skill 的脚本。

### 总控入口

默认先读取 `references/orchestration.md` 和 `references/conversion-boundary-gates.md`，从 Agent 当前工作目录运行：

```bash
python3 "$SKILL_ROOT/scripts/run_html_to_ios.py" \
  --workspace "$PWD" \
  --html <prototype.html>
```

已经具备校验通过且无未决交互的 UI IR 时，重复传入 `--ir`。总控负责工作目录工程发现、输入预检、项目生成决策、必要时创建 App、逐页 IR 构建、命名计划、代码生成和 target 接入；构建与启动验证按项目状态分阶段执行。除非正在定位单个阶段故障，否则优先使用总控，不要求用户手工串联脚本。HTML 模式完成提取、UI IR、原生生成和构建后，可声称核心转换及编译验证完成，但不得声称已经视觉验收。只有显式运行 `visual` 且 required states 门禁通过时，才可声称视觉验收完成；多模态 review 是否运行单独报告。

- `empty-no-ios-project`：创建 App 前必须传 `--ui-stack swiftui|uikit`；未选择时返回 `needs-input` 并保留项目决策。
- 一个 Xcode 工程：自动选择；target、scheme 或技术栈无法唯一判断时返回 `needs-input`。
- 多个 Xcode 工程：必须用 `--project` 明确，禁止猜测。
- 只有 Swift Package：确认需要独立宿主后使用 `--create-package-host-app`，禁止默认污染 Package workspace。
- 无效 IR 或未解决交互必须在创建工程前停止。
- 用户传入的 `--interaction-overrides` 是只读确认契约；自动发现的新草稿必须另存，禁止覆盖。
- 新工程自动接生成入口；现有工程不覆盖 App/SceneDelegate/Router，未接入口时报告 `generated-needs-entry-integration`。
- `--dry-run` 只给出工程决策且不写文件。新建托管项目的 `auto` 默认生成并构建，不启动模拟器；已有项目的 `auto` 停在 `generated-awaiting-verification`，等待用户选择 `build` 或 `visual`。截图和纠偏必须显式进入 `visual`。

### 1. 校验输入

确认：

- HTML 文件、目录或可访问 URL
- 是否存在对应 iOS 工程
- 目标页面范围
- 用户已经明确指定的技术栈、设备、最低 iOS 版本和资源要求

只询问会实质改变结果且无法从文件推断的信息。路径不存在、HTML 无法运行或目标页面不明确时停止生成并说明问题。

先执行 `references/conversion-boundary-gates.md` 的转换前门禁。阻断项返回 `needs-input` 或 `failed`；允许降级的事项必须进入后续报告，不能静默忽略。

先读取 `references/html-authoring-contract.md`。总控在浏览器发现前运行 `scripts/validate_html_authoring_contract.py`，将输入分为 L0 推断、L1 结构化或 L2 确定性三个等级。普通 HTML 没有 `data-ios-*` 时允许以警告继续；重复稳定 ID、非法枚举、缺少或不存在的 action target 必须停止。有效显式契约优先于语义 HTML/ARIA 与运行时推断，但显式契约和实际行为冲突时必须产生待确认项，不能静默覆盖。

### 2. 检查 iOS 工程

总控会运行工程检查与组件发现；单阶段调试时可手动运行，并读取发现的项目规范文件：

```bash
python3 "$SKILL_ROOT/scripts/inspect_ios_project.py" <ios-root> --out ios-project-report.json
python3 "$SKILL_ROOT/scripts/discover_ios_components.py" <ios-root> --out ios-component-index.json
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

当状态是 `empty-no-ios-project` 且用户需要完整 App 时，先运行 `scripts/build_project_generation_decision.py`；用户必须确认 SwiftUI 或 UIKit + Swift，随后才运行 `scripts/create_ios_project.rb`。状态是 `swift-package-only` 时先判断 Package 是否为目标 UI 模块；需要独立 App 时再创建工程并接入。创建器检测到现有工程会拒绝覆盖。创建后重新运行工程检查和组件发现。

随后读取 `references/sdk-availability-policy.md`，运行：

```bash
python3 "$SKILL_ROOT/scripts/inspect_ios_sdk.py" \
  --minimum-ios <deployment-target> \
  --out ios-sdk-report.json
```

以 Apple 当前文档、本机 iPhoneOS SDK 和工程 deployment target 共同决定 API。禁止把技能编写时的某个固定 SDK 当成永久最新版。

UI IR 准备完成后，总控必须生成并校验 `native-api-fallback-plan.json`、`compatibility-matrix.json` 和 `ios-compatibility-validation.json`。生成器必须消费两份计划并把哈希与逐项消费结果写入生成清单及 `native-structure-manifest.json`；不能只输出兼容报告而继续生成不受约束的代码。

### 3. 在浏览器中渲染 HTML

先读取 `references/multi-page-routing.md`。对入口运行 `scripts/discover_html_routes.cjs`，生成 `html-route-graph.json`。静态多页、History/Hash SPA 路由、单文档中的 `.page[id]`/tabpanel/`data-page` 虚拟页面和显式 `data-ios-action` 都进入图；原型展示导航标记为 discovery-only，不误当成 App 业务导航。不任意点击可能产生副作用的按钮。每个 screen 使用同一 screen ID 单独提取 render tree，无法确认的动态边保留为 `unresolvedTarget`。

随后读取 `references/dynamic-interaction-discovery.md`，运行 `scripts/discover_html_interactions.cjs` 生成 `interaction-state-graph.json` 与 `html-to-ios.overrides.json`，再运行 `scripts/validate_interaction_graph.py`。必须结合 JavaScript AST 与隔离浏览器 probe 识别 addEventListener、间接函数调用、局部状态、弹层、计时完成和动态页面跳转；不能只用正则扫描源码。并排展示的重复画板先比较页面骨架、文本、层级和几何，归并为 owner screen 的 `visual-state-representation`。总控随后为状态画板构建 UI IR，并运行 `scripts/merge_visual_state_ir.py` 生成通用节点差分；不得额外生成业务 Screen/ViewController。源 HTML 保持只读，歧义写入带 SHA-256 指纹的旁路覆盖文件。

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
- 浏览器 source order、伪元素 phase、stacking context owner、paint group、z-level 与稳定绘制顺序
- 文字 Range 行框、行数、字体加载状态和裁剪信息
- 内联 SVG markup、图片 URL 与 CSS background 资源详情

等待字体和页面稳定后再提取。默认阻止与页面来源无关的远程请求；不得让不可信 HTML 访问用户敏感文件或凭据。

### 4. 选择页面根容器

不要用“检测到 0 或 1 个候选就一定取整个页面”的简单规则。

- 页面本身就是移动端页面：使用 `body` 或应用根节点。
- 大展示板中包含一个或多个手机画板：只选择画板内部应用内容。
- 去除纯手机外壳、模拟刘海、展示标签和背景板装饰。
- 多个候选且无法可靠确定时，展示候选的 selector、尺寸和截图位置，请用户确认。

没有显式手机画板时，先用 `scripts/analyze_responsive_layout.cjs` 在 320/375/393/430 宽度分类来源：

- `responsive-document` / `responsive-mobile-root`：根宽跟随 viewport、移动宽度无文档级横向溢出，并有 viewport、媒体查询或实测重排证据；按目标移动 viewport 直接提取，`1 CSS px = 1 pt`，不缩放桌面版。
- `fixed-mobile-artboard`：按固定手机设计稿执行一次 token 归一化。
- `desktop-only`：存在桌面最小宽度、持续横向溢出或根宽明显大于手机 viewport；在用户指定移动页面范围或明确允许响应式重设计前停止。
- `ambiguous-responsive-source`：没有手机画板且实测证据不足；要求用户确认 app root、目标 breakpoint 或转换范围，不得任选卡片当页面根。

### 5. 生成并校验 UI IR

先运行 `scripts/build_ui_ir.py`，将 `extract_render_tree.cjs` 的输出转换为可审查的 UI IR 草稿：

```bash
python3 "$SKILL_ROOT/scripts/build_ui_ir.py" render-tree.json \
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
- 每个滚动容器的 `scrollAxis`、scroll/client 尺寸、实际溢出方向和滚动轴所有权
- 文字实测行数、`nowrap`/line clamp/ellipsis、横向裁剪状态和紧凑内容的 intrinsic-size 策略
- 固定尺寸、最小/最大尺寸、宽高比、flex shrink/wrap 以及横向重复条目的实测 item 尺寸
- 页面导航图和状态变化
- 分类、筛选、分页或步骤切换引起的结构化内容变体；必须保留 source node → target container → 有序 item 文字叶子映射，不能把变化后的整段 `textContent` 当成一个标签
- 支持级别、降级项和警告
- 每个节点的 SwiftUI/UIKit 控件建议、样式策略、置信度和映射理由
- 每个候选 API 的 SDK 核验状态、最低版本和降级路径
- motions、关键帧采样点和 visual state matrix
- 动态交互的多节点来源、跨 screen 副作用、自动迁移和 prerequisite interaction sequence

运行 `scripts/validate_ui_ir.py`。自动映射置信度低于 `0.7` 的节点必须人工复核；IR 未通过校验时不得开始生成代码。

随后读取 `references/text-calibration.md`、`references/form-and-dynamic-data.md`、`references/responsive-auto-layout.md` 和 `references/page-regions-and-system-chrome.md`，运行 `scripts/build_text_calibration.py`、`scripts/analyze_responsive_layout.cjs` 和 `scripts/probe_scroll_region_behaviors.cjs`。固定缩小画板只允许将设计 token 一次性归一化到基准设备；原生页面始终使用 Auto Layout/SwiftUI layout，不能运行时整体缩放。文字行数、baseline、截断和富文本 range 进入专项验收。文本控件必须先生成 `textBehavior`，区分 input/display、单行/多行、editable/readonly/selectable/scrollable，再选择 TextField、TextView 或 Label；纯展示 TextView 必须关闭编辑。顶部栏、底部栏、浮动操作和 presentation 必须进入 screen regions；fixed、sticky、scroll-away、hide-on-scroll、collapse 和 appearance-change 必须用真实滚动采样判定，不得只靠 class 名或首帧位置猜测。

文字 Range 高度只作为行盒、裁剪和 fallback 诊断，除非来源自身固定高度或裁剪，否则不得直接生成 Text/Label 的硬高度。首基线只对纯文字叶子的字形位置做有界校准，不得靠修改父容器 padding 或整页纵向偏移掩盖误差。纵向走查按顶部系统区域、滚动内容、固定底栏和弹层分别建立锚点；安全区由系统管理时，不得从滚动容器宽高或内容高度再次手工扣减。

动态列表、筛选、分类或步骤切换改变内容高度时，交互快照必须记录目标容器前后 rect。生成器优先让原生集合按内容和约束计算尺寸；仅当 HTML 自身存在固定高度容器或 presentation 且浏览器证明确有尺寸变化时，才在共享状态中生成该节点的 `sizeOverrides`。SwiftUI 和 UIKit 消费同一状态；禁止为某个截图在 Controller/View 中追加孤立常量。

多页面项目保留独立 `html-route-graph.json` 和 `interaction-state-graph.json` 作为跨 screen 契约。每个 screen 的 UI IR 负责页面内部结构；路由图负责 screen 集合，交互图负责 push/present 候选、sheet、popover、Tab、返回/关闭、计时跳转和局部状态。覆盖文件中的已确认原生所有权必须合并进 IR，禁止把所有页面压成一个 screen IR。

### 6. 规划原生结构

读取 `references/native-architecture-adjustment-plan.md`、`references/six-layer-native-architecture.md`、`references/common-mapping-rules.md`、`references/control-mapping-matrix.md`、`references/native-control-selection-policy.md`、`references/native-component-catalog.md`、`references/interaction-rules.md`、`references/navigation-presentation-containment.md`、`references/page-regions-and-system-chrome.md`、`references/custom-component-fallback.md`、`references/motion-and-effects.md`、`references/edge-case-policy.md`、`references/multi-page-routing.md`、`references/project-component-discovery.md`、`references/text-calibration.md`、`references/form-and-dynamic-data.md` 和 `references/responsive-auto-layout.md`，再按技术栈读取：

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

总控先生成并校验全局唯一的 `native-application-plan.json`，确定 App 容器、初始页面、Tab、每个 Tab 的 Navigation Stack、screen membership 和跨页面 route；随后运行 `scripts/build_native_architecture_plan.py` 生成 `native-architecture-plan.json`。页面计划必须完整包含 Application Container 兼容镜像、Screen Container、Screen Regions、Content Container、Reusable Section/Item 和 Leaf Component 六层，并引用全局 application membership 与唯一布局关系图，不得逐页面重新猜测 Tab 或 Navigation Stack。它将 controller/container 所有权、导航栏绘制、滚动行为、Table/Collection/Scroll/静态容器选择、Cell 复用、叶子 View/Control、presentation 和 Safe Area 分开建模。系统导航栈与顶部栏是否使用系统样式是两个独立决策；滚动页面的容器宽高始终等于父容器 bounds，禁止用 `width/height - safeAreaInsets` 计算 frame。

随后读取 `references/structural-fidelity.md`。总控必须生成 `layout-relation-graph.json`，固化 containment、视觉子节点顺序、相邻间距、对齐、等宽/等高、宽高比、overlap 与 scroll axis owner；再生成 `structural-fidelity-report.json`，验证这些关系能被六层原生架构完整表达。该生成前门禁不依赖截图或多模态能力，失败时必须回到提取、UI IR 或架构计划修复，不得直接补丁生成后的 Swift。

随后读取 `references/native-layout-lowering.md`。总控必须运行 `build_native_layout_plan.py` 与 `validate_native_layout_plan.py`，把架构关系降级为容器算法、视觉顺序、独立行列间距、尺寸表达式、CSS border-box、Flex/Grid item、定位参照系、状态布局增量和复合控件槽位。原始 CSS 声明用于保留 `%`、`calc()` 和 Grid 轨道语义，computed style 与浏览器几何用于验证最终生效结果；两类证据不得相互替代。该门禁通过后生成器才能运行，并必须通过 `--native-layout-plan` 消费同一份计划。

布局计划完成后，总控生成并校验 `native-appearance-plan.json`，将背景、透明度、四角椭圆半径、四边边框、阴影、裁剪、媒体适配和排版外观从布局兼容镜像中抽离。文字行盒、baseline、换行与 intrinsic size 仍由布局计划拥有，避免外观和布局互相循环修改。

控件计划使用 `native-control-configuration-plan-1.1`：先形成语义候选和上下文角色，再评估系统控件固有尺寸与来源几何，最后沿用 system control、system control with wrapper 或已证实的自定义降级。Presentation 完成后，总控生成并校验 `native-interaction-motion-plan.json`，把 Tab、Navigation、Screen Host、Reusable Content 和 Source Component 的动作/动画绑定到唯一 owner 与原生 executor。

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
python3 "$SKILL_ROOT/scripts/generate_ios_from_ir.py" \
  --ir page1-ui-ir.json \
  --ir page2-ui-ir.json \
  --out-dir <source-root>/Generated/HTMLToIOS \
  --ui-stack swiftui
```

- 多页面重复传入 `--ir`，第一份是默认根页面。
- 默认生成目录必须与人工源码隔离，并保留 `.html-to-ios-generation.json`。
- 总控运行 `scripts/build_native_naming_plan.py` 生成 `native-naming-plan.json`。新工程页面文件和类型默认使用 `Sky` 前缀；已有项目优先复用稳定模块前缀，没有证据时使用 target 名回退。`--name-prefix` 显式值优先，上一轮计划优先于重新猜测，类型冲突必须停止生成。
- 输出路径必须以 `Generated/HTMLToIOS` 结尾。内部按业务模块生成 `<Module>/Models|Screens|Controllers|Sections|Cells|Views` 中实际需要的源码，App 级 Navigation/Tab 放入 `Core/Navigation`，通用契约和运行时放入 `Core`，资源放入 `Resources`；禁止把全部文件平铺在一个目录。
- 只有确认目标工程的生成目录规范无法采用标准路径时，才可显式使用 `--allow-nonstandard-output`，并保持等价职责分层和独立生成所有权。
- 生成器拒绝 `requiresResolution=true` 的交互；禁止用 `--allow-unresolved` 掩盖正式交付中的歧义。
- 用户修改过的生成文件不得覆盖；候选版本进入 `<out-dir>.conflicts`，由 Agent 做节点级合并。
- 生成的 JSON 载荷必须作为 target resource 接入，不能内联成超大 Swift 字符串。
- 通用运行时只是原生基线；发现现有 Router、Design System、Cell 或控件时，按映射计划替换为项目组件。
- 六层架构计划必须物化为强类型页面源码：每个 screen 生成 UIContract，Section 生成独立容器，复用内容生成真实 UIKit Cell 子类或 SwiftUI Item View，并通过稳定 node ID 注册进入实际渲染链路。禁止只生成目录占位，或让一个通用 JSON NodeRenderer 独占全部页面架构。
- 每个 screen 生成 LayoutContract，保存容器的 source/visual order、axis、alignment、distribution、wrap、gap 和子项尺寸策略。它用于关系约束和视觉校准，不得转成逐节点页面绝对 frame。
- 读取 `references/native-structure-consumption.md`。生成器必须消费 `layout-relation-graph.json` 并写出 `native-structure-manifest.json`；随后运行 `scripts/validate_native_structure_manifest.py`。只有 screen/node/relation 集合、原生等价提升、区域所有权及 Swift/Payload 哈希全部通过，才能接入 Xcode target。
- 普通流子节点与 absolute/fixed 子节点发生实质重叠时，必须按浏览器绘制顺序进入同一 Overlay/ZStack；没有实质重叠时仍保持普通流与独立 overlay，避免装饰节点改变内容测量。
- 叶子强类型化只覆盖输入状态所有者、显式/项目组件、特殊媒体或绘制组件，以及拥有稳定业务 ID 的交互控件。普通文本、装饰节点、SVG 内部路径、自动编号节点和已由 Cell 拥有的 item 不按一节点一文件生成。
- 保持项目命名、目录、访问控制和状态管理风格。
- 静态页面不强制创建 ViewModel。
- 动态数据不是转换核心。HTML 当前可见内容只作为确定性视觉 fixture，用于还原列表、loading、empty、error 等画面；生成器不创建接口、请求层、分页器或业务 ViewModel，也不因重复列表猜测 endpoint。只有用户明确要求业务接入时，才把 `dataBinding` 交给项目数据层。
- 输入控件必须保存编辑状态并遵循 maxlength、键盘类型、return key、自动大写、自动纠错和 autofocus。键盘与 Safe Area 只能有一个避让所有者；滚动容器保持父级完整 bounds，不得预减键盘或安全区高度。
- 读取 `references/scroll-and-attachment.md`。架构完成后必须生成并验证滚动归属计划；原生生成后还要在 `native-structure-manifest.json` 中证明根轴向、区域 attachment、Safe Area owner 和 keyboard avoidance 已被消费。
- 读取 `references/native-control-configuration.md`。系统控件选择完成后必须生成并验证内部配置计划；原生生成后还要证明每个计划控件的 geometry、appearance、preferred style 与 native states 已被 Payload/Swift 消费。
- 系统控件目录至少覆盖当前 SDK 的 `UIButton`、`UITextField`、`UISwitch`、`UISlider`、`UIStepper`、`UISegmentedControl`、`UIDatePicker`、`UIColorWell`、`UIPageControl`、`UIPasteControl`、`UIRefreshControl`，并覆盖页面常用的 `UITextView`、`UISearchTextField`、`UISearchBar`、`UIPickerView`、`UIProgressView`、`UIActivityIndicatorView`、`UICalendarView`。先运行 SDK 与覆盖审计，再按最低 iOS 版本选择直接 API 或 fallback。
- Button、UIControl 和输入控件的 pressed、focused、disabled、selected 外观必须优先来自浏览器实测状态样式，并由原生控件状态驱动；不得用普通 View 手势或统一透明度假装所有状态。
- 仅在具有独立职责、重复使用或明显降低复杂度时拆组件。
- 每个可交互节点使用 UI IR 中的稳定 ID 作为 `accessibilityIdentifier`。
- 保持 source node → IR node → native view 的追溯关系。
- Safe Area、状态栏、导航栏和 Home Indicator 只计算一次。
- 每个 screen 必须有且只有一个 Safe Area owner。默认由 SwiftUI/`UIScrollView.adjustedContentInset` 管理系统安全区；滚动容器铺满父容器，不得预先减去顶部、底部、左侧或右侧安全距离。自绘栏位只追加栏位自身高度一次，禁止把系统 safe area 再手工加入 contentInset、padding 或 frame。
- 自绘顶部栏和底部操作栏必须从滚动内容拆出；普通文档 footer 保持随内容滚动。
- 容器轴向优先取浏览器 computed style 的 `display` 与 `flex-direction`；`layout.mode` 只作为缺失 computed style 时的回退。`row-reverse`/`column-reverse` 必须同步原生子节点顺序，不能因元素是 absolute/fixed 就丢失其内部 Flex 语义。
- 浏览器会为非 Flex 元素返回默认 `flex-direction: row`；只有 `display:flex/inline-flex` 或明确的行布局证据才允许据此生成横向 Stack。普通 `display:block` 保持纵向文档流；单行富文本按 Range 行框聚合后保留 inline/baseline 布局。混合流式与 absolute/fixed 子节点时，`orderedChildNodeIds` 只保存参与测量的流式节点，定位节点由独立 overlay 所有权承接，`paintOrderNodeIds` 仍覆盖全部直接子节点。
- 浏览器测得的 `preferredWidth` 不能无条件套到每层 SwiftUI 容器。结构容器由父级 Stack、Grid 和可用宽度分配；叶子节点保留理想宽度，Button、输入和选择类原生控件在来源明确时保留最小宽度，避免文字内在尺寸把等宽操作栏压缩。
- 每个 scroll node 必须具有单独的轴向契约。页面根纵向滚动容器只拥有 vertical；嵌套横向列表只拥有 horizontal。不得因为某个子节点暂时越界就把根页面升级为双轴滚动，也不得用一个双轴 ScrollView 包住整页兜底。
- 横向重复条目的来源 rect、`flex-basis`、`min-width`、`flex-shrink` 和 gap 是 item sizing 证据。来源未换行且实测为一行时，原生条目必须保持 intrinsic/fixed width 与单行语义，禁止由父容器平均拉伸后导致文字换行。
- Table/Collection 的 `collectionLayouts` 必须保存 layout engine、Section 顺序、item IDs、header/footer、pinning、column count、content insets、主/交叉轴间距、width/height mode、估算高度与宽高比。SwiftUI 和 UIKit 必须消费同一契约，禁止统一依赖 `automaticSize`。
- 响应式 Grid/Collection 必须把 `responsive-layout.json` 送入布局降级阶段。解析 `repeat()`、`minmax()`、`auto-fit`、`auto-fill`、`fr`、固定值、百分比及混合轨道，并保存 320/375/393/430pt 下的容器宽度、列数、item 几何和文字行数。原生运行时按实际容器宽度选断点，禁止固定使用基准画板列数。
- 异构集合必须生成 `itemSizingByNodeId`，分别保存 fixed/estimated/aspect-ratio、column/row span 和响应式行数；section 中位数只能作为缺失数据的 fallback。SwiftUI item modifier、UIKit flow/table delegate 与 compositional layout 必须优先消费 item 级契约。
- `UITableView` 使用 `.plain` 并显式管理 header/footer、content inset、row gap 和 separator，避免 `.grouped` 自动外观污染来源视觉。来源存在行选择或左右滑操作时，优先生成 delegate selection 与 `UISwipeActionsConfiguration`；自由布局中无法使用系统 swipe 生命周期时才使用原生自定义手势兜底。
- Compositional 根必须覆盖全部直接流式 Section。普通标题、工具栏或静态块作为单 item Section 保留，结构化 list/grid/carousel 再使用自己的复用与尺寸契约；不得因只收集列表和网格而丢失普通内容。
- 紧凑图标、状态槽、缩略图和其视觉容器应保留来源宽高比；接近方形且尺寸稳定的容器生成等宽高或 aspect-ratio 约束。禁止只固定一边后被 HStack/UIStackView 拉成长方形。
- `overflow:hidden/clip` 只表示裁剪；只有计算样式允许滚动且 scroll/client 度量或行为 probe 证明轴向成立时才生成滚动容器。
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

先完成构建。只有 verification 为 `visual` 时才读取 `references/visual-validation.md` 并执行下面第 3–16 项；`build` 模式在第 2 项完成后结束核心验证。

1. 先读取 `project-generation-decision.json` 的 verification：`ask` 只生成并提示，`build` 运行 `xcodebuild` 并完成核心验证，`visual` 才启动模拟器、截图、对比和纠偏，`none` 明确跳过。已有项目 `auto` 解析为 `ask`，新建托管项目 `auto` 解析为 `build`。
2. 修复由本次生成引起的编译错误。
3. `visual` 模式已经运行 `scripts/build_visual_state_manifest.py`，从 UI IR 生成 HTML actions 与 iOS accessibility actions；先复用报告中的 manifest。
4. `visual` 模式已经运行 `scripts/capture_html_states.cjs` 捕获 required HTML states；只有产物缺失或正在单阶段调试时才手动重跑。
5. `visual` 模式下总控使用 `scripts/prepare_visual_ui_tests.rb` 创建隔离且带 ownership 标记的 `HTMLToIOSVisualTests` target，再由 `scripts/capture_ios_states.py` 执行 XCUITest、导出 xcresult 附件并归一化到目标逻辑 viewport。现有同名非托管 target 不得覆盖；单阶段调试时才手动运行这两个脚本。
6. 对移除、隐藏、展开、选择和路由类交互，视觉 manifest 应携带可推导的后置状态断言；XCUITest 必须先验证目标节点消失、出现、选中或路由到达，再截图。只完成 tap 而页面状态未变化不得算作有效状态捕获。
7. 运行 `scripts/build_visual_review_bundle.py` 检查精确尺寸、全局 mismatch、平均差异、critical region 和文本 edge mismatch。任一 required state 超阈值必须重新生成和截图，不能由多模态评语改成通过。
8. 同时读取每个状态的 `geometry-report.json`，先核对高置信度节点的 top/middle/bottom median y delta、height delta、`anchorRows` 和 `driftTransitions`。固定画板 region 坐标必须使用与 HTML 截图一致的 cover/center 裁切；累计漂移沿首个 transition 回查中间容器，禁止用整页 y offset 掩盖 border-box、item 高度或 gap 丢失。
9. 判断当前 Agent 的实际图像查看能力，并以 `available`、`unavailable` 或 `auto` 传给 review bundle；不要根据模型名称猜测。脚本生成每个状态的像素报告、comparison、heatmap、overlay、regions 和能力门控状态。
10. 能力为 `available` 时读取 `references/visual-agent-review.md`，实际打开图片并检查 failed-threshold 状态；能力为 `unavailable` 时标记 `not-run`；`unknown` 时先尝试打开一张图片，不能把 unknown 当成完成。
11. 视觉失败后先运行 `build_visual_correction_plan.py`。只有计划包含高置信度、状态归属明确且幅度未越界的 `proposedMutation` 时，才运行 `apply_visual_correction_plan.py` 生成新的 corrected UI IR；随后重新生成、构建并回归全部 required states。原始 IR 必须保留，默认最多 3 轮，单轮提升低于 0.25% 或没有安全修正时停止。
12. 至少复核首屏、有意义的长页末端、弹层和切换状态。动画 0/50/100 帧只有具备原生确定性采样钩子时才设为 required，否则作为 advisory，不能用三张相同静态图冒充动画验证。
13. 在项目支持的 320/375/393/430pt 或实际设备宽度上验证 Auto Layout、文字换行、边距和横向溢出；使用 `scripts/validate_responsive_ios_matrix.py` 为每个宽度指定真实可用 Simulator，使用 `scripts/compare_text_calibration.py` 核对 iOS 文字测量结果。禁止把一台设备截图缩放后冒充多设备验证；本机缺少某尺寸 runtime 时必须如实报告。
    总控可重复传入 `--runtime-case 'PROFILE=WIDTHxHEIGHT@ORIENTATION:SIMULATOR_NAME'`。每个显式 profile 都是必测项，结果由 `build_ios_runtime_compatibility_report.py` 汇总；未提供运行 case 时保持 `optional-not-requested`，不能继承来源分析状态并声称运行通过。
14. 执行轴向隔离走查：纵向页面的横向拖动不得移动根内容；横向 carousel 的纵向拖动不得带动其自身产生纵向偏移。逐项核对横向 item 宽度、文字行数、图标容器宽高比和末项可达性。
15. 几何采集仅在 `-HTMLToIOSGeometryCapture 1` 测试启动参数下扩展 accessibility tree，正常 App 不改变辅助功能分组。报告必须同时给出全部可见节点和 `validationRegions` 节点的采集率；容器合并 frame、装饰节点和横向滚动拥有的越界不能误判为根页面溢出。
16. 同一生成器版本至少执行一次 SwiftUI 与 UIKit 的真实 `xcodebuild` 回归。对 sheet、modal、popover、overlay、push 和 tab 切换，XCUITest 必须验证目标页面或 presentation 根节点实际出现后再截图。

对于大展示板中的固定手机画板，状态捕获使用 HTML 源 viewport 执行动作，再按 UI IR 的目标 viewport 生成归一化对比图；这是验收图片的单次设计归一化，不得转化为 iOS 运行时整页缩放。归一化方式和原始尺寸必须写入 captures report。

用户选择验证后，如果环境无法启动模拟器，编译验证仍是必需项，并明确报告未完成的视觉验证及原因。用户尚未确认已有项目验证时，不得把 pending 说成失败或通过。

模型不支持图像时，核心转换链路照常执行。若用户显式选择 `visual`，仍可执行不依赖模型看图的状态矩阵和确定性像素检查，并把多模态阶段标记为 `not-run`；若没有选择 `visual`，不创建截图和纠偏产物。

## 交付要求

先执行 `references/conversion-boundary-gates.md` 的转换后门禁。核心转换完成至少要求 UI IR、原生架构、生成前结构门禁、生成后原生消费门禁、工程接入和所请求的构建门禁通过；只有请求 `visual` 时才追加 required states、滚动轴、系统区域和确定性视觉门禁。报告必须区分“转换/编译完成”和“视觉验收通过”。

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
5. 自动纠偏只接受计划白名单内的 UI IR 几何操作；外观、系统控件配置、Presentation 和低置信度差异没有确定目标值时不得猜测修改。
6. 重新生成、构建、截图和对比；失败时可直接回退到上一份 IR。
7. 报告本次修改范围及是否影响其他页面。
