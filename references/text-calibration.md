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

## iOS 实现顺序

1. 先接入正确字体文件和所有使用到的 weight；许可不明时停止嵌入并标记 fallback。
2. 再匹配字号、字重和字距。
3. 再匹配 line height、first/last baseline 和段落间距。
4. 最后调整容器宽度、换行、line limit、truncation 和富文本 range 样式。

不能为了让一段文字看起来对齐而任意修改父容器 padding。先判断差异属于字体度量还是容器约束。

SwiftUI `lineSpacing` 不是 CSS `line-height`；UIKit 使用 `NSParagraphStyle.minimumLineHeight/maximumLineHeight` 时要同时处理 baseline offset。中英文混排、Emoji、JetBrains Mono 等等宽字体和不同 weight 必须分别验证。

复合文字内容不能只保留拼接后的字符串。直接文本节点、内联 span 和视觉子 View 应保留浏览器中的顺序、Range 宽高与前置间距。来源中明确为单行的独立片段可以在实测宽度内使用 `lineLimit(1)` 与有限 `minimumScaleFactor`；多行正文和富文本只把实测宽度作为可收缩上限，禁止为了匹配基准截图造成根页面横向溢出。

`Range.getClientRects()` 的矩形数量不等于视觉行数；数字、单位、上下标等不同字号 run 可能在同一行产生多个不同高度的矩形。应按垂直重叠和中心线距离合并视觉行，单行多字号内容在 SwiftUI 使用 `firstTextBaseline`、在 UIKit 使用 first-baseline 约束，不能因 DOM 容器是 `display:block` 就改成纵向堆叠。

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
