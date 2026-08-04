# Responsive Auto Layout

HTML 截图提供基准视觉，iOS 实现使用 Auto Layout/SwiftUI layout semantics。禁止把整个页面放进 `scaleEffect` 或按设备宽度实时整体缩放。

## 来源分类门禁

在创建新工程或生成代码前，对每个独立页面范围在 320、375、393、430px 运行实测分类：

- `responsive-document` / `responsive-mobile-root`：根容器宽度随 viewport 变化，覆盖主要移动宽度，且 document horizontal overflow 不超过容差。viewport meta、width media query 和布局几何变化属于支持证据，但不能单独替代实测。
- `fixed-mobile-artboard`：固定在 280-500px 的竖向应用画板；允许一次性设计 token 归一化。
- `desktop-only`：移动 viewport 下仍保留大于 500px 的自然根宽、明显 `min-width` 或超过 12px 的 document horizontal overflow。不得缩放成手机页面。
- `ambiguous-responsive-source`：未发现明确手机画板，且根宽、覆盖率或响应式证据不足。停止自动生成并确认页面根、移动 breakpoint 或是否允许适配设计。

响应式网站没有手机外壳时，使用语义 app root、`main`/`[role=main]`，最后才使用 `body`。不得因为某个普通卡片恰好为 393px 宽就将它当作完整 screen。

## 两类来源

### 原生响应式页面

当应用根宽随浏览器 viewport 同步变化时，基准通常使用 `1 CSS px = 1 iOS pt`。在多个 viewport 采样 computed layout，用变化趋势判断 pin、stretch、intrinsic、比例和 min/max。

### 固定缩小画板

桌面展示板中的手机常被缩小，例如应用根宽只有 318px，但实际目标设备为 393pt。先计算一次：

```text
designScale = targetBaselineWidthPt / sourceAppRootWidthCssPx
```

将字号、边距、圆角和固定控件尺寸转换为基准 token；随后用原生约束布局，运行时不再整体缩放。模拟状态栏、刘海和 Home Indicator 不参与等比换算，由 iOS Safe Area 接管。

## Safe Area 与容器尺寸

Safe Area 是内容避让信息，不是设备画布尺寸。滚动页面先让 `ScrollView`/`UIScrollView` 铺满父容器，再由系统在内容层形成可见 inset：

```text
scrollFrame = parentBounds
adjustedContentInset = systemSafeArea + nativeSystemChrome + customBarInsetOnce
```

禁止生成 `scrollHeight = viewportHeight - safeAreaTop - safeAreaBottom` 或对应宽度公式。这样会在 SwiftUI/UIKit 自动适配后重复扣减，并导致背景、滚动指示器、吸顶栏和底部弹层坐标错误。普通非滚动内容是否约束到 `safeAreaLayoutGuide` 仍按视觉所有权判断；这个规则不等于所有内容都忽略 Safe Area。

## 多宽度分析

```bash
NODE_PATH=<playwright-node-modules> node "$SKILL_ROOT/scripts/analyze_responsive_layout.cjs" \
  --html <entry.html> \
  --selector <app-root> \
  --widths 320,375,393,430 \
  --baseline-width 393 \
  --out responsive-layout.json
```

工具自动区分 viewport 与 fixed-artboard，按目标宽度探测节点相对父容器的位置、宽高响应和文字行数，输出 Auto Layout 建议。

分析结果还必须保留每个宽度下可见节点的 selector/runtime ID、rect、parent rect 和文字行数快照，使 Grid/Collection 降级阶段能确定真实列数和异构 item 尺寸。截图目录仍是可选项；这些结构化浏览器测量不依赖截图或多模态模型。

CSS Grid 解析覆盖 `repeat()`、`minmax()`、`auto-fit`、`auto-fill`、`fr`、固定/百分比/计算轨道和显式 span。存在多个宽度样本时，以实测断点验证 authored CSS；两者冲突时保留 authored 规则并将冲突列入门禁，不能静默固定为基准宽度列数。

