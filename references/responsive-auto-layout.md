# Responsive Auto Layout

HTML 截图提供基准视觉，iOS 实现使用 Auto Layout/SwiftUI layout semantics。禁止把整个页面放进 `scaleEffect` 或按设备宽度实时整体缩放。

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
NODE_PATH=<playwright-node-modules> node scripts/analyze_responsive_layout.cjs \
  --html <entry.html> \
  --selector <app-root> \
  --widths 320,375,393,430 \
  --baseline-width 393 \
  --out responsive-layout.json
```

工具自动区分 viewport 与 fixed-artboard，按目标宽度探测节点相对父容器的位置、宽高响应和文字行数，输出 Auto Layout 建议。

## 约束推断

- leading、trailing 在各宽度基本不变，width 随父宽同比变化：双边 pin，不设固定宽度。
- leading 不变且 width 不变：leading + 固定/intrinsic width。
- trailing 不变且 width 不变：trailing + 固定/intrinsic width。
- center offset 不变且 width 不变：centerX + 固定/intrinsic width。
- width 达到上限后保持不变、两侧同步增长：centerX + `width <= maxWidth`。
- width/parentWidth 在多个样本中稳定：只有此时才使用比例宽度。
- 高度随文字行数变化：让 intrinsic content size 决定，不写死高度。
- absolute/overlay：相对最近定位容器建立约束，不使用页面全局坐标。
- 横向滚动集合：容器宽度随 viewport，item 使用来源 fixed/intrinsic/bounded width；不得把 item 宽度按屏宽重新平均分配。
- 紧凑方形视觉容器：只要浏览器实测宽高明确、尺寸不超过 180pt、宽高比接近 1，且节点依赖背景色、渐变、圆角、边框或阴影表达视觉，即使它位于纵向流或单格 CSS Grid 中，也必须保留 fixed/bounded width、height 与 `aspectRatio`；不能让 Stack/Grid 的 fill alignment 把圆形拉成胶囊。
- 百分比圆角按实测容器短边计算，例如 `border-radius: 50%` 对 104×104 容器应得到 52pt，不能把百分数当作 px，也不能用与容器无关的圆角上限截断。
- 单行紧凑文本：保留 measured line count、nowrap 和 compression resistance；只有空间策略明确允许时才截断，不能静默换行改变 item 高度。
- `preferredHeight` 必须保留浏览器实测高度，不得用统一经验上限截断。媒体占位图、列表预估高度等需要限制时，应在具体控件分支局部限制，不能污染叠层、画布、圆环和大型视觉容器的几何信息。
- 没有文本和子节点、仅依赖背景、边框、圆角、渐变或阴影表达的视觉叶节点没有可靠 intrinsic content size。对于非满宽的此类节点，必须保留实测宽高或等价约束；否则边框环、光圈、装饰条和占位块会在原生布局中塌缩为零。
- `::before`/`::after`、`aria-hidden` 等装饰节点只表示不进入辅助功能树，不表示视觉上可以删除。只要它们有背景、边框、渐变、阴影或资源，就必须保留，并按相对最近定位父容器的实测中心偏移放入 overlay。
- 当容器全部可见子项均为 absolute/fixed 时，应生成 `ZStack`/自定义 overlay container。容器同时包含流式和 absolute/fixed 子项时，必须在生成模型中拆分为 `children` 与 `overlayChildren`：前者参与 VStack/HStack/UIStackView 的 intrinsic size 计算，后者使用相对父容器中心的定位叠加，不得撑大、压缩或重排父容器。
- 宽度小于父容器约 75% 的紧凑混合叠层（圆环、仪表盘、头像角标、局部画布）应保留浏览器实测宽高，避免父容器退化为底图或文字的 intrinsic size。接近满宽的卡片和页面区块仍使用约束布局，不因存在角标或光晕而锁死整体尺寸。
- 圆角只决定背景、边框和形状，不等于 `overflow: hidden`。叠层子项是否裁剪必须严格服从计算样式中的 `overflow: hidden/clip`；`overflow: visible` 的轨道圆点、角标、光晕和阴影允许越过圆角边界。
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

多尺寸目标是保持设计语义和可用性，不要求每个尺寸都与单一 HTML 截图拥有相同换行。
