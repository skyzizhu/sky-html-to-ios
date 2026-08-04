# UI IR Schema

UI IR 是浏览器渲染结果与原生代码之间的稳定中间层。不要直接把原始 DOM 当成生成模型。

## 目录

1. 顶层结构
2. Source 与 Target
3. Screen 与 Node
4. Semantic Type 词表
5. Native Mapping
6. State
7. Style 与 Content
8. Interaction、Motion、Visual State、Asset 与 Warning

## 顶层结构

```json
{
  "schemaVersion": "1.2",
  "source": {},
  "target": {},
  "screens": [],
  "interactions": [],
  "states": [],
  "motions": [],
  "visualStates": [],
  "assets": [],
  "warnings": []
}
```

### source

- `kind`: `html-file`、`html-directory` 或 `url`
- `entry`: 入口文件或 URL
- `viewport`: 浏览器基准宽高和 device scale factor
- `capturedAt`: ISO 8601 时间
- `baselineScreenshot`: 基准截图路径
- `routeGraph`: `html-route-graph.json` 路径与 screen ID 映射
- `interactionGraph`: 已验证的 `interaction-state-graph.json` 路径与源指纹
- `interactionOverrides`: 可选的 `html-to-ios.overrides.json` 路径；指纹必须与 interaction graph 一致

### target

- `platform`: 固定为 `ios`
- `uiStack`: `swiftui` 或 `uikit`
- `device`: 模拟器设备名称
- `viewportPt`: iOS 逻辑宽高
- `orientation`: `portrait` 或 `landscape`
- `appearance`: `light` 或 `dark`
- `minimumIOS`: 最低系统版本；无法确定时允许为空并添加 warning
- `scale`: 固定宽度原型的缩放比；响应式同宽渲染通常为 `1`

## Screen

```json
{
  "id": "home",
  "name": "Home",
  "moduleId": "home",
  "rootNodeId": "home.root",
  "sourceSelector": "#app",
  "systemChrome": {
    "statusBar": "native",
    "navigationBar": "custom",
    "homeIndicator": "native"
  },
  "regions": {
    "topBar": {"nodeId": "home.top", "kind": "custom-navigation-bar", "confidence": 0.91, "evidence": []},
    "bottomBar": {"nodeId": "home.actions", "kind": "bottom-action-bar", "placement": "viewport-overlay", "confidence": 0.9, "evidence": []},
    "floatingAction": null
  },
  "navigation": {
    "style": "native",
    "title": "Home",
    "titleMode": "large",
    "scrollEdgeAppearance": "transparent",
    "backButton": "system"
  },
  "tabContainer": {
    "id": "main-tabs",
    "initialTabId": "home-tab",
    "reselectBehavior": "pop-to-root",
    "visibility": "hide-on-push",
    "items": []
  },
  "nodes": []
}
```

`systemChrome` 的每项只能是 `native`、`custom` 或 `none`。HTML 中模拟的系统状态栏、信号、电池和 Home Indicator 属于展示外壳，默认移除；应用自己的导航栏可以保留为 `custom`。

`moduleId` 是稳定的 `lower-kebab-case` 业务模块 ID，用于生成 `<Module>/Screens|Controllers|Views`。它优先来自 `data-ios-module`；缺失时可由稳定 screen ID 前缀谨慎推断，不得从视觉相似度猜测业务边界。

`regions` 保存持久页面区域的节点、类型、放置方式、置信度和证据。底栏 `placement` 只能是 `safe-area-inset` 或 `viewport-overlay`；区域识别依据几何、固定/吸顶行为、交互子项和语义综合判断，详见 `page-regions-and-system-chrome.md`。

`sourceCoverage` 保存从选定 app root 到当前 route UI IR 的来源覆盖统计，包括 `rootSubtreeNodeCount`、`routeScopedNodeCount`、`mappedNodeCount`、`excludedByRouteCount`、`excludedNonVisualOrUnsupportedTagCount` 和 `mappedRatio`。HTML 模式必须生成且计数闭合；直接传入已有 UI IR 时允许缺失，并在结构验收中标记来源核算不适用。