## 动态内容与架构

内容变体应先进入 ViewModel/store，再由 `LazyVGrid`、Stack、`UICollectionView`、`UITableView` 和 Auto Layout 的 intrinsic content size 重新布局。若来源中的弹层或固定容器会随内容状态改变尺寸，记录浏览器前后 rect，并把差值转换为状态化约束；不要在页面控制器里为每种分类写一组绝对 frame。自动生成的尺寸覆盖只允许作用于实测发生变化的内容容器及其 presentation 根，不向无关祖先传播。若 `overflow:auto` 只在某个内容变体中产生真实溢出，滚动轴也属于状态数据；该容器转换为独立原生滚动 viewport，不能让溢出内容压缩同级标题、标签栏或操作区。

## 约束推断

架构规划器必须将有意义容器的关系写入 `contentContainer.layoutRelations`：source/visual child order、axis、alignment、distribution、wrap、gap，以及每个子项的 fixed/intrinsic/flexible 策略、实测宽高、宽高比、flex grow/shrink 和 compression resistance。生成 `<Screen>LayoutContract.swift` 供强类型组件、视觉校准和差异定位使用。该契约表达约束关系，不允许退化成逐节点页面绝对 frame；实测宽高是基准证据，响应式父容器仍由 Auto Layout/SwiftUI Layout 决定最终尺寸。

