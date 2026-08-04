# 原生控件内部配置契约

系统控件的选择完成后，必须把网页实测的内部几何、颜色和状态降级为 `native-control-configuration-plan.json`。该计划是 UI IR 到 SwiftUI/UIKit 的确定性配置契约，不依赖截图、Simulator 或模型多模态能力。

## 输入证据

- `semanticType`、HTML/ARIA 表单语义和 `nativeControlDecision`；
- 浏览器计算后的 padding、gap、宽高、前景色、背景色和边框色；
- 浏览器计算后的 `accent-color` 与 `appearance`；
- `controlVisualStates` 中的 normal、pressed/highlighted、focused/editing、selected/checked、disabled、loading；
- 本机 SDK 审计结果和最低 iOS 版本。

不得用控件外层文字的颜色无条件覆盖内部轨道、滑块或选中项。颜色要按控件语义分槽：Switch 使用 off track/on fill/thumb，Slider 和 Progress 使用 track/fill，PageControl 使用普通页/当前页，SegmentedControl 使用普通文字/选中文字/选中背景。

## 几何规则

- 保存来源宽高、四向 content inset、复合控件 item spacing 和 intrinsic-size 语义；
- 四向顺序固定为 `top, right, bottom, left`；
- `UISwitch`、`UIStepper`、`UIDatePicker`、`UIColorWell` 等优先保留系统固有尺寸，来源外框差异由布局容器或 wrapper 承担；
- Button/Search/Input 的 content inset 写入系统 configuration 或文本容器，不用额外空白 Label 模拟；
- wrapper 只能承载阴影、渐变、非对称装饰或外框布局，不能截获系统控件事件与无障碍语义。

## 状态规则

系统控件必须保留原生状态机。`UIControl.Event`、editing lifecycle、Binding 或 selection 驱动视觉状态；禁止用普通 View 的点击手势伪造 pressed、selected、focused 或 disabled。缺少某个来源状态时保留系统行为或继承 normal，不猜测截图专用颜色。

状态优先级为 disabled > editing/focused > highlighted/pressed > checked/selected > normal。浏览器 `pressed` 可降级为 UIKit `highlighted`，`focused` 可降级为输入控件 `editing`，`checked` 与 `selected` 只在控件语义允许时互为回退。每个控件都必须保存 normal 基线，状态别名必须进入计划并由验证器核对。

SwiftUI 使用 `ButtonStyle`、`FocusState`、Binding/selection 和系统控件 tint 执行状态；UIKit 使用 `UIControl.State`、editing events、`valueChanged` 以及具体控件的 `isOn`/selected index。Switch、Slider、SegmentedControl、PageControl、ProgressView 与 ActivityIndicator 必须更新系统控件内部槽位，不能只改变 wrapper 背景。

## 执行与门禁

```bash
python3 scripts/build_native_control_configuration_plan.py \
  --ir ui-ir.json \
  --out native-control-configuration-plan.json

python3 scripts/validate_native_control_configuration_plan.py \
  --plan native-control-configuration-plan.json \
  --out native-control-configuration-validation.json
```

生成器必须通过 `--control-configuration-plan` 消费计划。`native-structure-manifest.json` 按 node 记录 content inset、spacing、tint、track/fill tint、preferred style 和状态消费；独立验证失败时不得接入 Xcode target。

截图视觉验证只用于发现系统内部栅格化或无法从 DOM 确定的差异。纠偏结果必须回写 UI IR 或该派生计划后重新生成，不得直接修改 Swift 形成样例特判。
