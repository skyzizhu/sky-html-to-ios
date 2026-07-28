# Six-Layer Native Architecture

HTML 转原生代码必须先完成六层架构规划，再生成节点。六层从生命周期所有者逐步收敛到最小视觉组件，禁止跳过中间层直接把 DOM 递归翻译成 View。

## 1. Application Container

确定 App 级页面容器及其所有权：

- SwiftUI `NavigationStack`、`TabView`、`NavigationSplitView`；
- UIKit `UINavigationController`、`UITabBarController`、`UISplitViewController`、`UIPageViewController`；
- 已有项目 Router、Coordinator 或自定义容器；
- 每个主 Tab 独立持有导航栈；
- 已有工程中的 inherited navigation 不再重复包装。

页面内 segmented control 不得升级为 App Tab。普通 HTML 容器不得生成 Controller。

## 2. Screen Container

每个 screen 对应一个完整页面所有者：

- SwiftUI `View`；
- UIKit `UIViewController`；
- 有明确生命周期需求时的 custom container controller；
- UIKit child containment 必须执行完整 `addChild`/`didMove` 和移除生命周期。

Screen 负责状态、页面级事件、Safe Area 所有权、键盘避让和 presentation，不负责 App 级 Tab 生命周期。

## 3. Screen Regions

将页面分为互不重复计算 inset 的持久区域：

- system/custom top bar；
- content；
- fixed/sticky/scroll-away bottom region；
- floating action region；
- overlay；
- sheet、modal、popover 和 alert presentation。

系统导航栏与自绘顶部栏是独立决策。系统 TabBar 成立后，来源 HTML 中对应的自绘 TabBar 节点必须从 screen 内容树移除。

## 4. Content Container

内容容器必须按可观察结构和滚动证据选择：

| 证据 | SwiftUI | UIKit |
|---|---|---|
| 无溢出的少量静态内容 | Stack/Grid | `UIView`/`UIStackView` |
| 单一连续内容真实纵向溢出 | `ScrollView` | `UIScrollView` |
| 单列、重复、长列表 | `List`/`LazyVStack` | `UITableView` |
| 网格、横向卡片、数据表 | `LazyVGrid`/`LazyHStack` | `UICollectionView` |
| 多种独立 Section 布局 | Lazy section composition | `UICollectionViewCompositionalLayout` |

选择规则：

- 少量静态节点不为了“原生感”强制使用 Table/Collection；
- 五个以上同构行、动态重复模板或 sectioned list 优先复用容器；
- 横向轮播优先 Collection，不让根页面获得 horizontal 轴；
- 多列数据表不得用普通 `UITableView` 假装多列；
- `overflow:hidden` 只代表裁剪，不能单独证明滚动；
- 页面根仅在计划为 `scroll-view` 时增加外层滚动容器；
- Table/Collection 自己拥有滚动轴，禁止再套同轴根 ScrollView。

## 5. Reusable Section And Item

Table/Collection 计划必须保存：

- section ID、类型和来源节点；
- header/footer 节点；
- item 节点顺序；
- item template；
- item 数量；
- 滚动轴；
- Cell 类型；
- 是否使用复用；
- 未来可接入项目 diffable data source 或现有数据源的边界。

当前 HTML 可见内容是视觉 fixture，不据此生成网络接口、分页器或业务 ViewModel。Cell 内仍保留 source node ID 以供几何和截图追踪。

## 6. Leaf Component

每个最终叶子节点必须获得明确的 SwiftUI/UIKit 类型、样式策略、交互性、无障碍 ID、置信度和理由。

### 基础小颗粒视图

| 语义 | SwiftUI | UIKit |
|---|---|---|
| 空容器/背景块 | `Color`/custom `View` | `UIView` |
| 文本/标题 | `Text` | `UILabel` |
| 可选择只读文本 | selectable `Text` | non-editable `UITextView` |
| 图片/缩略图 | `Image` | `UIImageView` |
| 图标 | `Image`/Shape/SF Symbol | `UIImageView`/`CAShapeLayer` |
| 分隔线 | `Divider`/`Rectangle` | `UIView` |
| 弹性间距 | `Spacer` | layout spacer `UIView` |
| 装饰层/伪元素 | Shape/Canvas | `UIView`/`CALayer` |
| 静态 Canvas 图稿 | Canvas/Image | Core Graphics/`UIImageView` |

### 交互和输入

- `Button` / `UIButton` / `UIControl`；
- `TextField` / `UITextField`；
- `SecureField` / secure `UITextField`；
- `TextEditor` / `UITextView`；
- `Toggle` / `UISwitch`；
- checkbox、radio、segmented control；
- Picker、Menu、Slider、Stepper、DatePicker、ColorPicker；
- ProgressView、UIProgressView、UIActivityIndicatorView；
- disclosure、file importer 和系统 controller 入口。

### 决策约束

- `project-component` 优先于通用系统映射；
- 有子节点的复合按钮保留图标、文字、角标和尾部信息顺序；
- 普通展示文本默认使用 Label，不因多行自动升级为 TextView；
- 只有编辑、选择或内部滚动行为成立时使用 TextView；
- 无系统对应控件时依次选择项目组件、系统组合、自定义 View/UIControl、Layer/Core Graphics；
- 不允许用截图或 WebView 代替叶子组件；
- unsupported 节点必须显式报告。

## Plan Contract

`native-architecture-plan-1.1` 的每个 screen 必须包含：

```text
layers.applicationContainer
layers.screenContainer
layers.screenRegions
layers.contentContainer
layers.reusableContent
layers.leafComponents
```

代码生成器必须校验六层完整性，并消费 `contentContainer.nodeStrategies`。缺层、重复滚动所有权或未解决的低置信度核心容器不得进入正式生成。
