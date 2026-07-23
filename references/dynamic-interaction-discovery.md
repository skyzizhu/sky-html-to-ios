# Dynamic Interaction Discovery

动态交互发现用于补齐静态 DOM 和 route graph 无法表达的行为：`addEventListener`、函数间接调用、class/style/content 变化、计时完成跳转、弹层开关、选择状态和临时反馈。源 HTML 始终只读，不为方便提取而注入永久标记或改写业务脚本。

## 标准流程

先生成 `html-route-graph.json`，再运行：

```bash
NODE_PATH="<node_modules>" "<node>" scripts/discover_html_interactions.cjs \
  --html <entry.html> \
  --route-graph html-route-graph.json \
  --out interaction-state-graph.json \
  --overrides-out html-to-ios.overrides.json

python3 scripts/validate_interaction_graph.py interaction-state-graph.json \
  --overrides html-to-ios.overrides.json
```

URL 输入改用 `--url`。仅当可信页面确实依赖跨源资源时使用 `--allow-remote`。诊断纯静态结果时可用 `--skip-runtime-probe`，但产物必须将 runtime capability 标记为 `not-run`。

## 证据层

发现器必须分开保存两类证据，不得互相冒充：

1. AST evidence：使用内置 Acorn 解析 JavaScript，记录注册位置、调用链、目标 selector、变更类型、计时信息和源码片段。
2. Runtime evidence：每个可安全点击的交互在全新浏览器页面中执行，记录动作前后可见 screen、目标 class/text 和 URL 变化。

正则只能用于辅助命名和风险词判断，不能代替 JavaScript AST。某段脚本解析失败时写入 warning，并降低覆盖结论；不能声称已完整发现。内置解析器位于 `scripts/vendor/acorn`，保留其 MIT License 和版本信息。

## 图结构

`interaction-state-graph-1.0` 至少包含：

- `source.fingerprint`：源 HTML 或 URL+脚本内容的 SHA-256。
- `capabilities`：AST 与 runtime probe 的真实执行状态。
- `screens`：与 route graph 对齐的原生转换页面。
- `interactions`：来源 selector/scope、owner screen、trigger、分类、静态证据、运行时证据与 confidence。
- `states`：局部状态、选择、展开、进度、sheet、popover、overlay、full-screen overlay 和 transient feedback。
- `transitions`：screen/state 目标、触发类型、时间、Web 行为、原生动作候选与 override 要求。
- `unresolved`：仅保留无法由证据唯一决定的问题。

原型展示外壳的导航只用于 screen 激活，不进入 App 业务交互图。共享底栏等位于 screen 根节点外的控件，可结合直接目标、入口页、显示状态和 opener 关系推断 owner；推断证据不足时保留未解析项。

## 运行时安全策略

- 每个 probe 使用全新页面，避免上一个动作污染状态。
- 只执行本地、可逆、无明显副作用的 tap；文件选择、提交、支付、购买、删除、发送和外部跳转默认跳过。
- 对折叠区、popover、sheet 和 overlay 内部控件，先执行已发现的 opener，再探测目标。
- 重复 selector 优先探测可见且未激活项，避免点击默认选中项得到假阴性。
- backdrop 点击使用空白边缘，不点击内容中心。
- document/window 级监听记为 ambient event，默认不主动 probe。
- 弹窗由隔离页面自动 dismiss，并在证据中记录。

运行时 `failed`、`skipped` 和 `changed=false` 都不等于行为不存在。Agent 应结合 AST、DOM 状态和截图判断，并保留真实状态。

## 原生动作判定

HTML 只能证明“发生了页面状态变化”，通常不能唯一证明 iOS 所有权。以下行为必须给出候选而不是武断定论：

- 前进页面：`replace-flow-state`、`push`
- 回入口：`pop-to-root`、`replace-root`、`replace-flow-state`
- 自动完成后进入结果：`replace-flow-state`、`push`、`replace`
- 弹层：根据视觉层级选择 `sheet`、`popover`、`full-screen-cover` 或项目内 overlay

最终选择遵循：用户显式要求 > 项目 Router/Coordinator 现有所有权 > HTML 视觉和返回关系 > 推荐候选。`switchTo()` 或 class toggle 本身不等于 `push`。

## 覆盖文件

`html-to-ios.overrides.json` 是唯一允许的人工作业面。不要修改 HTML 来消除解析歧义。文件包含与图一致的 `sourceFingerprint`、待解决项和用户/Agent 已确认的 resolution。

处理规则：

1. 只在候选会实质改变原生结构时请求用户，例如 push 与 sheet、pop 与 dismiss。
2. 推荐项可由现有工程结构唯一证明时，Agent 可填写 resolution 并记录理由。
3. 源指纹变化后旧覆盖文件失效，验证器必须报错；重新发现后再迁移仍适用的决定。
4. 非关键低置信度项可以保留 TODO，但核心验收路径存在未解析目标时不能宣称完成。

## 与 UI IR 的关系

route graph 决定有哪些 screen，interaction graph 决定 screen/state 如何变化，render tree 决定每个状态长什么样。构建 UI IR 时三者共同输入：

- screen ID 必须一致；
- interaction ID 和 state ID 保持稳定并映射到 accessibility identifier 与测试动作；
- presentation/open/close 和导航 transition 进入 visual state matrix；
- CSS 动画的关键帧仍由 motion pass 提供，interaction graph 只负责触发和状态边。

使用以下参数完成确定性合并：

```bash
python3 scripts/build_ui_ir.py render-tree.json \
  --screen-id <screen-id> \
  --route-graph html-route-graph.json \
  --interaction-graph interaction-state-graph.json \
  --interaction-overrides html-to-ios.overrides.json \
  --out ui-ir.json
```

合并器必须：排除其他虚拟 screen 子树；保留当前 screen 外部但属于其流程的共享底栏/弹层；将一个 class selector 映射到全部重复 node；将容器内部动作补成 opener → action 的 prerequisite sequence；保留返回时重置其他 screen 状态等跨 screen 副作用。interaction graph 存在时，没有 href/form/data-ios/动态证据的普通 button 不应生成 `unknown` 业务动作。

## 边界与降级

- 动态 `eval`、运行时拼接函数、closed Shadow DOM、跨域 iframe 和不可访问脚本：记录能力缺口，必要时使用运行时截图与人工 override。
- 手势依赖坐标、拖拽、长按或多点触控：静态图可记录入口，但必须专项运行时验证。
- 网络驱动或随机流程：固定可复现状态；无法固定时标记不稳定，不自动执行真实请求。
- Canvas/WebGL 内部交互：不声称已识别 DOM 控件，按 `edge-case-policy.md` 降级。
