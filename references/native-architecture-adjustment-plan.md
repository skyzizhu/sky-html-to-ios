# Native Architecture Adjustment Plan

## 文档状态

- 状态：已按兼容方式实施，旧字段暂保留为兼容镜像
- 目标：在保留现有能力和六层结构的基础上，调整职责边界、执行顺序和契约归属，提高 HTML/CSS/JavaScript 首次转换为 SwiftUI/UIKit 原生页面时的准确性、稳定性和可维护性。
- 范围：Skill 架构与工作流规划。
- 非目标：本轮不修改用户 HTML、现有 iOS 工程或截图验收流程，也不移除旧契约字段。

## 核心目标

转换结果主要需要保证三件事：

1. 原生控件和视图映射正确。
2. 坐标、尺寸、排版和视觉属性尽可能接近高保真原型。
3. 路由、Presentation、局部交互和动画由正确的原生所有者执行。

截图和多模态走查只作为生成后的可选验收，不参与首次架构、控件、布局和外观决策。

## 设计原则

1. 先完整读取来源，再做从全局到局部的原生规划，不能边识别边写死代码。
2. 六层结构只表达原生节点的所有权和包含关系；布局、外观、控件配置和行为通过横向契约附着到各层节点。
3. 同一事实只能有一个权威来源。SwiftUI/UIKit 生成器不得分别重新推断页面架构、节点顺序、控件类型或视觉属性。
4. 系统控件优先，但必须经过语义、上下文和几何适配验证；系统本体不足时优先增加 Configuration 或原生包装层。
5. 原生代码必须保持合理的 Controller、Container、Section、Cell、Component 分工，不能为了匹配单张截图堆叠无意义包装 View 或固定 frame。
6. 所有核心计划和消费门禁必须能够在没有截图、Simulator 和多模态模型的环境中运行。

## 六层所有权架构

### 1. Application Architecture

全局唯一，负责：

- App 入口；
- Tab 结构；
- 每个 Tab 的 Navigation Stack；
- 全局路由表；
- 首屏；
- 已有 Router/Coordinator 的接入方式；
- 全局 API fallback 和生成/复用边界。

典型原生对象：

- SwiftUI：`TabView`、`NavigationStack`；
- UIKit：`UITabBarController`、`UINavigationController`。

### 2. Screen Host

每个业务页面一个 Screen Host，负责：

- 页面生命周期；
- 页面级状态；
- 页面根内容；
- Presentation 所有权；
- 页面级键盘和系统栏协调。

典型原生对象：

- SwiftUI Screen；
- `UIViewController`。

### 3. Screen Regions

负责划分：

- 系统导航栏；
- 自定义顶部栏；
- 内容区域；
- 底部操作栏；
- 悬浮区域；
- Overlay；
- Safe Area 和滚动附着关系。

每个区域必须拥有唯一 owner，禁止系统导航与自定义导航重复渲染，也禁止 Safe Area 或底部 inset 重复计算。

### 4. Content Container

根据来源结构选择最小且合理的原生内容容器：

- `UIView` / SwiftUI Stack；
- `UIScrollView` / `ScrollView`；
- `UITableView` / `List` / `LazyVStack`；
- `UICollectionView` / SwiftUI Grid；
- Compositional Layout；
- Positioned Overlay。

选择依据包括重复模式、滚动轴、单列或多列、Section、异构内容、sticky 行为、复用需求和子节点关系，不能只依据 HTML tag、class 名或条目数量。

### 5. Reusable Content

负责：

- Section；
- Cell；
- Header/Footer；
- Supplementary View；
- 重复 Card；
- 可复用组合组件。

只有存在重复、状态、独立布局职责或明确业务语义时才生成独立组件类型。

### 6. Leaf Components

负责最小原生控件和视图：

- Button、Label、ImageView；
- TextField、TextView；
- Switch、Slider、Stepper；
- Picker、SegmentedControl、PageControl；
- Progress、Activity Indicator；
- 其他系统控件和必要的自定义 View/UIControl。

胶囊、圆角、阴影等通常属于 Leaf 或组合组件的外观，不独立成为第七个结构层级。

## 横向执行契约

六层回答“节点属于哪里”，以下契约回答“节点如何布局、如何显示、如何交互”。

### Native Layout Plan

负责：

- 父子关系；
- 视觉顺序和绘制顺序；
- 坐标关系；
- 宽高、min/max 和宽高比；
- Padding、Margin、Gap 和对齐；
- Flex、Grid、Overlay；
- 滚动轴和滚动 owner；
- 响应式约束；
- Safe Area 与键盘避让；
- 内容驱动和系统固有尺寸。

