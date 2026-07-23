# UIKit Rules

仅在选择 UIKit + Swift 时读取。优先复用项目的 ViewController、View、Coordinator、Design System 和约束库。

## 结构选择

先读取 UI IR 的 `semanticType` 与 `nativeMapping.uiKit`。置信度高于或等于 `0.7` 时以该原生语义为起点，再根据项目组件库调整；低于 `0.7` 时先复核。

- 页面入口：项目现有 `UIViewController` 基类或 `UIViewController`。
- 普通布局：Auto Layout。
- 项目已经稳定使用 SnapKit 时可以沿用；否则使用原生 anchors。
- 重复列表：`UITableView` 或 `UICollectionView`。
- Grid、横向列表和复杂重复布局：`UICollectionViewCompositionalLayout` 或项目已有布局。
- 内容滚动：`UIScrollView`，确保 content/layout guides 约束完整。

不要使用固定 screen frame 代替 Auto Layout。绝对位置只用于源页面确实脱离文档流的局部节点。

按钮、输入、选择和状态控件优先使用 UIButton、UITextField、UITextView、UIControl、UISwitch 等保留原生语义的类型。自定义外观时不要退化成普通 UIView + UITapGestureRecognizer。

## View 组织

- 静态页面可以由 ViewController + root View 构成，不强制 ViewModel。
- 有业务状态时沿用项目架构。
- 可复用、独立状态、复杂绘制或重复 cell 才拆分独立 View。
- View 暴露语义化配置与事件，不暴露大量内部控件。

## Auto Layout

- 结构化恢复 leading/trailing/top/bottom、center、尺寸、比例和 min/max 约束。
- content-driven 文本和按钮依赖 intrinsic content size，并设置正确 hugging/compression priorities。
- 百分比宽高使用 multiplier。
- 固定底栏约束到 safeAreaLayoutGuide 或页面底部，依据 IR systemChrome 决定。
- `UIScrollView` 内容容器横向必须与 frameLayoutGuide 同宽，避免错误横向滚动。

## 文本

- `UILabel` 设置 font、textColor、numberOfLines、textAlignment、lineBreakMode。
- 精确 line-height、kern 和富文本使用 `NSAttributedString` paragraph style。
- 输入控件保留 keyboard type、secure entry、placeholder、disabled 和焦点顺序。
- 验证中英文和数字混排的 baseline 与换行。

## Layer

- border、corner radius、shadow、mask 和 gradient 分层处理。
- `masksToBounds = true` 会裁掉外部 shadow；需要圆角裁剪和阴影时使用外层 shadow container + 内层 clipped content。
- spread shadow 使用 `shadowPath`；多重或 inset shadow 使用额外 layer。
- gradient layer 在 `layoutSubviews` 中同步 bounds，避免初始 frame 为零。
- 四角不同按最低系统版本使用 `maskedCorners` 或 `UIBezierPath`。

## Safe Area 与导航

- 不复刻 HTML 展示用系统状态栏、信号、电池和 Home Indicator。
- 接入已有 `UINavigationController`、Coordinator 或 Router。
- 自定义导航栏时按项目方式隐藏系统 navigation bar，避免双导航栏。
- full-screen、page sheet、popover 和 overlay 按交互图分类，不用一个 `present` 处理全部语义。

## 事件

- 控件事件使用 target-action、closure 或项目已有绑定方式。
- 可点击卡片优先使用 `UIControl` 或明确手势；不要给整个页面滥加 gesture recognizer。
- 每个交互节点设置：

```swift
view.accessibilityIdentifier = "home.search.button"
```

## 复用依赖

- SnapKit、Kingfisher、SDWebImage 等仅在项目已经使用且目标模块允许时复用。
- 不为一次转换增加等价的新依赖。
- 图片加载保留占位、裁剪和缓存语义，但真实接口行为仍按项目数据层处理。
