# Visual Validation

视觉验收必须在对应逻辑 viewport、方向、外观和稳定数据状态下进行。HTML 位于固定展示板时允许源 viewport 与目标 viewport 不同，但归一化过程必须显式记录。

## 基准

- 响应式 HTML viewport 与 iOS 目标逻辑尺寸一致；固定展示板保留 source viewport，并单独归一化 app root。
- HTML 和 iOS 使用相同浅色/深色模式。
- 去除浏览器外框、模拟手机壳和非应用展示背景。
- system chrome 要么两边都纳入，要么两边都排除。
- 固定时间、随机内容、异步数据、光标和动画帧。

## 验证顺序

1. 构建成功。
2. 在目标模拟器打开对应页面和状态。
3. 运行 `capture_ios_states.py`，由 generator-owned XCUITest target 按 manifest 截取 iOS 页面。
4. 检查两张图尺寸和裁剪区域。
5. 运行 `scripts/visual_diff.py`。
6. 运行 `scripts/build_visual_review_bundle.py` 生成成对截图、热力图和重点差异区域。
7. 模型支持图像时，按 `visual-agent-review.md` 执行多模态走查。
8. 先处理大面积结构偏差，再处理字体、阴影和抗锯齿细节。
9. 每轮只修改有限节点并重新验证。

## 指标

不要只给一个“相似度”。至少报告：

- 图片尺寸是否一致
- RGB 平均绝对差异
- 超过阈值的像素比例
- 简单像素相似度
- 被 mask 的系统区域
- 最大差异区域的人工说明
- top difference regions 和全局 difference bounds
- required visual states 是否完整
- critical navigation/bottom-bar region 的最大 mismatch
- typography region 的 edge mismatch

默认门禁同时检查 exact-size、全局 mismatch ratio、平均绝对差异、critical region mismatch 和 text edge mismatch。文本抗锯齿、阴影和透明 blur 需要更宽容；关键布局锚点建议控制在约 1–2pt。多模态只能解释失败并给出修复建议，不能覆盖 required state 的确定性失败。

## Mask

可以 mask：

- 两边无法统一的系统时间、电池和信号区域
- 明确声明为动态且无法固定的内容
- 用户同意暂不实现的外部媒体区域

不能 mask：

- 生成错误的主要布局
- 文字换行、按钮位置和卡片尺寸
- 为了让结果通过而任意扩大区域

每个 mask 都要在报告中记录原因和矩形。

## 长页面和状态

- 首屏、中段和末端分别检查，避免间距误差累计。
- 检查固定头部、吸顶、固定底栏和嵌套滚动。
- 每个重要 Tab、展开态、弹窗、sheet、错误态和空状态都应有对应基准。
- 只有内容溢出超过 `max(24pt, viewportHeight * 5%)` 才自动增加滚动末端；内容超过 1.5 个 viewport 时再增加 middle，避免为几像素溢出制造伪必测状态。
- 动画采样帧在没有原生确定性采样钩子时为 advisory；不能把未受控的原生帧设为 required。
- 交互覆盖与视觉相似度分开报告。
- `interactionSequence` 是 HTML selector actions 与 iOS accessibility actions 的共同来源；先执行 screen activation，再执行 prerequisite 和目标动作，禁止两侧手写不同路径。

## 固定画板归一化

响应式页面通常使用目标 viewport 直接截图。固定手机画板位于大展示板中时，浏览器使用提取时的 source viewport 执行动作，再将选定 app root 以 preserve-aspect `cover` 方式归一化到 target viewport。captures report 必须保存 `originalSize`、`outputSize`、`normalized` 和 `normalization`。

归一化只用于建立同尺寸视觉参考，不允许成为原生运行时缩放方案。若 source 与 target 的宽高比差异明显，必须先复核画板根节点、system chrome 和目标设备，不得依靠裁剪掩盖布局错误。

## 多尺寸

先按 `responsive-auto-layout.md` 推断约束，再在 320、375、393、430pt 或项目实际支持宽度上构建验证。HTML 多尺寸基准用于验证布局规则，不要求通过整页比例缩放制造截图。每个宽度单独检查边距、最大宽度、文字行数、横向溢出和约束冲突。

## 差异定位

按以下顺序排查：

1. viewport、scale 和 Safe Area
2. 根容器和滚动容器
3. 字体文件、weight、line-height 和换行
4. 资源 content mode 与裁剪
5. padding、gap、margin 和 constraint priority
6. border、corner、shadow、gradient 和 blur
7. transform、z-order 和 animation state

通过 UI IR source selector 和 `accessibilityIdentifier` 将差异区域映射回单个原生组件。

自动状态截图由 `build_visual_state_manifest.py` 生成清单，`capture_html_states.cjs` 捕获 HTML；iOS 侧由 `prepare_visual_ui_tests.rb` 和 `capture_ios_states.py` 按同一清单的 `iosActions` 捕获。XCTest 导出的 Retina 像素图必须在 capture 阶段降采样到 `targetViewport`，并在 `captures.json` 保留 original/output size；visual diff 阶段禁止暗中 resize。两侧文件统一使用 `<state-id>.png`。

`validationRegions` 必须至少包含 viewport，并尽量包含 top navigation、bottom/tab/action bar、文字、控件和资源节点。导航与底部持久区域标为 critical；报告中的 `regions.png` 与 `worstSemanticRegions` 必须保留 nodeId，便于直接回到 UI IR 和生成组件。