`navigation` 明确系统导航、自绘顶部栏、隐藏或沉浸式所有权；不得只用一个显示布尔值表达。`tabContainer` 仅用于 App 级主 Tab，每个 item 必须具有稳定 ID 和有效 `targetScreenId`。页面内筛选 Tab 继续使用 `tab-control` 或 segmented state，不生成 `UITabBarController`。

## Node

每个节点至少包含：

```json
{
  "id": "home.search.button",
  "parentId": "home.search",
  "source": {
    "selector": "#app .search button",
    "tag": "button",
    "domId": "search-button"
  },
  "semanticType": "button",
  "layout": {
    "mode": "flow",
    "rect": {"x": 329, "y": 62, "width": 44, "height": 44},
    "display": "flex",
    "position": "static",
    "overflowX": "visible",
    "overflowY": "visible",
    "scrollAxis": "none",
    "scrollMetrics": {
      "horizontalAllowed": false,
      "verticalAllowed": false,
      "overflowsHorizontally": false,
      "overflowsVertically": false,
      "scrollWidth": 44,
      "scrollHeight": 44,
      "clientWidth": 44,
      "clientHeight": 44
    }
  },
  "style": {},
  "content": {
    "text": null,
    "lines": null,
    "lineRects": [],
    "lineTexts": [],
    "clippedHorizontally": false,
    "clippedVertically": false
  },
  "state": {
    "enabled": true,
    "selected": false,
    "checked": false,
    "expanded": null,
    "readonly": false
  },
  "textBehavior": {
    "role": "input",
    "nativeControl": "text-field",
    "editable": true,
    "readOnly": false,
    "selectable": true,
    "multiline": false,
    "scrollable": false,
    "secure": false,
    "maxLength": 80,
    "fieldID": "search.query",
    "autofocus": false,
    "returnKey": "search",
    "autocapitalization": "none",
    "autocorrection": false,
    "validation": "required"
  },
  "dataBinding": {
    "sourceID": "search-results",
    "itemIDKey": "id",
    "stateRole": "content",
    "pagination": "cursor",
    "ownership": "external",
    "requiresViewModel": true,
    "snapshotIsSampleData": true
  },
  "nativeMapping": {
    "swiftUI": "Button",
    "uiKit": "UIButton",
    "styleStrategy": "plain-native-semantics-custom-appearance",
    "confidence": 0.98,
    "rationale": ["html-tag:button"],
    "availability": {
      "verifiedSDK": "iphoneos-26.2",
      "minimumIOS": "16.0",
      "swiftUI": {"status": "available", "symbol": "Button", "introduced": "13.0", "fallback": null},
      "uiKit": {"status": "available", "symbol": "UIButton", "introduced": null, "fallback": null}
    }
  },
  "assetRef": null,
  "interactionRef": "interaction.search",
  "support": "native"
}
```

规则：

