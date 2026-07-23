# Interaction Rules

交互转换的目标是保留原型中可观察到的页面行为，不是凭空补全业务系统。

## 建立交互图

先按 `dynamic-interaction-discovery.md` 运行 `discover_html_interactions.cjs` 和 `validate_interaction_graph.py`。route graph、interaction graph 和 render tree 是三个独立契约，不能靠手写推测替代发现结果。

为每个交互记录：

- 稳定 interaction ID
- 来源 node ID
- trigger：tap、long-press、drag、submit、change、appear
- action
- target screen 或 state
- transition 和动画参数
- presentation style、detents 或 containment owner
- confidence
- 无法迁移的业务依赖

静态 AST 证据和运行时 probe 证据必须分别保存。运行时失败不抹掉静态证据，静态发现也不能冒充已执行验证。原型外壳控制器不进入 App 业务交互图。

## 导航分类

| Web 行为 | 原生行为 |
|---|---|
| 当前页面进入详情 | push |
| 返回链接、history back | `back`；接入后按所有权解析为 pop 或 dismiss |
| 半屏表单、选择器 | sheet |
| 全屏流程或登录 | full-screen presentation |
| Toast、气泡、轻量菜单 | overlay |
| 页面内 Tab | switch-tab 或局部状态 |
| 外部 HTTP(S) 链接 | 系统浏览器或项目已有 Web 容器 |
| 同页锚点 | 滚动到目标节点 |

不要仅因为 HTML 使用 `window.open` 就一律映射为 sheet；结合内容范围、视觉层级和目标来源判断。

完整 action 词表、push/pop/present/dismiss/addChild 的生命周期规则见 `navigation-presentation-containment.md`。当 HTML 作者能提供转换意图时，优先读取：

- `data-ios-action`
- `data-ios-target`
- `data-ios-presentation-style`
- `data-ios-detents`
- `data-ios-container`
- `data-ios-component`

显式标注仍需通过 SDK 和项目架构检查，不允许借此生成不存在的类或错误生命周期。

## 状态变化

- class 切换、`display`、`visibility` 和 `opacity` 切换应映射为局部状态。
- 文本、Slider、Picker、select 和 textarea 的值变化统一记录为 `change + update-value`；checkbox、switch 和 radio 使用 `change + toggle-state`。
- `display:none` 不占布局；`visibility:hidden` 保留布局；`opacity:0` 仍可能可交互，三者不能混淆。
- 展开收起、选中态、Tab、弹层和 loading 分别建立状态节点。
- 动画保留 duration、delay、easing、方向和触发时机；无法精确映射时使用最接近曲线并标记。

## 表单

- 保留输入类型、placeholder、键盘类型、secure 状态、disabled、selected 和焦点顺序。
- HTML 表单校验规则可以映射为本地输入约束，但不要伪造服务端校验。
- submit 有后端依赖时，生成明确回调接口或项目已有 action，不编造请求地址。

## 手势

- click/tap 可以直接映射。
- hover 没有 iPhone 等价行为；若它只是视觉反馈则忽略，若承载信息则改为可点击或长按入口并报告。
- drag、swipe、pinch、复杂轮播和嵌套滚动必须单独验证手势优先级。

## 可测试性

- 每个交互节点使用 IR node ID 作为 `accessibilityIdentifier`。
- 生成页面级 smoke test 时，优先按 identifier 定位，不依赖坐标。
- 至少验证每个页面入口、返回路径、弹层关闭和主要状态切换。

## 不确定处理

confidence 较低或目标缺失时：

- 保留原生事件入口和稳定 identifier；
- 不猜测错误页面；
- 在交付报告中列出来源代码和需要确认的问题。

原生导航所有权不唯一时写入 `html-to-ios.overrides.json`，不要修改源 HTML。源指纹变化后旧 resolution 必须重新验证。