- leading、trailing 在各宽度基本不变，width 随父宽同比变化：双边 pin，不设固定宽度。
- leading 不变且 width 不变：leading + 固定/intrinsic width。
- trailing 不变且 width 不变：trailing + 固定/intrinsic width。
- center offset 不变且 width 不变：centerX + 固定/intrinsic width。
- width 达到上限后保持不变、两侧同步增长：centerX + `width <= maxWidth`。
- width/parentWidth 在多个样本中稳定：只有此时才使用比例宽度。
- 高度随文字行数变化：让 intrinsic content size 决定，不写死高度。
- absolute/overlay：相对最近定位容器建立约束，不使用页面全局坐标。
- 横向滚动集合：容器宽度随 viewport，item 使用来源 fixed/intrinsic/bounded width；不得把 item 宽度按屏宽重新平均分配。
- 普通 Flex/Grid 行必须保留 `flex-grow`、`flex-shrink` 和 basis 的空间分配语义。`flex-grow > 0` 的直接子项在 SwiftUI 使用可伸展 frame，在 UIKit 使用相应 stack distribution、hugging 与 compression priority；不能只保留文字 intrinsic width，也不能把理想宽度无条件升级为最小宽度。
- viewport 级 navigation bar、tab bar、toolbar 和 bottom action bar 的宽度由父容器 leading/trailing 决定。来源画板宽度只能作为基准 `preferredWidth`，不得由栏内按钮总理想宽度反向撑开；栏内大项允许按 flex 规则压缩/伸展，小图标、角标和文字继续保留自身度量。
- 紧凑方形视觉容器：只要浏览器实测宽高明确、尺寸不超过 180pt、宽高比接近 1，且节点依赖背景色、渐变、圆角、边框或阴影表达视觉，即使它位于纵向流或单格 CSS Grid 中，也必须保留 fixed/bounded width、height 与 `aspectRatio`；不能让 Stack/Grid 的 fill alignment 把圆形拉成胶囊。
- 百分比圆角按实测容器短边计算，例如 `border-radius: 50%` 对 104×104 容器应得到 52pt，不能把百分数当作 px，也不能用与容器无关的圆角上限截断。
- 单行紧凑文本：保留 measured line count、nowrap 和 compression resistance；只有空间策略明确允许时才截断，不能静默换行改变 item 高度。
- `preferredHeight` 必须保留浏览器实测高度，不得用统一经验上限截断。媒体占位图、列表预估高度等需要限制时，应在具体控件分支局部限制，不能污染叠层、画布、圆环和大型视觉容器的几何信息。
- 没有文本和子节点、仅依赖背景、边框、圆角、渐变或阴影表达的视觉叶节点没有可靠 intrinsic content size。对于非满宽的此类节点，必须保留实测宽高或等价约束；否则边框环、光圈、装饰条和占位块会在原生布局中塌缩为零。
- `::before`/`::after`、`aria-hidden` 等装饰节点只表示不进入辅助功能树，不表示视觉上可以删除。只要它们有背景、边框、渐变、阴影或资源，就必须保留，并按相对最近定位父容器的实测中心偏移放入 overlay。
- 当容器全部可见子项均为 absolute/fixed 时，应生成 `ZStack`/自定义 overlay container。容器同时包含流式和 absolute/fixed 子项时，必须在生成模型中拆分为 `children` 与 `overlayChildren`：前者参与 VStack/HStack/UIStackView 的 intrinsic size 计算，后者使用相对父容器中心的定位叠加，不得撑大、压缩或重排父容器。
- 宽度小于父容器约 75% 的紧凑混合叠层（圆环、仪表盘、头像角标、局部画布）应保留浏览器实测宽高，避免父容器退化为底图或文字的 intrinsic size。接近满宽的卡片和页面区块仍使用约束布局，不因存在角标或光晕而锁死整体尺寸。
- 圆角只决定背景、边框和形状，不等于 `overflow: hidden`。叠层子项是否裁剪必须严格服从计算样式中的 `overflow: hidden/clip`；`overflow: visible` 的轨道圆点、角标、光晕和阴影允许越过圆角边界。
- CSS margin 位于 border box 外部，不属于背景、边框、伪元素、渐变或 `overflow` 裁剪区域。SwiftUI 应先在内容 border box 上绘制和裁剪，再在最外层施加 margin；UIKit 应让真实内容 View 持有背景、圆角和 layer，再由透明 wrapper 表达 margin。禁止把 margin 伪装成内容 padding，否则叠层会扩张到外边距并产生矩形色块或错误裁剪。
- 径向渐变的终止半径应由宿主 border box 与 CSS size 语义推导。默认 `farthest-corner` 至少覆盖宿主中心到最远角的距离，不能使用与节点大小无关的固定半径；伪元素渐变还必须以其定位宿主为坐标系，并按宿主真实 overflow 规则裁剪。
- 复合控件内部顺序必须依据浏览器最终几何位置，而不是“父节点直接文本优先”或 DOM 顺序硬编码。图标、徽标、计数、标签文字和尾部元信息应作为有序 `contentItems` 保留，横向布局按最终 x 坐标、纵向布局按最终 y 坐标校准，并保留 CSS `order`/reverse 的视觉结果。
- 带图标、背景、padding、圆角、固定尺寸、大 margin 或显著空白分组的 Flex 行不是普通富文本。不得把它压成单个 Text/UILabel；应保留子 View 和实测间距，使前导图标、计数徽标以及 `margin-left:auto` 形成的尾部信息保持原位。
- `contentItems` 还必须保留每个文字片段的实测宽高、是否单行以及与前项的几何间距。普通 gap 生成固定 spacing；`space-between`、自动外边距或占据容器大部分剩余宽度的空白生成弹性 Spacer。已由父布局消费的 margin 不得再次作为子 View 外层 padding 重复占宽。
- 浏览器中已经保持单行的独立文字片段应在实测宽度内使用单行排版和有限字号适配，避免 iOS 字形度量差异额外制造换行。多行文字和富文本使用浏览器实测文字容器宽度作为可收缩的上限，不锁死为不可响应的整页 frame。
- 对宽度不超过约 120pt、高度不超过约 56pt 的图标底座、计数徽标、标签 Chip 等紧凑视觉包装，应保留实测宽高；不能只让内部 Image/Text 的 intrinsic size 决定外层尺寸，否则背景、圆角、padding 和点击区域都会系统性缩小。
- CSS border 属于 border box，会参与来源节点的总宽高；SwiftUI overlay stroke 和 CALayer border 默认不参与 intrinsic size。带可见边框的普通流式容器必须至少保留浏览器实测 border-box `minHeight`，避免每经过一个卡片、配置行或 footer 就丢失上下边框厚度并形成纵向累计误差。正文、动态内容和响应式大容器仍允许向下扩展，不得因此统一改成 fixed height。
- 横向滚动 item 的实测高度与宽度同样属于集合契约。紧凑 item 应保留 fixed/bounded height，父 carousel 再以来源高度约束；不能只固定宽度，让字体 intrinsic size 把每个 item 压矮并带动后续区块上移。
- 浏览器已经确认发生换行的文本必须同时保留 `expectedTextLines`、测量宽度和可验证的行断点。普通文本和富文本遵循同一规则；当 `lineTexts` 去除空白后能无损重组原文时，生成端应写入显式换行，不能让 SwiftUI/UIKit 因字体更窄而擅自压回单行。
- 带点击行为的复合容器仍须保留原布局语义。CSS Grid/Flex 容器映射为 `Button`/`UIControl` 时，点击语义只能包裹内容，不得把 Grid 子项展平成按钮标题或单行内容。

