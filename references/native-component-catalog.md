# iOS Native Component Catalog

本目录用于保证 HTML 到 iOS 的组件选择覆盖完整、可维护。它覆盖“用户可见并能承载页面语义”的系统组件；配置对象、布局属性对象、过渡上下文等辅助类型不作为 HTML 节点映射目标。

## 使用规则

1. 先确定 HTML 语义和交互，再在本目录中选择组件。
2. 优先复用目标工程已有组件，其次使用系统组件，再考虑组合或自定义实现。
3. 同一语义可以有多个候选，必须结合数据量、展开方式、平台尺寸和最低系统版本选择。
4. 生成前运行 `scripts/inspect_ios_sdk.py`，不得只凭目录中的类名断言 API 可用。
5. UIKit 与 SwiftUI 不要求逐类一一对应。SwiftUI 缺少直接组件时，可以组合 View，或通过 representable 接入 UIKit。

## 基础视图与布局

| Web 语义 | SwiftUI | UIKit | 选择条件 |
|---|---|---|---|
| 普通容器 | `VStack` / `HStack` / `ZStack` | `UIView` | 根据 flex、层叠和文档流选择 |
| 自动排列 | Stack / `Grid` | `UIStackView` | 少量、稳定子视图 |
| 滚动容器 | `ScrollView` | `UIScrollView` | 内容真实溢出 |
| 长列表/网格 | `List` / Lazy stacks / Lazy grid | `UICollectionView` / `UITableView` | 数据驱动和 cell 复用 |
| 分隔线 | `Divider` / `Rectangle` | `UIView` | 保留厚度和 inset |
| 模糊/材质 | Material / visual effect wrapper | `UIVisualEffectView` | 原型确有模糊层，且版本允许 |
| 空态 | `ContentUnavailableView` 或自定义 | `UIContentUnavailableView` 或自定义 | 受最低 iOS 版本约束 |

## 文本、图像与媒体

| Web 语义 | SwiftUI | UIKit / 系统框架 |
|---|---|---|
| 单行/多行文本 | `Text` | `UILabel` |
| 富文本 | `Text` / `AttributedString` | `UILabel`/`UITextView` + `NSAttributedString` |
| 图片 | `Image` / 项目图片组件 | `UIImageView` |
| 视频播放 | `VideoPlayer` | `AVPlayerViewController` / `AVPlayerLayer` |
| 音频播放 | 项目播放器 View | `AVPlayer` / `AVAudioPlayer` + 自定义控制条 |
| 地图 | `Map` | `MKMapView` |
| PDF/文件预览 | Quick Look 接入 | `QLPreviewController` |
| 网页链接 | `Link` / 项目 Web 路由 | `SFSafariViewController` 或 `UIApplication.open` |

## 输入与 UIControl

`UIControl` 是交互控件基类，不是所有点击节点的默认最终实现。优先选择具体子类；只有系统没有匹配语义、但仍需要 target-action、状态和无障碍行为时，才自定义 `UIControl`。

| HTML / 产品语义 | SwiftUI | UIKit |
|---|---|---|
| 普通/图标按钮 | `Button` | `UIButton` |
| 文本输入 | `TextField` | `UITextField` |
| 密码输入 | `SecureField` | `UITextField` + secure entry |
| 多行编辑 | `TextEditor` | `UITextView` |
| 搜索框 | `.searchable` / `TextField` | `UISearchController` / `UISearchTextField` / `UISearchBar` |
| 二元开关 | `Toggle` | `UISwitch` |
| checkbox | checkbox style 或自定义 `Toggle` | 自定义 `UIControl`/`UIButton` |
| radio | `Picker` 或自定义 option | 自定义 `UIControl` 组 |
| 分段选择 | segmented `Picker` | `UISegmentedControl` |
| 连续数值 | `Slider` | `UISlider` |
| 增减数值 | `Stepper` | `UIStepper` |
| 日期/时间 | `DatePicker` | `UIDatePicker` |
| 滚轮选择 | wheel `Picker` | `UIPickerView` |
| 颜色值 | `ColorPicker` | `UIColorWell` / `UIColorPickerViewController` |
| 页码指示 | page-style `TabView` / 自定义 | `UIPageControl` |
| 进度 | `ProgressView` | `UIProgressView` |
| 加载中 | indeterminate `ProgressView` | `UIActivityIndicatorView` |
| 下拉刷新 | `.refreshable` | `UIRefreshControl` |
| 粘贴按钮 | `PasteButton` | `UIPasteControl` |

## 集合、表格与重复内容

| 场景 | 首选 | 不应选择的情况 |
|---|---|---|
| 单列、系统行风格 | SwiftUI `List` / `UITableView` | 多列数据表、自由卡片瀑布流 |
| 网格、横向卡片、复杂组合布局 | Lazy grid / `UICollectionView` | 三五个完全静态的小元素 |
| 多列数据表 | `Grid` 或自定义布局 / compositional collection | 不要用普通 table view 假装多列表格 |
| 大量可复用行 | Lazy container / table or collection | 不要展开成大量独立固定 View |
| 轮播分页 | page `TabView` / `UICollectionView` | 只有静态横向排列且不可滚动 |

