# HTML to Native Control Mapping

本文件定义 HTML 视图/控件到 SwiftUI 与 UIKit 的语义映射。先识别语义，再选择原生控件，最后还原外观；不要只按 tag 机械翻译。

本表负责常见 HTML 语义。系统组件全类别目录见 `native-component-catalog.md`；导航、呈现和 child controller 见 `navigation-presentation-containment.md`；系统无直接对应组件时遵循 `custom-component-fallback.md`；所有候选都必须执行 `sdk-availability-policy.md`。

## 目录

1. 判定优先级
2. 容器与布局
3. 文本、链接与按钮
4. 输入与表单
5. 选择、数值与状态控件
6. 列表、表格与集合
7. 导航、弹层与反馈
8. 图片、媒体与特殊内容
9. 原生默认外观处理
10. 状态与无障碍
11. 置信度与降级

## 1. 判定优先级

按以下证据从高到低判定 `semanticType` 和原生控件：

1. HTML 原生语义：`button`、`input`、`select`、`textarea`、`nav`、`table` 等。
2. `role`、`aria-*`、`type`、`disabled`、`checked`、`selected` 等属性。
3. 可观察交互：submit、href、点击、切换、拖动、展开收起、焦点和键盘行为。
4. 结构与重复模式：列表、Tab、卡片集合、导航栏、工具栏。
5. class/id 命名线索。
6. 视觉样式和位置。

发生冲突时，高优先级证据胜出，并在 UI IR 的 `nativeMapping.rationale` 中记录原因。纯视觉推断不得给出高置信度。

## 2. 容器与布局

| HTML / 运行时特征 | semanticType | SwiftUI | UIKit | 说明 |
|---|---|---|---|---|
| 普通块级容器 | `container` | `VStack`/`HStack`/`ZStack` | `UIView` + Auto Layout | 根据 computed display/flex/position 决定，不按 `div` 固定映射 |
| `header` 页面顶部 | `header` | 结构化 View | `UIView` | 不自动等于系统导航栏 |
| `nav` 页面主导航 | `navigation` | Toolbar/自定义导航 | `UINavigationBar`/自定义 View | 结合位置和交互决定导航栏、Tab 或普通链接组 |
| `footer` | `footer` | 结构化 View | `UIView` | 固定底部时放在滚动区域外 |
| overflow scroll/auto | `scroll` | `ScrollView` | `UIScrollView` | 只让真实溢出的容器滚动 |
| 固定重复行列 | `grid` | `Grid`/`LazyVGrid` | `UICollectionView` | 静态少量内容可用普通布局 |
| `hr` 或细分隔元素 | `divider` | `Divider`/Rectangle | `UIView` | 精确保留颜色、厚度和 inset |
| 纯间距节点 | `spacer` | `Spacer`/spacing | constraint constant | 只有确实承担布局间距时才生成 |

## 3. 文本、链接与按钮

| HTML / 语义 | semanticType | SwiftUI | UIKit | 注意事项 |
|---|---|---|---|---|
| `h1`–`h6` | `heading` | `Text` | `UILabel` | 保留 heading 无障碍层级 |
| `p`、有文本的 `span` | `text` | `Text` | `UILabel` | 富文本使用 AttributedString/NSAttributedString |
| `a[href]` 内部页面 | `link` | `Button`/`NavigationLink` | `UIButton`/UIControl + Router | 导航语义由目标决定，不因蓝色外观才算链接 |
| `a[href]` 外部 URL | `link` | `Link` 或项目 Web 路由 | `UIApplication.open`/项目 Web 容器 | 不把外部链接误做 push |
| `button` | `button` | `Button` | `UIButton` | 自定义 HTML 外观时使用 plain/custom style |
| `input[type=button/submit/reset]` | `button` | `Button` | `UIButton` | submit 绑定表单 action；reset 恢复本地表单状态 |
| `div/span` + click/role=button | `button` | `Button` | `UIControl`/`UIButton` | 保留原生按钮语义和点击区域，不能只加手势 |
| 仅图标的可点击节点 | `icon-button` | `Button` + Image | `UIButton` | 必须有 accessibility label |
| 文本标签 `label` | `label` | `Text` | `UILabel` | 与输入控件建立语义关联 |

按钮视觉还原时，保留 Button/UIButton 的交互和无障碍语义，但移除不属于 HTML 的默认 padding、tint、背景和高亮样式。

## 4. 输入与表单

