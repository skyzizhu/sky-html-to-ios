# Common Mapping Rules

这些规则适用于 SwiftUI 和 UIKit。所有数值以 UI IR 中的运行时结果为准。

具体 HTML 控件、ARIA role、input type 与原生组件的选择，读取 `control-mapping-matrix.md`。本文件只处理两套技术栈共有的布局和视觉规则。

## 布局语义

| Web | iOS 语义 |
|---|---|
| 普通块级纵向流 | 垂直结构化布局 |
| `display:flex; flex-direction:row` | 水平结构化布局 |
| `display:flex; flex-direction:column` | 垂直结构化布局 |
| Grid | Grid/Collection 布局 |
| `position:absolute` | 相对父容器的叠层定位 |
| `position:fixed` | 页面根层固定区域，不放进滚动内容 |
| `position:sticky` | pinned header 或滚动联动实现 |
| `overflow:auto/scroll` | 仅对应容器可滚动 |
| `overflow:hidden` | 裁剪，不代表滚动 |

不要仅凭 tag 决定布局。`div` 可能是任意布局，`span` 也可能被 CSS 改成块级元素。

## 尺寸模式

- `fixed`: 明确固定图标、头像、按钮高度等，按目标 scale 转为 pt。
- `container`: `width:100%`、stretch、flex grow，使用父容器约束。
- `content`: 文本、徽标和自适应内容，让 intrinsic size 决定。
- `proportional`: 百分比和 aspect ratio，保留比例语义。
- `bounded`: `min/max-width/height`，保留边界。

对响应式同宽渲染，不要再把 computed rect 整体乘 `375 / sourceWidth`。桌面展示板中的固定缩小画板只允许按 `responsive-auto-layout.md` 做一次设计 token 归一化，运行时仍使用原生约束，禁止整页 `scaleEffect`。

## CSS 盒模型

IR 中保留 content rect、padding、border 和 margin。原生实现必须区分：

- padding 属于组件内部；
- margin 属于父布局间距；
- border 在背景外缘；
- shadow 不应被错误裁剪；
- `box-sizing` 已反映在 computed rect 中，不要重复加减。

## 文本

- 使用实际渲染字体族、字号、字重、行高、字距、对齐和截断规则。
- CSS `font-weight: 500/600` 映射到最接近的 iOS weight，不要全部退化为 regular/bold。
- `line-height` 是最终行框高度；SwiftUI `lineSpacing` 通常应使用 `lineHeight - fontMetricsLineHeight`，不能直接把 line-height 当 lineSpacing。
- 多行文本验证实际换行位置。字体度量差异往往比 padding 更容易造成累计偏差。
- 保留 `nowrap`、line clamp、ellipsis 和富文本片段。

## 颜色、边框、圆角和阴影

- 使用 computed color，包括 alpha 和色彩空间假设。
- 四边边框可能不同，不要只读取 `border` 简写。
- 四角圆角分别保留；`50%` 圆形按最终元素尺寸计算。
- CSS shadow blur 与 CALayer/SwiftUI shadow 参数不完全等价，先按语义映射，再通过截图调整。
- spread、inset shadow、复杂多重阴影需要 CALayer/Core Graphics fallback，并在 IR 中标记。
- 普通线性/径向渐变必须保留种类、方向、颜色 stop 和 location；SwiftUI 与 UIKit 从同一结构化字段渲染，禁止 UIKit 只取首色冒充渐变。
- 普通统一边框、虚线/点线、opacity、外阴影和 overflow 裁剪属于生成器基础能力。四边宽度/颜色不同、inset/multiple shadow、复杂 blend/filter 才进入自定义 View/CALayer fallback。
- shadow 与 clipping 同时存在时必须拆分外层投影和内层裁剪语义；不能为了圆角直接裁掉外阴影。

## 层级、变换和裁剪

- 根据 stacking context 和最终绘制顺序建立原生层级。
- 保留 transform origin、translate、scale、rotate；skew 和 perspective 允许使用 `CATransform3D`。
- border radius、mask、clip-path 和 overflow 的组合必须按浏览器可见结果验证。
- 不要因为某个子节点绝对定位，就把整个页面改成绝对坐标。

## 滚动

- 找到真正滚动的容器，保持固定头部和底部栏在滚动区域之外。
- 横向列表使用横向滚动容器；纵向页面不要嵌套多个同方向滚动容器。
- 长页面至少验证顶部、中段和末端。
- sticky、分页、轮播和下拉刷新分别标记，不能都简化成普通 ScrollView。

## 系统栏与 Safe Area

- HTML 展示用的时间、电池、信号、刘海和 Home Indicator 默认移除。
- 应用自定义导航栏或 Tab Bar 是内容的一部分，可以保留。
- 背景是否延伸进 Safe Area 与内容是否避让是两个独立决定。
- 每个 screen 在 IR 中记录 `systemChrome`，避免重复预留。

## 降级顺序

1. 使用目标栈原生组件。
2. 使用项目已有组件。
3. 使用局部 CALayer/Core Graphics/UIViewRepresentable。
4. 将纯装饰区域作为合法图片资源。
5. 无法实现时使用明确占位并报告。

禁止使用整页位图或 `WKWebView` 作为降级方案。