### Native Appearance Plan

负责：

- Shape：四角水平/垂直半径、Capsule、Circle、Clip、Mask；
- Fill：背景色、多层渐变、背景图片、位置、尺寸和重复；
- Stroke：四边独立宽度、颜色和线型；
- Effects：多重阴影、Inset Shadow、Opacity、Blur、Filter 和 Blend Mode；
- Typography：字体、字号、字重、行高、字间距、对齐、行数和截断；
- Media：Content Mode、Crop、Tint 和 Rendering Mode；
- Decoration：伪元素和非交互装饰；
- SwiftUI/UIKit fallback 和 unsupported 证据。

Typography 的度量参数必须先参与 Layout 求解，最终绘制必须消费相同的字体参数。

### Native Control Configuration Plan

负责：

- 控件语义候选；
- 父级上下文角色；
- 系统控件候选；
- 系统固有尺寸和内部 Insets；
- normal、highlighted、focused、editing、selected、checked、disabled、loading 状态；
- Geometry Fit；
- 最终控件决策；
- Configuration、包装层或自定义实现；
- SDK 可用性和 fallback。

### Native Interaction and Motion Plan

负责：

- 点击、输入和选择；
- push、pop、replace 和 Tab 切换；
- present、sheet、popover、alert 和 dismiss；
- 局部展开、收起、选择和加载状态；
- Cell 左滑、上下文菜单和手势；
- addChild/removeChild；
- transition、keyframes、duration、delay 和 timing curve；
- 动画目标节点和原生执行者；
- 系统转场、自定义转场及降级策略。

## 目标工作流

```text
HTML/CSS/JavaScript
    ↓
移动页面、路由、状态和交互识别
    ↓
浏览器计算样式、DOM、几何、文字和资源提取
    ↓
UI IR
    ↓
全局 Native Application Plan
    ↓
唯一 Layout Relation Graph
    ↓
六层 Native Architecture Plan
    ↓
控件语义候选
    ↓
Layout + Typography + 系统控件固有尺寸联合求解
    ↓
最终控件决策 + Native Appearance Plan
    ↓
Native Interaction and Motion Plan
    ↓
统一生成 SwiftUI/UIKit
    ↓
确定性结构与契约消费验证
    ↓
可选构建、Simulator 和截图验收
```

## 全局 Application Plan

新增全局唯一的 `native-application-plan.json`，建议包含：

```json
{
  "schemaVersion": "native-application-plan-1.0",
  "applicationContainer": {
    "id": "main-application",
    "ownership": "generated-or-existing-project-router",
    "swiftUIType": "TabView + NavigationStack",
    "uiKitType": "UITabBarController + UINavigationController"
  },
  "initialScreenId": "home",
  "tabs": [],
  "navigationStacks": [],
  "routes": [],
  "entryIntegration": {},
  "apiFallbacks": []
}
```

每个 Screen 只保存应用归属引用：

```json
{
  "applicationContainerId": "main-application",
  "tabId": "home-tab",
  "navigationStackId": "home-navigation"
}
```

不再在每个 Screen 中重复定义 `applicationContainer`。

## 布局关系前置

UI IR 完成后立即生成唯一 `layout-relation-graph.json`：

1. Architecture Plan 必须消费关系图，不能自行重新推断完整关系。
2. Content Container Plan 负责选择容器，不重新定义节点来源顺序。
3. Native Layout Plan 负责执行约束，不重新决定 Table/Collection/Scroll 类型。
4. SwiftUI/UIKit 只能消费相同的容器、顺序和尺寸契约。
5. 关系图、架构计划、布局计划和生成清单通过哈希建立来源链。

## 控件映射闭环

### 阶段一：Semantic Candidate

根据以下证据生成候选：

- HTML tag；
- ARIA role；
- JavaScript 行为；
- 状态；
- 文本和资源；
- 父级上下文；
- 所在 Region、Cell、Toolbar 或 Tab。

### 阶段二：Geometry Fit

使用以下信息检查系统控件是否适合：

- HTML 实测宽高；
- 系统控件固有尺寸；
- Content Insets；
- 标题、图标和槽位顺序；
- 字体度量；
- 状态视觉；
- 最低 iOS 和 SDK 能力。

### 阶段三：Final Decision

按以下顺序选择：