- `id` 在整个 IR 中唯一，并可安全用作 `accessibilityIdentifier`。
- `parentId` 为空表示 screen 根节点。
- `semanticType` 使用本文件下方的受控词表。不要为同一种语义临时创造近义名称。
- `layout.rect` 使用浏览器 viewport 坐标中的 CSS px；转换后保留原值和 iOS pt 值。
- `layout.scrollAxis` 只能为 `none`、`horizontal`、`vertical` 或 `both`。它由 computed overflow、scroll/client 度量和行为 probe 综合决定，不能仅因子节点越界而扩大父容器滚动轴。
- `layout.scrollMetrics` 保留允许方向、实际溢出和 scroll/client 尺寸。纵向页面与嵌套横向集合分别拥有各自轴向；无证据时使用 `none`，不使用 `both` 兜底。
- `content.lines`、`lineRects`、`lineTexts`、`firstBaselineY`、`lastBaselineY` 与 `fontMetrics` 使用浏览器实际文字行框、字符归属和字体度量。`lineTexts` 只有在逐字符 Range 与完整渲染文本可校验一致时才写入；`whiteSpace`、`webkitLineClamp`、`textOverflow`、`flexShrink`、`flexWrap` 和宽高比继续保留在 computed `style` 中。
- 文本节点和输入节点使用 `textBehavior` 固化行为判定。`role` 为 `input|display`，`nativeControl` 为 `label|text-field|text-view`；编辑、只读、选择、多行、内部滚动和 secure 分开表达。`readonly` 输入仍是输入控件，纯展示 TextView 必须 `editable=false`。
- `dataBinding` 只在存在明确动态数据标注时生成，并默认作为视觉 fixture 元数据。`ownership=external` 必须具有 `sourceID`，但基础转换不生成接口或 ViewModel；loading/content/empty/error 只用于区分需要还原的画面结构。
- `controlVisualStates` 保存浏览器实测的 `normal|pressed|highlighted|focused|editing|disabled|selected|checked|loading` 紧凑样式。它只记录与 normal 状态不同或来源当前明确激活的状态；`pressed` 可作为 UIKit `highlighted` 的来源别名，输入控件的 `focused` 可作为 `editing` 的来源别名。原生 Button/UIControl/输入焦点消费该字段，不能靠统一高亮效果替代。
- 横向 repeat item、紧凑图标容器和比例媒体的来源 rect 是生成 `fixed/min/intrinsic/aspectRatio` 策略的证据。生成器可派生运行时样式，但不得删除原始 rect 与 CSS 证据。
- UI IR 校验后必须派生 `layout-relation-graph-1.0`，把 parent-child、视觉顺序、相邻间距、对齐、等宽/等高、square aspect、overlap 和 scroll owner 从单节点属性提升为跨节点约束；原生架构不得改变这些关系。
- `support` 使用 `native`、`native-fallback`、`placeholder` 或 `unsupported`。
- `source.synthetic` 记录 pseudo-element 等浏览器生成节点；`layout.estimated` 表示边界需要截图纠偏。
- 自动视觉纠偏生成新的 IR 副本，并在根级 `visualCorrectionHistory` 与节点级 `calibration.visualCorrections` 记录来源 IR hash、计划、迭代、前后值和操作。该记录不得改写 `sourceRectCssPx`，也不得成为跳过重新截图的理由。

## Semantic Type 词表

结构：

- `container`、`header`、`footer`、`navigation`、`navigation-bar`
- `tab-bar`、`tab-control`、`scroll`、`grid`、`divider`、`spacer`
- `decoration`：伪元素、装饰层和没有独立交互语义的视觉节点

内容：

- `text`、`heading`、`label`、`image`、`icon`、`video`、`audio`
- `canvas-artwork`、`map`、`embedded-content`、`unsupported-web-content`

操作与输入：

- `button`、`icon-button`、`link`
- `form`、`text-input`、`secure-input`、`search-input`、`number-input`
- `date-input`、`text-area`、`file-input`
- `checkbox`、`switch`、`radio`、`radio-group`、`segmented-control`
- `select`、`multi-select`、`option`、`option-group`、`slider`、`stepper`、`color-picker`

集合与反馈：

- `list`、`list-item`、`sectioned-list`、`data-table`、`table-row`、`table-header`、`table-cell`、`carousel`
- `progress`、`meter`、`disclosure`、`disclosure-trigger`、`form-group`
- `tab-item`、`menu-item`
- `modal`、`sheet`、`alert`、`toast`、`menu`、`loading`、`overlay`
- `custom`

## Native Mapping

每个节点必须有 `nativeMapping`：

- `swiftUI`: 推荐 SwiftUI 控件或布局原语，如 `Button`、`TextField`、`LazyVGrid`。
- `uiKit`: 推荐 UIKit 控件，如 `UIButton`、`UITextField`、`UICollectionView`。
- `styleStrategy`: `native-default`、`plain-native-semantics-custom-appearance`、`project-component`、`custom-native-view`、`native-fallback` 或 `unsupported`。
- `confidence`: `0–1`。
- `rationale`: 字符串数组，记录 tag、role、input type、交互或布局证据。
- `availability`: SDK 核验结果，包含 `verifiedSDK`、`minimumIOS`，以及 SwiftUI/UIKit 各自的 `status`、`symbol`、`introduced` 和 `fallback`。

`status` 使用 `pending-verification`、`available`、`available-review-version`、`requires-fallback`、`deprecated`、`unavailable` 或 `review-required`。开始生成某个节点代码前，该节点所选技术栈不得仍为 `pending-verification`。

低于 `0.5` 的映射不得直接生成特定系统控件，应保留为 `custom` 或要求人工确认。

## State

控件节点保留：

