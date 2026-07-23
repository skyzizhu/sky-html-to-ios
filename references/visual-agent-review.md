# Multimodal Visual Agent Review

多模态视觉走查位于构建和确定性像素检查之后。它用于解释差异和指导局部纠偏，不替代编译、交互测试或像素指标。

## 能力门控

进入本阶段前必须判断当前 Agent 是否真的能够接收或打开图片。判断依据是可用的图像输入/查看工具及一次实际打开测试，不根据模型名称、模式名称或硬编码型号表猜测。

能力状态：

- `available`：能够实际查看 `comparison.png`/`overlay.png`，允许进入结构化视觉走查。
- `unavailable`：保留确定性像素报告，将本阶段标记为 `not-run`。
- `unknown`：先尝试打开一张 comparison；成功后改为 `available`，失败后改为 `unavailable`。`unknown` 不得视为已完成。

生成 review bundle 时显式传入实际状态：

```bash
python3 scripts/build_visual_review_bundle.py <manifest> \
  --html-dir <html-screenshots> \
  --ios-dir <ios-screenshots> \
  --out-dir <review-output> \
  --multimodal-capability available
```

自动化环境也可设置 `CODEX_AGENT_IMAGE_CAPABILITY=available|unavailable`。CLI 显式参数优先。

## 输入

每个 visual state 向 Agent 提供：

- HTML reference screenshot
- iOS simulator screenshot
- side-by-side comparison
- diff、heatmap 和 overlay
- mismatch metrics 与 top difference regions
- UI IR 节点树、source selector 和 accessibility identifier
- 当前状态的操作步骤和 motion sample progress

缺少必需截图时状态直接失败，不允许模型凭描述批准。

## 状态矩阵

自动生成最低集合：initial、长页面 top/middle/bottom、主 Tab/局部 Tab、sheet/full-screen/popover/alert/menu/overlay、主要 toggle state、原型存在的 empty/loading/error，以及动画 0%/50%/100% 采样帧。

复杂交互链由 Agent 在 manifest 中补充安全动作。禁止在状态清单中执行任意 JavaScript；HTML 捕获工具只允许 click、fill、check、select、press、hover、scroll 和 wait。

## 结构化输出

```json
{
  "stateId": "after-present-sheet",
  "severity": "major",
  "category": "layout",
  "rect": [24, 318, 345, 220],
  "relatedNodeId": "filters.sheet",
  "observation": "The iOS sheet starts 28pt lower than the HTML reference.",
  "recommendedFix": "Adjust the selected detent or preferred content height.",
  "confidence": 0.93
}
```

category 使用 viewport/safe-area、layout、typography、color、asset、shape、shadow-blur、z-order、state、motion 或 system-chrome。

## 自动纠偏循环

1. 先修 viewport、scale、safe area 和根容器。
2. 再修大面积布局与滚动。
3. 再修字体、资源、层级和控件状态。
4. 最后修边框、阴影、blur 和抗锯齿细节。
5. 每轮只修改能映射到明确 UI IR node 的有限范围。
6. 重新构建全部受影响状态，不只重测当前截图。

默认最多自动纠偏 3 轮。相同问题连续两轮没有改善、其他 required state 明显退化、需要改变业务语义/资源许可/架构、差异来自不可控系统内容，或模型置信度低于 0.6 且没有确定性证据时停止并报告。

## 通过条件

通过是组合结论：构建成功、required states 齐全、关键交互可达、结构锚点正确、差异指标在项目阈值内、视觉 Agent 无 blocker/major 未解决项。

文字抗锯齿、动态系统时间、透明 blur 和阴影允许更宽松阈值，但必须局部 mask 并记录理由。模型不得以“整体看起来接近”为由忽略按钮错位、文字换行、裁剪或关闭路径错误。

## 无多模态能力时

继续执行状态截图、像素差异、top difference regions 和人工可读报告。将视觉 Agent 阶段标记为 `not-run`，不得虚报已完成视觉审查。