```text
系统控件
→ 系统控件 + Configuration
→ 系统控件 + 原生包装层
→ 组合 View
→ 自定义 UIControl/View
→ unsupported
```

每个最终决策必须记录候选、上下文、适配结果、误差、拒绝原因、最终实现和 fallback。

Geometry Fit 失败时只重新求解受影响节点，默认最多两轮；仍无法稳定映射时进入 review 或 unsupported，禁止无限迭代。

## 容器选择规则

容器选择分为两步。

第一步识别内容模式：

- 是否大量重复；
- 是否单列或多列；
- 是否横向滚动；
- 是否存在多个 Section；
- 是否需要 Cell 复用；
- 是否存在 sticky Header/Footer；
- 是否异构；
- 谁拥有主滚动轴。

第二步选择原生容器：

- 少量静态内容使用 View/Stack；
- 普通连续长页面使用 ScrollView；
- 长单列同构内容使用 TableView；
- 多列、横向或异构 Section 使用 CollectionView；
- 页面纵向 Scroll 与内部横向 Collection 可以共存；
- 禁止同一纵轴上存在两个同时拥有滚动权的原生容器。

## 胶囊、卡片和细节属性

胶囊通常是一种 Appearance，而不是固定控件：

- 静态文字胶囊：Text/Label + Capsule Appearance；
- 可点击胶囊：Button/UIControl + Capsule Appearance；
- 选中标签：Control + selected state + Capsule Appearance；
- 大量横向标签：Collection + Capsule Cell；
- 系统分段选择：优先 SegmentedControl。

卡片是否生成独立 View 取决于结构、状态和复用：

- 单纯背景区域：容器 + Appearance；
- 图片、标题和操作组合：组合 View；
- 多次重复：Reusable View 或 Cell；
- 列表卡片：Table/Collection Cell；
- 一次性简单结构：不生成额外类型。

圆角、阴影、背景、边框和字体不得因为视觉重要就生成额外结构层级。只有实际需要独立布局、状态、交互或复用时才增加原生 View。

## 行为所有权

- Tab 切换归 Application Architecture；
- push/pop 归 Navigation Stack；
- sheet/alert/popover 归 Screen Host；
- 左滑和条目操作归 Table/Collection；
- 展开、选择和 Loading 归对应组件；
- addChild/removeChild 归 Screen Host；
- 自定义顶部栏隐藏/显示归对应 Screen Region；
- 动画必须由发生状态变化的最小稳定 owner 执行。

每个行为至少保存 source、trigger、action、target、native owner、native executor、transition、timing、system implementation、custom fallback 和 unsupported reason。

## 生成规则

生成器只能消费已验证计划：

- Application 代码来自 Application Plan；
- Screen 代码来自 Screen Architecture；
- 内容容器来自 Content Container 决策；
- 控件来自 Final Control Decision；
- 坐标和尺寸来自 Native Layout Plan；
- 视觉来自 Native Appearance Plan；
- 跳转和动画来自 Native Interaction and Motion Plan。

生成器遇到缺失决策时必须停止、进入 review 或输出明确降级，不能在 SwiftUI/UIKit 分支中临时猜测。

## 验证门禁

### 生成前

- 全局只有一个 Application Container；
- 每个 Screen 引用有效的 Tab/Navigation Stack；
- 每个节点只有一个原生 owner；
- 每个滚动轴只有一个 owner；
- 每个可见节点都有 Layout 和 Appearance；
- 每个交互控件都有最终 Control Decision；
- 每个路由和 Presentation 都有执行者；
- 每个动画目标节点存在；
- Typography 度量和绘制参数一致；
- Safe Area、键盘和自定义栏位不重复计算；
- 不同计划没有重复定义同一权威事实。

### 生成后

`native-structure-manifest.json` 必须证明实际 Swift/Payload 已消费：

- Application Plan；
- 六层 Screen Architecture；
- Layout Relation Graph；
- Native Layout Plan；
- Native Appearance Plan；
- Final Control Decision；
- Interaction and Motion Plan；
- API fallback 和兼容矩阵。

任何 required 契约未消费时，不得接入 Xcode target，也不得声称核心转换完成。

## Schema 与兼容策略

建议版本：

- `native-application-plan-1.0`；
- `native-architecture-plan-1.2`；
- `native-layout-plan-1.2`；
- `native-appearance-plan-1.0`；
- `native-control-configuration-plan-1.1`；
- `native-interaction-motion-plan-1.0`。

兼容原则：

