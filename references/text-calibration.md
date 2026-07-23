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

## 对比

生成页面时为文字节点保留 UI IR node ID/accessibility identifier，并让 UI Test 或 Debug-only 测量器导出：`nodeId`、frame、lineCount、firstBaseline、lastBaseline、truncated。运行：

```bash
python3 scripts/compare_text_calibration.py text-calibration.json ios-text-metrics.json \
  --out text-comparison.json
```

默认要求行数完全一致，frame 偏差不超过约 1.5pt，baseline 偏差不超过约 1pt。截图仍用于观察字形、抗锯齿、富文本颜色和下划线；结构化指标用于防止肉眼漏掉累计换行误差。

## Dynamic Type

高保真基准先在原型字号下验收。若产品要求 Dynamic Type，再单独验证支持的 Content Size Category，使用相对字体和可伸缩约束；不要同时承诺像素级固定稿与任意字号下完全相同。无障碍放大时允许页面高度和换行变化，但禁止裁剪关键操作和正文。