## 滚动轴隔离

- 页面主滚动轴来自根容器 computed overflow、scroll/client 度量和实际拖动 probe。普通手机长页默认只能 vertical，不以内容越界自动推导 horizontal。
- nested carousel、标签条或横向卡片列表单独拥有 horizontal；它们的高度与父布局约束，内容宽度由 item 累加形成。
- 二维滚动只用于来源明确的画布、地图、缩放内容或双向数据表。不能把 `both` 当作约束冲突的逃生口。
- 多宽度验证若出现根横向 overflow，应先定位超宽子节点、错误 fixed width、padding 重复或 compression priority，而不是打开根横向滚动。

## 左右边距示例

固定画板根宽 318px、基准设备 393pt、HTML 左右边距 18px：

```text
designScale = 393 / 318 = 1.23585
baselineInset = 18 × 1.23585 ≈ 22.25pt
```

iOS 基准使用约 22.25pt 的 leading/trailing constraint。到 320、375、430pt 宽度时 inset 保持不变，内容宽度自动变为 `containerWidth - leading - trailing`。只有多宽度采样证明 margin/width 比例稳定时，才用比例边距。

## 验证矩阵

手机默认验证 320、375、393、430pt；项目最低设备和主流目标可调整。iPad、横屏和 Split View 只有产品支持时加入，但不能因未支持就让页面约束冲突。每个宽度检查：

- 无 Auto Layout ambiguity/constraint conflict
- 无横向溢出和关键文字裁剪
- 纵向根页面不能横向拖动；嵌套横向集合只能在自己的轴向移动
- 关键边距、对齐和最大宽度规则正确
- 文本行数变化合理
- 横向 item 宽度、gap、末项可达性和紧凑图标宽高比正确
- fixed header/footer、滚动和弹层仍可用

原生矩阵必须使用真实 Simulator：

```bash
python3 "$SKILL_ROOT/scripts/validate_responsive_ios_matrix.py" visual-state-manifest.json \
  --project App.xcodeproj --target App --out-dir responsive-matrix \
  --case '375x667:iPhone SE (3rd generation)' \
  --case '393x852:iPhone 16' \
  --case '430x932:iPhone 16 Plus'
```

宽高用于报告中的逻辑 viewport，设备名必须对应本机真实 runtime。工具必须用原始截图尺寸反推 1×/2×/3× Retina scale，并校验它与声明 viewport 一致；不一致直接失败。同一矩阵不得重复声明相同宽高。若没有 320pt 设备，不得把 375pt 截图缩成 320pt；记录缺失并使用项目现有最窄设备完成门槛。

多尺寸目标是保持设计语义和可用性，不要求每个尺寸都与单一 HTML 截图拥有相同换行。