UIKit 实现优先考虑 diffable data source、cell/supplementary registration 和 compositional layout，但必须沿用项目已有数据源方式；不要为了使用新 API 重写现有模块。

## Bars、菜单与系统反馈

| 语义 | SwiftUI | UIKit |
|---|---|---|
| 导航栏 | `.toolbar` / navigation title | `UINavigationBar` + `UINavigationItem` |
| 工具栏 | `.toolbar` | `UIToolbar` + `UIBarButtonItem` |
| 主 Tab | `TabView` / `Tab` | `UITabBarController` + `UITabBar` |
| 操作菜单 | `Menu` | `UIMenu` + `UIAction` |
| 上下文菜单 | `.contextMenu` | `UIContextMenuInteraction` |
| 编辑菜单 | 系统文本编辑行为 | `UIEditMenuInteraction` |
| alert | `.alert` | `UIAlertController(.alert)` |
| action sheet | `.confirmationDialog` | `UIAlertController(.actionSheet)` |
| popover | `.popover` | `UIPopoverPresentationController` |
| sheet | `.sheet` + presentation modifiers | `UISheetPresentationController` |
| toast/snackbar | 自定义 overlay | 自定义 overlay `UIView`，UIKit 无统一系统 toast |

## 内容与选择控制器

以下是页面行为映射目标，不是普通内嵌 View：

| 需求 | UIKit / Apple 框架 | SwiftUI 路径 |
|---|---|---|
| 分享 | `UIActivityViewController` | representable 或项目封装 |
| 文件选择 | `UIDocumentPickerViewController` | `fileImporter` / representable |
| 文件浏览 | `UIDocumentBrowserViewController` | representable |
| 图片/视频选择 | `PHPickerViewController` | `PhotosPicker` |
| 拍照/录像 | `UIImagePickerController` 或 AVFoundation 自定义相机 | representable / 项目相机 |
| 颜色选择 | `UIColorPickerViewController` | `ColorPicker` / representable |
| 字体选择 | `UIFontPickerViewController` | representable |
| 打印 | `UIPrintInteractionController` | representable |
| Quick Look | `QLPreviewController` | quick-look modifier / representable |
| 浏览网页 | `SFSafariViewController` | representable 或系统 URL 打开 |
| 联系人选择 | `CNContactPickerViewController` | representable |
| 邮件/短信 | `MFMailComposeViewController` / `MFMessageComposeViewController` | representable |
| 日历事件 | `EKEventEditViewController` | representable |

使用跨框架控制器前必须检查 import、隐私权限、entitlement、设备能力和项目最低版本。HTML 只有“上传图片”时，不得自动推断为相机；根据 `accept`、`capture` 和实际 UI 决定。

## 容器与页面控制器

| 页面关系 | SwiftUI | UIKit |
|---|---|---|
| 层级栈 | `NavigationStack` / `NavigationSplitView` | `UINavigationController` |
| 主 Tab | `TabView` | `UITabBarController` |
| 主从栏 | `NavigationSplitView` | `UISplitViewController` |
| 翻页 | page `TabView` | `UIPageViewController` |
| 搜索结果覆盖 | `.searchable` + state | `UISearchController` |
| 自定义复合页面 | 组合 View | 自定义 container `UIViewController` |

具体 push、present、sheet 和 child containment 规则见 `navigation-presentation-containment.md`。

## 手势与交互能力

Web click、long press、drag、swipe、pinch、hover 可分别映射到 SwiftUI gestures 或 UIKit 的 tap、long-press、pan、swipe、pinch、hover/pointer interaction。手势识别器是行为实现，不替代 Button、Switch、Slider 等有明确语义的系统控件。

## 禁止新生成的废弃目标

除非维护既有遗留代码，不生成：

- `UIAlertView`、`UIActionSheet`：改用 `UIAlertController`。
- `UIWebView`：外部网页用 `SFSafariViewController`，合法的局部 Web 内容才评估 `WKWebView`；本技能禁止整页嵌入伪转换。
- `UISearchDisplayController`：改用 `UISearchController`。
- `UIPopoverController`：改用 `popoverPresentationController`。
- `UIMenuController`：新编辑菜单优先 `UIEditMenuInteraction`，同时遵循项目最低版本。
- 已被当前 SDK 标注 deprecated/unavailable 的其他 API。

## 覆盖边界

“全面映射”表示每类用户界面语义都有系统首选、项目组件、自定义实现或 unsupported 结论，不表示把 UIKit 头文件里的每个 `UI*` 辅助类硬塞进映射表。发现目录未覆盖的新系统组件时：

1. 由 SDK 扫描确认符号和可用性；
2. 归入现有语义类别，或新增一个稳定类别；
3. 定义最低版本与降级项；
4. 增加测试样例后再允许自动选择。
