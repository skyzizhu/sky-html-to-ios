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
- 关键边距、对齐和最大宽度规则正确
- 文本行数变化合理
- fixed header/footer、滚动和弹层仍可用

多尺寸目标是保持设计语义和可用性，不要求每个尺寸都与单一 HTML 截图拥有相同换行。