| HTML | semanticType | SwiftUI | UIKit | 原生配置 |
|---|---|---|---|---|
| `input[type=text]` | `text-input` | `TextField` | `UITextField` | 普通文本键盘 |
| `email` | `text-input` | `TextField` | `UITextField` | emailAddress keyboard/content type |
| `url` | `text-input` | `TextField` | `UITextField` | URL keyboard/content type |
| `tel` | `text-input` | `TextField` | `UITextField` | phonePad/content type |
| `password` | `secure-input` | `SecureField` | `UITextField(isSecureTextEntry)` | 不把密码写入日志或截图报告 |
| `search` | `search-input` | `TextField` 或 `.searchable` | `UISearchTextField`/`UISearchController` | 页面级搜索才优先系统 search controller |
| `number` | `number-input` | `TextField` + 数字解析 | `UITextField` | decimalPad/numberPad；保留 min/max/step |
| `date/time/datetime-local` | `date-input` | `DatePicker` | `UIDatePicker` | 保留模式和最小最大值 |
| `textarea` | `text-area` | `TextEditor` | `UITextView` | placeholder 需自定义处理 |
| `contenteditable` | `text-area` | `TextEditor` | `UITextView` | 只有可编辑区域才映射，不迁移编辑器插件逻辑 |
| `input[type=file]` | `file-input` | Button + fileImporter | `UIDocumentPickerViewController` | 不伪造浏览器文件路径 |
| `form` | `form` | 结构容器 + submit action | 容器 + action | 不生成额外视觉容器，除非 HTML 本身有样式 |

输入控件保留 placeholder、value、required、readonly、disabled、autocomplete、maxlength、pattern、焦点顺序和提交行为。HTML 校验可以转成本地校验，服务端校验不能凭空补全。

## 5. 选择、数值与状态控件

| HTML / 语义 | semanticType | SwiftUI | UIKit | 注意事项 |
|---|---|---|---|---|
| checkbox | `checkbox` | `Toggle` + checkbox style 或 Button 状态控件 | 自定义 `UIControl`/`UIButton` | checkbox 不等同于 switch；外观与语义分别保留 |
| switch 语义/role=switch | `switch` | `Toggle` | `UISwitch` | 只有明确 switch 语义时使用系统开关 |
| `input[type=radio]` | `radio` | 单个自定义 radio option | 单个自定义 UIControl | 同一 `name` 的 radio 通过 `state.groupName` 组成 `radio-group`，不能把每个节点都当成整个组 |
| radio group 容器 | `radio-group` | Picker/自定义单选组 | 自定义 UIControl 组 | 不能默认压成 segmented control |
| 明确分段选择器 | `segmented-control` | `Picker(.segmented)` | `UISegmentedControl` | 仅布局和交互都符合分段控件时使用 |
| `select` 单选 | `select` | `Picker`/Menu | `UIMenu`/`UIPickerView` | 根据选项数量和原型展开方式选择 |
| `select[multiple]` | `multi-select` | 多选列表 | table/collection 多选 | 不使用单值 Picker |
| `input[type=range]` | `slider` | `Slider` | `UISlider` | 保留 min/max/step/value |
| 数值加减组合 | `stepper` | `Stepper` 或自定义组合 | `UIStepper` 或自定义组合 | 视觉明显自定义时保留组合结构 |
| `input[type=color]` | `color-picker` | `ColorPicker` | `UIColorWell` | 受最低 iOS 版本约束 |
| `progress` | `progress` | `ProgressView` | `UIProgressView` | 线性/不确定态分开 |
| `meter` | `meter` | 自定义 Gauge/ProgressView | 自定义 View | 保留 low/high/optimum 语义 |
| `details/summary` | `disclosure` | `DisclosureGroup` 或自定义 | 展开折叠 UIControl | 保留 open 初始状态和动画 |

`summary` 单独作为 `disclosure-trigger`，由它控制父级 `disclosure`。类似地，`role=tab` 是 `tab-item`，`role=tablist` 才是 `tab-control`；`role=menuitem` 是 `menu-item`，不是整个菜单。

## 6. 列表、表格与集合

| HTML / 模式 | semanticType | SwiftUI | UIKit | 说明 |
|---|---|---|---|---|
| `ul/ol` 简单静态内容 | `list` | VStack/LazyVStack | UIStackView/自定义 View | 不为三个静态文本强制上 List/TableView |
| 长列表或重复数据项 | `list` | LazyVStack/List | UITableView/UICollectionView | 依据原型样式决定是否使用系统 List |
| `li` | `list-item` | 行 View | cell/content view | 重复结构生成可复用组件 |
| 横向重复卡片 | `carousel` | horizontal ScrollView/LazyHStack | UICollectionView | 保留 measured item width、gap、nowrap/compression；snap/page 行为单独处理 |
| `table` 数据表 | `data-table` | Grid/自定义滚动表格 | UICollectionView/自定义表格 | 不用 UITableView 假装多列表格 |
| CSS Grid 卡片 | `grid` | LazyVGrid | UICollectionView | 保留列宽、gap 和自适应规则 |
| 分组列表 | `sectioned-list` | Section + List/LazyVStack | table/collection sections | 保留 header、footer、sticky 语义 |