1. 新总控只生成新版本计划。
2. 生成器在一个过渡版本内读取旧计划并输出明确 warning。
3. 旧 `native-layout-plan` 中的 appearance 字段可迁移到新 Appearance Plan。
4. 不能静默用旧字段覆盖新契约。
5. 计划哈希不一致或输入过期时必须停止生成。

## 实施轮次

### 第一轮：全局架构和权威关系

- 新增 Native Application Plan；
- 从每个 Screen 中移除重复 Application Container；
- 将 Layout Relation Graph 前置；
- 让 Architecture Plan 引用全局 App 和关系图；
- 增加全局/页面所有权验证。

### 第二轮：外观契约拆分

- 新增 Native Appearance Plan；
- 从 Native Layout Plan 迁移纯视觉字段；
- 保持 Typography Metrics 与 Layout 的显式引用；
- 增加 SwiftUI/UIKit 外观消费清单；
- 保留旧计划兼容读取。

### 第三轮：控件映射闭环

- 扩展 Control Configuration Plan；
- 增加 Semantic Candidate、Geometry Fit 和 Final Decision；
- 建立系统控件、包装控件和自定义控件的可审计 fallback；
- 实现最多两轮的局部适配求解；
- 增加父级上下文角色验证。

### 第四轮：交互与动画归一化

- 新增 Native Interaction and Motion Plan；
- 统一 route、presentation、local state、contextual action 和 containment；
- 固化行为 owner、executor、transition 和 timing；
- 让 SwiftUI/UIKit 消费同一份行为计划。

### 第五轮：生成器和门禁收口

- 移除生成器中的重复架构猜测；
- 补齐跨计划哈希和消费验证；
- 验证已有工程入口不被覆盖；
- 运行 SwiftUI/UIKit 类型检查；
- 完成真实 HTML 全流程回归。

## 测试计划

- Schema 单元测试；
- Application/Screen 唯一所有权测试；
- Layout Relation Graph 权威来源测试；
- Table/Collection/Scroll 容器选择测试；
- 系统控件 Geometry Fit 测试；
- Wrapper/Custom fallback 测试；
- Typography 度量一致性测试；
- 圆角、边框、背景、阴影和媒体外观测试；
- 路由、Tab、Presentation 和局部状态测试；
- 动画 owner 和 timing 测试；
- SwiftUI/UIKit 生成代码类型检查；
- 新建工程和已有工程接入测试；
- 不启用截图时的完整核心流程测试；
- 显式 visual 模式下的可选截图验收测试。

## 风险与控制

### 计划数量增加

控制方式：每个计划只负责一种权威事实，通过 ID 和哈希引用，禁止复制完整节点定义。

### 控件和布局产生循环依赖

控制方式：先生成语义候选，再执行 Geometry Fit，最多局部求解两轮，最终形成不可变决策。

### 生成过多自定义 View

控制方式：保持系统控件优先和最小充分容器原则，只有明确阻断证据才生成包装或自定义控件。

### Schema 漂移

控制方式：所有计划具有独立 validator、版本号、输入哈希和生成后消费证据。

### 已有工程被污染

控制方式：Application Plan 明确 existing/generated ownership；现有入口、Router、Coordinator 和人工文件默认只读。

## 验收标准

架构微调完成后必须满足：

1. 相同输入稳定生成相同计划和代码。
2. 全局 Tab/Navigation 不在多个 Screen 中重复。
3. Table、Collection、Scroll 的选择具有确定性证据。
4. 每个控件映射包含上下文和 Geometry Fit 结果。
5. 圆角、阴影、字体和背景不依赖截图补齐。
6. 路由、弹层、局部状态和动画拥有唯一执行者。
7. SwiftUI/UIKit 消费相同结构、布局、外观和行为契约。
8. 无多模态能力时能够完成完整核心转换。
9. 截图只用于最终可选验收，不参与首次架构和样式决策。
10. 现有工程入口、Router 和人工代码不会被自动覆盖。

## 结论

本方案不是增加更多 UI 层级，也不是推倒现有 Skill。调整重点是：

1. 将 Application Architecture 提升为全局唯一计划；
2. 保持六层所有权结构；
3. 将布局、外观、控件配置和行为变成清晰的横向执行契约；
4. 建立“语义候选、几何适配、最终决策”的控件映射闭环；
5. 让 SwiftUI/UIKit 只消费经过验证的统一计划；
6. 使用确定性结构门禁保证首次生成质量，继续把截图保留为可选验收兜底。
