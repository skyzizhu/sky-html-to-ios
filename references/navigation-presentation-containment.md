# Navigation, Presentation, and Containment Mapping

页面变化分为 navigation、presentation、containment 和 local state 四类。四者生命周期与关闭方式不同，不能统一实现成“隐藏当前 View，再显示另一个 View”。

## 判定顺序

1. 读取项目现有 Router、Coordinator、NavigationStack/path 和容器结构。
2. 使用 HTML 的显式标注：`data-ios-action`、`data-ios-target`、`data-ios-presentation-style`、`data-ios-detents`。
3. 读取原型可观察行为：URL/历史变化、遮罩、覆盖范围、返回方式、交互手势。
4. 根据页面关系推断并写入 confidence；低置信度保持 `unknown`。

显式标注是转换元数据，不要求最终 iOS 页面保留这些属性。

## 导航栈

| IR action | 页面关系 | SwiftUI | UIKit |
|---|---|---|---|
| `push` | 当前流程向下进入详情 | `NavigationLink` / path append | `pushViewController` |
| `pop` | 返回上一级且确认处于栈内 | path removeLast / `dismiss` environment with navigation ownership | `popViewController` |
| `pop-to-root` | 返回栈根 | 重置 path | `popToRootViewController` |
| `replace-stack` | 登录切主流程等整体替换 | 替换 path/root state | `setViewControllers` |
| `set-flow-state` | 同一流程容器内切换步骤/页面状态 | enum/binding/path owner 更新 | Coordinator/container state 更新 |
| `show-detail` | 主从界面打开 detail | split selection/detail | `showDetailViewController` |

Web 内部链接通常是 `push`，但单文档原型的 `switchTo(page)` 可能只是 `set-flow-state`，底部主 Tab、认证流程、外部 URL 和同页锚点也属于例外。`history.back()` 只能确定“返回”，不能从 HTML 单独确定 pop 还是 dismiss，应保留 `back`，在接入目标工程时根据 presentation ownership 解析。

## 模态呈现

| IR action / style | SwiftUI | UIKit | 适用场景 |
|---|---|---|---|
| `present-sheet` | `.sheet` | `present` + `.pageSheet` / sheet controller | 半屏表单、选择器、辅助任务 |
| `present-fullscreen` | `.fullScreenCover` | `.fullScreen` | 登录、扫码、沉浸流程 |
| `present-popover` | `.popover` | `.popover` + source view/item | iPad 定位气泡、上下文辅助内容 |
| `present` + automatic | 由项目路由决定 | `.automatic` | HTML 能确认模态但不能确认样式 |
| `dismiss` | environment dismiss / binding reset | `dismiss(animated:)` | 关闭当前 presented 内容 |

`UIModalPresentationStyle` 的 `.pageSheet`、`.formSheet`、`.currentContext`、`.overCurrentContext`、`.overFullScreen`、`.popover` 和 `.custom` 不是视觉同义词。选择时记录 `presentation.style`，并处理 compact size class 的自适应行为。

HTML 中已有明确绝对定位、无系统箭头、无模态背景且在手机宽度内浮于来源页面之上的 `popover-overlay`，属于自定义局部浮层，不得直接使用会在 compact width 自动适配为 sheet 的系统 `.popover`。IR 必须保存浮层相对应用根容器的实测 `sourceRect`；SwiftUI 使用同层 overlay，UIKit 使用 `.overFullScreen` 自定义容器，并按该矩形定位。真正具备系统 popover 语义、锚点和自适应要求的 `popover` 才映射系统 API。

### Sheet

HTML 出现底部遮罩层、圆角面板和拖拽提示时，可候选为 sheet，但仍需检查内容高度和关闭行为。IR 可记录：

```json
{
  "action": "present-sheet",
  "presentation": {
    "style": "page-sheet",
    "detents": ["medium", "large"],
    "largestUndimmedDetent": null,
    "interactiveDismissDisabled": false,
    "grabberVisible": true
  }
}
```

SwiftUI 使用 `.presentationDetents`、`.presentationDragIndicator` 等可用修饰器；UIKit 配置 `UISheetPresentationController`。自定义高度必须检查目标 SDK 与最低部署版本，并提供 medium/large 或自定义容器降级。

不能只根据 `position: fixed` 判定 sheet。还要验证遮罩覆盖范围、面板是否贴底、顶部圆角/grabber、打开与关闭动作及内容滚动归属。生成后分别截图 medium、large 或自定义高度状态，并检查键盘弹出后的 detent 与输入区域可见性。