- `enabled`、`selected`、`checked`、`expanded`、`readonly`
- `initiallyVisible`：节点及其 CSS 祖先在初始捕获状态是否可见；初始隐藏节点只能由对应 state/presentation 显示
- `required`、`focused`
- `min`、`max`、`step`、`value`
- `groupName`：radio/checkbox/select 的逻辑分组名
- `keyboardType`、`contentType`、`submitLabel`

没有语义的字段使用 `null`，不要用错误的 `false` 表示“未知”。

## Style

样式保留最终计算值，不保留无法确定的原始 CSS 表达式：

- `backgroundColor`、`color`、`opacity`
- `fontFamily`、`fontSize`、`fontWeight`、`fontStyle`、`lineHeight`、`letterSpacing`、`textAlign`
- `padding`、`margin`、`gap`
- `borderWidths`、`borderColors`、`borderStyles`
- `cornerRadii`
- `boxShadow`
- `backgroundImage`、`backgroundGradient`、`backgroundSize`、`backgroundPosition`、`backgroundRepeat`
- `transform`、`zIndex`
- `objectFit`、`objectPosition`、`clipPath`、`filter`、`backdropFilter`

颜色统一为可解析的 `rgb()`/`rgba()` 或 8 位 Hex。四方向值都展开成 top/right/bottom/left，四角值展开成 topLeft/topRight/bottomRight/bottomLeft。

生成载荷还要从 `fontFamily` 推导 `fontDesign=default|monospaced|serif|rounded`，作为 Web Font 不可嵌入时的原生 fallback 契约；原始 family 字符串仍须保留，不能用推导值覆盖来源证据。

## Content

- `text`: 节点自身的直接文本，避免重复包含子节点文本
- `placeholder`、`value`
- `accessibilityLabel`
- `lines`: 已知的行数限制
- `fontResolution`: 请求字体序列、最终解析字体、解析状态、失败候选和置信度
- `isDecorative`: 是否为纯装饰

## Interaction

```json
{
  "id": "interaction.open-detail",
  "sourceInteractionId": "interaction-5",
  "sourceNodeId": "home.card.1",
  "sourceNodeIds": ["home.card.1"],
  "sourceSelector": ".card",
  "sourceScope": null,
  "trigger": "tap",
  "action": "push",
  "target": "detail",
  "payload": {},
  "presentation": null,
  "containment": null,
  "confidence": 0.94,
  "automatic": false,
  "prerequisiteInteractionIds": [],
  "requiresResolution": false
}
```

`sourceInteractionId` 追溯到动态交互图；重复 selector 使用 `sourceNodeIds` 保留全部原生节点，`sourceNodeId` 是测试所用的首选节点。`target` 可引用 route graph screen ID 或 interaction graph state ID。自动迁移允许 `sourceNodeId=null`、`sourceScope=screen`、`trigger=appear/timer-complete`；页面根之外、应用壳以内的共享导航、底栏和弹层控件使用 `sourceScope=application`，并在六层架构中绑定 Application Container 或 Screen Region；document/window 环境事件也允许无 node，但必须保留 scope。AST-only、runtime-verified 与 override-resolved 状态应进入 warning/evidence，不能合并成一个虚假的“已验证”。

一个 Web 事件可以产生多个副作用，全部写入 `payload.transitions`。每项包含 source transition ID、action、screen/state target、state owner、schedule、resolution 状态、confidence 和 evidence。返回首页时同时重置检测进度等跨 screen state 是合法副作用，不得因目标 state 不属于当前 screen 而删除。

`prerequisiteInteractionIds` 表示动作前必须先执行的 opener，例如展开区域 → 点选项、打开 popover → 切分类。不得把隐藏子控件直接放进视觉状态步骤。

## Dynamic State

顶层 `states` 来自 interaction graph，至少包含：`id`、`sourceStateId`、`ownerScreenId`、`kind`、`targetSelector`、`targetNodeIds`、`classes` 和 `confidence`。同一个 selector 可映射多个 node；当前 screen 之外的 state 只作为 transition 的 `targetStateOwnerScreenId` 引用，不复制进本页 state 列表。

`action` 使用以下受控词表：

