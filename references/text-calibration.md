# Text Calibration

文字是独立验收面，不作为普通矩形节点附带处理。浏览器和 iOS 字体度量、fallback、baseline、字重插值和换行算法不同，必须先固定字体资源，再校准布局。

## 提取与报告

`extract_render_tree.cjs` 的 `render-tree-1.2` 为文字节点记录 `textMetrics`：完整渲染文本、Range 行框、行数、字体加载状态和横纵裁剪。随后运行：

```bash
python3 scripts/build_text_calibration.py render-tree.json \
  --out text-calibration.json \
  --target-width 393
```

报告给出：

- 字体候选、字号、weight、style、line-height 和 letter-spacing
- 固定缩小画板的一次性设计倍率
- 目标 frame、逐行 line rect 和准确行数
- 富文本节点、字体文件需求和 fallback 风险
- iOS 测量结果需要提供的字段

字体解析状态区分 `loaded-web-font`、`system-local`、`generic-family`、`generic-fallback` 和 `unresolved-fallback`。`generic-fallback` 表示前置命名字体加载失败后浏览器实际落到 system-ui/serif/monospace 等通用族；它不是“Web Font 已加载”，也不应触发字体文件接入。`system-local` 只证明当前浏览器宿主可用，仍须核对目标 iOS SDK 是否存在对应字体。

## iOS 实现顺序

1. 先接入正确字体文件和所有使用到的 weight；许可不明时停止嵌入并标记 fallback。
2. 再匹配字号、字重和字距。
3. 再匹配 line height、first/last baseline 和段落间距。
4. 最后调整容器宽度、换行、line limit、truncation 和富文本 range 样式。

不能为了让一段文字看起来对齐而任意修改父容器 padding。先判断差异属于字体度量还是容器约束。

## 控件所有权

文字度量与控件选择是两个阶段。先根据 DOM/ARIA、运行时属性和交互证据生成 `textBehavior`，再执行字体校准：

- 单行可编辑内容属于 TextField，`textarea` 或明确多行编辑属于 TextView/TextEditor。
- 普通展示文字属于 Label/Text，即使视觉上有背景和圆角也不因此升级为输入控件。
- 只有选择、链接交互、独立文本滚动或显式原生提示成立时，展示文字才使用只读 TextView；必须关闭编辑能力，避免弹出键盘或改变内容。
- `readonly`、`disabled`、`editable`、`selectable` 是不同状态，不得互相代替。验收必须覆盖聚焦、键盘、选择、滚动和提交行为，而不只比较静态截图。

SwiftUI `lineSpacing` 不是 CSS `line-height`；UIKit 使用 `NSParagraphStyle.minimumLineHeight/maximumLineHeight` 时要同时处理 baseline offset。中英文混排、Emoji、JetBrains Mono 等等宽字体和不同 weight 必须分别验证。

生成器必须保留字体候选、resolved family、resolution status、失败候选、generic family、style 和 100–900 weight，不能只留下字号。无法合法嵌入来源 Web Font 时，按最终可用语义选择原生 fallback：`monospace` → monospaced system design，`serif` → serif system design，普通 `sans-serif/system-ui` → default system design，并在报告中保留 fallback 风险。只有经过白名单核验的 iOS 内置字体才可写入 `fontNativeName`；未知宿主字体继续使用 system design。`800/900`、`100/200/300` 不得分别压成统一 bold 或 regular。

SwiftUI 多行文字的额外间距按 `CSS line-height - native UIFont.lineHeight` 计算，并把剩余 leading 对称分配到首尾行框；禁止继续使用 `line-height - font-size`。UIKit 使用目标 line height 固定 paragraph 的 minimum/maximum line height，并按原生字体 line height 计算 baseline offset。未显式给出 line-height 时沿用原生字体度量，不制造额外 leading。

复合文字内容不能只保留拼接后的字符串。直接文本节点、内联 span 和视觉子 View 应保留浏览器中的顺序、Range 宽高与前置间距。来源中明确为单行的独立片段可以在实测宽度内使用 `lineLimit(1)` 与有限 `minimumScaleFactor`；多行正文和富文本只把实测宽度作为可收缩上限，禁止为了匹配基准截图造成根页面横向溢出。

`Range.getClientRects()` 的矩形数量不等于视觉行数；数字、单位、上下标等不同字号 run 可能在同一行产生多个不同高度的矩形。应按垂直重叠和中心线距离合并视觉行，单行多字号内容在 SwiftUI 使用 `firstTextBaseline`、在 UIKit 使用 first-baseline 约束，不能因 DOM 容器是 `display:block` 就改成纵向堆叠。

UI IR 必须保留 `firstBaselineY`、`lastBaselineY` 和浏览器字体 ascent/descent。生成 payload 时将绝对 baseline 转为节点顶部相对偏移，作为结构化验收目标。水平复合内容只有在浏览器确认同属一条视觉行、所有参与项都有真实文本且不存在图标/装饰占位时，才启用 first-baseline 对齐；图标加文字、状态圆点和空样式 span 继续服从来源 `align-items`。

浏览器逐字符 Range 可以进一步形成 `lineTexts`，用于保留真实换行位置，但它不是无条件的硬换行来源。只有字符归属能够与完整渲染文本校验一致，并且 `lineTexts` 数量与合并后的视觉行数一致时，生成器才可在富文本 run 中插入换行；否则必须保留 run 顺序并交给原生排版。这样可以防止数字和较小单位因 baseline 不同被误拆为两行，同时保留中文长句在浏览器中的真实断行。

## 对比

生成页面时为文字节点保留 UI IR node ID/accessibility identifier，并让 UI Test 或 Debug-only 测量器导出：`nodeId`、frame、lineCount、firstBaseline、lastBaseline、truncated。运行：

```bash
python3 scripts/compare_text_calibration.py text-calibration.json ios-text-metrics.json \
  --out text-comparison.json
```

默认要求行数完全一致，frame 偏差不超过约 1.5pt，baseline 偏差不超过约 1pt。截图仍用于观察字形、抗锯齿、富文本颜色和下划线；结构化指标用于防止肉眼漏掉累计换行误差。

## Dynamic Type

高保真基准先在原型字号下验收。若产品要求 Dynamic Type，再单独验证支持的 Content Size Category，使用相对字体和可伸缩约束；不要同时承诺像素级固定稿与任意字号下完全相同。无障碍放大时允许页面高度和换行变化，但禁止裁剪关键操作和正文。