## Alert、确认框、菜单与 Overlay

| IR action | SwiftUI | UIKit |
|---|---|---|
| `present-alert` | `.alert` | `UIAlertController(.alert)` |
| `present-confirmation` | `.confirmationDialog` | `UIAlertController(.actionSheet)` |
| `present-menu` | `Menu` / `.contextMenu` | `UIMenu` / context menu interaction |
| `overlay` | `overlay` / `ZStack` | 当前 VC 上的 overlay view |

Toast、snackbar、loading 蒙层通常属于局部 overlay，不应创建新 ViewController。Alert 不用于承载复杂滚动表单；复杂内容改为 sheet 或自定义 presented controller。

## Tab、Split 和 Page

| IR action | SwiftUI | UIKit |
|---|---|---|
| `switch-tab` | 改变 `TabView` selection | 改变 `selectedIndex`/`selectedViewController` |
| `show-primary` | split column selection | `show` 或更新 split column |
| `show-detail` | split detail selection | `showDetailViewController` |
| `page-next` / `page-previous` | 改变 page selection | `setViewControllers(direction:)` |

页面内 segmented tab 只改变局部状态，不创建 `UITabBarController`。主导航 Tab 才使用 app-level tab container。

主 Tab 容器必须位于页面导航栈之外，每个 Tab 内部各自持有导航栈。禁止所有 Tab 共享同一 path 或同一 `UINavigationController`，否则切换和返回会串栈。重复选择当前 Tab 时执行 IR 的 `keep`、`pop-to-root` 或 `scroll-to-top`；`hide-on-push` 仅影响非 Tab 根页面。系统 TabBar 的 safe area 由系统容器管理，HTML 自绘底栏节点在升级后必须从页面树中移除，不能再加一层底部 inset。

## Child ViewController Containment

`addChild` 不是普通“跳转”，它建立父子控制器生命周期。只有下列情况采用 child containment：

- 一个页面长期嵌入另一个独立控制器的内容；
- 自定义容器在多个子控制器间切换；
- 子模块需要独立 appearance、rotation、safe area 或生命周期转发；
- 目标项目架构已经以 child controller 组合模块。

添加顺序：

1. `addChild(child)`；
2. `view.addSubview(child.view)`；
3. 建立 frame/Auto Layout constraints；
4. `child.didMove(toParent: self)`。

移除顺序：

1. `child.willMove(toParent: nil)`；
2. 移除约束；
3. `child.view.removeFromSuperview()`；
4. `child.removeFromParent()`。

IR 使用 `add-child` / `remove-child`，并记录：

```json
{
  "containment": {
    "containerNodeId": "dashboard.content",
    "childScreenId": "analytics",
    "lifecycleOwner": "dashboard-controller"
  }
}
```

SwiftUI 普通 View 组合不需要模拟 `addChild`。只有桥接 UIKit controller 时使用 `UIViewControllerRepresentable` 或项目已有宿主。

## 自定义转场与 Presentation Controller

只有原型确有不可由标准 push/sheet/cover/popover 表达的覆盖范围、交互转场或背景处理时，才使用：

- `UIViewControllerTransitioningDelegate`
- `UIViewControllerAnimatedTransitioning`
- `UIPercentDrivenInteractiveTransition`
- 自定义 `UIPresentationController`

IR 写入 `presentation.transition`、duration、interactive 和 source node。不能仅为了淡入淡出就创建完整自定义转场体系。

## 返回与关闭的所有权

- push 出来的页面由 navigation stack pop。
- present 出来的页面由 presentation chain dismiss。
- child controller 由 parent container remove。
- overlay 由局部 state 关闭。
- Tab 切换不是 dismiss，也不是 pop。

代码生成时必须沿着入口反向生成关闭动作，禁止 sheet 的关闭按钮调用 pop，也禁止栈页面调用 dismiss 后碰巧“看起来能退回”。

## IR Action 词表

导航：`push`、`pop`、`pop-to-root`、`replace-stack`、`set-flow-state`、`back`、`show-primary`、`show-detail`。

呈现：`present`、`present-sheet`、`present-fullscreen`、`present-popover`、`present-alert`、`present-confirmation`、`present-menu`、`dismiss`、`overlay`。

容器与页面：`add-child`、`remove-child`、`switch-tab`、`page-next`、`page-previous`。

内容行为：`toggle-state`、`update-value`、`scroll-to`、`open-url`、`submit`、`unknown`。
