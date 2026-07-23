# Custom Component Fallback

当系统控件不能同时满足语义、交互和视觉要求时，允许自定义，但必须按层级降级，不能一开始就重写系统能力。

## 降级顺序

1. 目标工程已有 Design System 或业务组件。
2. 系统组件 + 官方样式配置，例如 `UIButton.Configuration`、appearance、SwiftUI style。
3. 多个系统组件组合成复合 View。
4. 自定义 `UIView` / `UIControl` / SwiftUI `View`。
5. 需要独立页面生命周期时，自定义 `UIViewController`。
6. 需要复合生命周期和子模块管理时，自定义 container `UIViewController`。
7. 仅装饰内容可用 `CALayer`、Core Graphics、SwiftUI Canvas 或合法位图资源。
8. 无法原生表达且不适合安全转换时标记 `unsupported`，不得整页截图或 `WKWebView` 伪装。

## 选择 View、UIControl 还是 ViewController

使用自定义 View：纯展示、布局组合、装饰、无独立页面生命周期。

使用自定义 `UIControl`：具有 enabled、selected、highlighted、focused、value changed、target-action 或明确可访问性控件语义。不要给普通 `UIView` 加 tap gesture 来替代 Button、checkbox、radio 或 switch。

使用自定义 ViewController：页面需要独立生命周期、导航/模态所有权、键盘/状态栏协调、复杂子视图状态管理，或目标项目规定每个 screen 由 controller 管理。

使用自定义 container ViewController：需要管理 child controller 的增加、移除、切换、appearance/rotation/safe-area 转发。仅为了圆角、阴影或一段动画，不得创建 container controller。

## 必须写入 IR 的信息

自定义节点至少记录：

- `nativeMapping.styleStrategy`: `project-component`、`custom-native-view`、`custom-native-control` 或 `custom-view-controller`
- `nativeMapping.rationale`: 系统候选为何不满足
- `nativeMapping.fallbackChain`: 已评估的候选顺序
- `support`: `native`、`native-fallback` 或 `unsupported`
- 状态、事件、无障碍 role/label/value
- 视觉验证的重点区域

## 自定义封装要求

- API 以语义命名，不以 HTML class 名直接命名。
- 样式参数只暴露真实变化项，避免生成一个包含几十个 CSS 参数的万能组件。
- 保留 Dynamic Type、VoiceOver、RTL、Dark Mode、Reduce Motion、键盘和点击区域。
- 复合组件的子控件继续使用系统语义控件。
- UIKit 明确 intrinsic content size、Auto Layout 和 trait changes；SwiftUI 明确状态所有权和 identity。
- 只有重复使用、独立职责或明显降低复杂度时才抽组件。

## 常见正确兜底

| HTML 形态 | 不合适的系统直译 | 推荐兜底 |
|---|---|---|
| 圆形 checkbox | `UISwitch` | 自定义 `UIControl`，保留 checked 状态 |
| 多列数据表 | `UITableView` | compositional `UICollectionView` 或自定义网格 |
| 高度自适应底部面板且旧系统不支持 detent | 强行调用新 API | 自定义 presented controller + 可用性分支 |
| 复杂品牌化 Tab | 普通 segmented control | 自定义 Tab control；若为主导航仍由 tab container 管理 |
| Toast | `UIAlertController` | 当前页面 overlay View |
| 复杂 SVG 交互图 | 一张截图 | 局部 Shape/Core Graphics，不能可靠迁移则 unsupported |

## 禁止事项

- 不用 screenshot slice 替代功能卡片或控件。
- 不把 Web DOM 运行时带进原生页面。
- 不为每个 `div` 创建一个自定义 View 类。
- 不因系统控件默认外观不同就放弃其语义；先尝试官方样式能力。
- 不生成无 `@available` 防护的新 API。