- 导航：`push`、`pop`、`pop-to-root`、`replace-stack`、`back`、`show-primary`、`show-detail`
- 流程状态：`set-flow-state`，用于同一原生流程容器内切换 screen state，不等同于 push
- 呈现：`present`、`present-sheet`、`present-fullscreen`、`present-popover`、`present-alert`、`present-confirmation`、`present-menu`、`dismiss`、`overlay`
- 容器/页面：`add-child`、`remove-child`、`switch-tab`、`page-next`、`page-previous`
- 内容：`toggle-state`、`update-value`、`scroll-to`、`open-url`、`submit`、`unknown`

`presentation` 可记录 style、detents、transition、interactive dismiss 和 source anchor。`containment` 可记录 container node、child screen 和 lifecycle owner。字段无关时为 `null`。

无法可靠判断时使用 `unknown` 并添加 warning，不要猜成某种导航。

## Motion

`motions` 中每项包含 source node、kind、properties、duration/delay、timing、iteration、keyframes、sampleProgress、SwiftUI/UIKit 建议、support 和 confidence。Motion source 可为 `css-transition`、`css-animation` 或 `web-animation`。

## Visual State

`visualStates` 至少包含 `initial`，并记录 triggerInteractionId、interactionSequence、scroll、required 和来源。长页面与可观察的弹层/切换状态由 builder 自动补充；隐藏控件状态必须通过 prerequisite sequence 到达。

当 HTML 使用并排画板表达同一页面的不同状态时，`states[].visualRepresentation` 记录原始 `screenId`、`sourceSelector` 与归并证据。状态画板不会生成独立业务页面，但必须单独提取 UI IR。

`states[].stateDelta` 使用 `visual-state-delta-1.0`，包含：

- `operations`：规范化后的 `insert-subtree`、`remove-subtree`、`replace-subtree`。比较阶段可产生 `update-node`，但合并器必须在生成前将其物化为可切换的 `replace-subtree`，并保留 `changes` 作为差分证据。
- `nativeStrategy`：由差分作用域与触发方式推导的 presentation、conditional subtree、replacement、property variant、contextual item actions 或 composite delta。
- `contextualTargetNodeId`、`contextualTargetConfidence` 与 `contextualActionRootNodeIds`：条目操作的原生承载节点、推断置信度和操作子树。操作按钮可位于操作根的任意后代层级。
- `suppressedRemovalNodeIds` 与 `protectedInteractionSourceNodeIds`：因证据不足而抑制的删除，以及为避免误删而保护的触发节点/祖先。
- `matchedNodeCount`、owner/representation node count 与 `confidence`。
- `triggers`：来自 interaction graph 的 tap、swipe、drag、change 等触发证据。

原生生成必须消费该差分：新增节点进入 owner 原生树，presentation 节点从页面主布局中分离，局部节点由状态控制可见性，删除/替换节点进入隐藏或替换集合。文字、样式、尺寸或布局属性变化统一物化为最小受影响子树的替换状态，不能因为某个属性难以原地修改而重新生成整张业务页面。

每个节点的 `nativeMapping.nativeControlDecision` 使用 `system-first-visual-fit-gated` 策略，记录系统候选、HTML/ARIA/JS 行为证据、CSS 视觉复杂度、blocker、官方配置路径和 fallback chain。生成器必须保留系统控件作为交互所有者；只有 `requiresCustomControl=true` 才允许自定义 `UIControl` 承担交互。

状态生成节点必须携带 owner state 标识。后续状态差分只读取基础 owner 节点，不能把已生成的其他状态子树当成新增或删除证据。条件显隐只挂在增量子树根节点，后代保留各自在状态画板中的可见性，避免深层图标、文字或控件被连带过滤。

顶层 `stateDeltaReviews` 为每个重复画板保留自动决策证据，包括策略、操作数量、置信度、被抑制删除、受保护触发节点、复核原因与推荐作者提示。`requiresHumanReview=true` 时，不得把该状态宣称为确定性转换完成。

## Asset

- `id`、`kind`、`source`
- `localPath` 或 `url`
- `contentType`
- `dimensions`
- `renderMode`: `original`、`template`、`cover`、`contain`、`tile`
- `licenseStatus`: `local-provided`、`known` 或 `unknown`
- `iosName` 和接入状态

## 警告

每条 warning 包含：

- `code`
- `severity`: `info`、`warning`、`error`
- `nodeId`
- `message`
- `fallback`

存在 `error` 级 warning 时，除非该错误仅影响用户明确放弃的区域，否则不要进入代码生成。