## 7. 导航、弹层与反馈

| HTML / 行为 | semanticType | SwiftUI | UIKit |
|---|---|---|---|
| 顶部应用导航栏 | `navigation-bar` | toolbar/自定义 View | UINavigationBar/自定义 View |
| 底部主 Tab | `tab-bar` | TabView/自定义 Tab | UITabBarController/自定义 Tab |
| 页面内 Tab | `tab-control` | Picker/自定义状态切换 | UISegmentedControl/自定义 Tab |
| modal 全屏 | `modal` | fullScreenCover | fullScreen present |
| 半屏选择/表单 | `sheet` | sheet | pageSheet |
| alert/confirm | `alert` | alert/confirmationDialog | UIAlertController |
| toast/snackbar | `toast` | overlay + transition | overlay View |
| popover/menu | `menu` | Menu/popover | UIMenu/UIPopoverPresentationController |
| loading overlay | `loading` | ProgressView overlay | UIActivityIndicatorView overlay |

导航呈现方式根据页面关系和视觉层级判断，不能只根据 `window.open`、`position:fixed` 或 class 名决定。

`UIViewController`、`UINavigationController`、`UITabBarController` 等是页面或容器所有者，不作为普通 `div` 的逐节点映射。只有页面关系或生命周期证据成立时才生成 controller。

## 8. 图片、媒体与特殊内容

| HTML | semanticType | SwiftUI | UIKit | 支持级别 |
|---|---|---|---|---|
| `img` | `image` | Image/项目图片组件 | UIImageView/项目图片组件 | native |
| 小型 SVG 图标 | `icon` | Image/Shape | UIImage/CAShapeLayer | native 或 native-fallback |
| 复杂 SVG/插画 | `image` | 矢量资源 | UIImageView 矢量资源 | native-fallback |
| `video` | `video` | VideoPlayer/项目播放器 | AVPlayerViewController | native-fallback；业务播放逻辑不自动补全 |
| `audio` | `audio` | 项目播放器控制 | AVAudioPlayer/AVPlayer | native-fallback |
| `canvas` 纯装饰 | `canvas-artwork` | 合法位图/Canvas | UIImage/Core Graphics | native-fallback |
| `canvas` 交互应用/WebGL | `unsupported-web-content` | 不自动转换 | 不自动转换 | unsupported |
| `iframe/embed/object` | `embedded-content` | 按具体内容决定 | 按具体内容决定 | 默认 unsupported，禁止直接整页 WKWebView |
| 地图 | `map` | MapKit Map | MKMapView | 只有可识别地图语义时 |

## 9. 原生默认外观处理

原生控件常自带 HTML 没有的 padding、tint、圆角、focus ring 或列表背景。处理顺序：

1. 保留原生控件语义、状态和可访问性。
2. 去除与 HTML 不一致的默认皮肤。
3. 按 computed style 重建背景、边框、圆角、字体和 pressed/disabled 状态。
4. 不影响键盘、焦点、点击区域和系统辅助功能。

SwiftUI 自定义按钮通常使用 `.buttonStyle(.plain)` 后重建样式；UIKit 使用 `UIButton.Configuration` 或 custom UIControl，但不要退化成只有 `onTapGesture`/`UITapGestureRecognizer` 的普通 View。

## 10. 状态与无障碍

UI IR 应保留：

- enabled/disabled、selected、checked、expanded、focused、readonly
- normal/pressed/hover/focus/disabled 的视觉状态
- label、value、hint、role 和 heading level
- 命中区域和焦点顺序
- keyboard type、content type 和 submit label

hover 在 iPhone 没有直接等价。若只用于视觉反馈可忽略；若承载必要信息，转换成点击、菜单或长按入口并报告。

## 11. 置信度与降级

每个节点写入：

- `nativeMapping.swiftUI`
- `nativeMapping.uiKit`
- `nativeMapping.styleStrategy`
- `nativeMapping.confidence`
- `nativeMapping.rationale`

建议置信度：

- `0.9–1.0`：原生 tag/type/role 明确。
- `0.7–0.89`：交互和结构明确，但 tag 为通用容器。
- `0.5–0.69`：主要依靠命名和视觉模式。
- `<0.5`：不要自动定型，标记 `custom` 或 `unsupported` 并请求确认。

降级只能选择局部原生绘制、合法资源或明确占位。禁止整页截图和 `WKWebView` 伪转换。
