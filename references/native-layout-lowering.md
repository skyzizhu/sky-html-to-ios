# Native Layout Lowering

`native-layout-plan.json` 是 UI IR、六层架构与 SwiftUI/UIKit 生成器之间唯一可执行的布局契约。它解决“架构计划保存了关系，但运行时 Payload 又重新猜测”的双轨问题。

该阶段不依赖截图、Simulator 或多模态能力。

## 输入与产物

固定输入：

- 已校验的 UI IR；
- `native-architecture-plan.json`；
- `layout-relation-graph.json`。

固定产物：

- `native-layout-plan.json`；
- `native-layout-plan-validation.json`。

当前 schema 是 `native-layout-plan-1.1`。计划按 Screen 保存 Content Container、所有有意义容器、每个节点的盒模型、Flex/Grid item、定位参照系、状态布局增量和复合控件槽位。

## 容器降级

每个容器必须固定记录并由两套生成器共同消费：

- `axis`：horizontal、vertical、grid 或 overlay；
- `layoutAlgorithm`：stack、wrapping-stack、grid 或 positioned-overlay；
- `orderedChildNodeIds`：浏览器最终视觉顺序；
- 独立 row/column gap、alignment、distribution、wrap、reverse 和 writing direction；
- 每个子项的 fixed、intrinsic 或 flexible 尺寸策略；
- measured width/height、aspect ratio、flex grow/shrink 和 compression resistance；
- 关系图中支撑该决策的 relation IDs。

SwiftUI 使用这些证据选择 Stack/Grid/Overlay、spacing、frame 和 layout priority。UIKit 使用相同证据配置 `UIStackView`、Auto Layout、Table/Collection item sizing 与 hugging/compression priority。禁止技术栈各自重排子节点。

Wrapping stack 在 SwiftUI 中使用原生 `Layout` 协议实现，在 UIKit 中使用独立 wrapping `UIView`；不得用横向滚动或缩小文字代替换行。Grid 必须保存 column/row tracks、auto flow、auto tracks，以及每个 item 的 start/end/span。固定轨道映射为固定原生尺寸，其他轨道保留 flexible/intrinsic 语义。

## CSS 盒模型

浏览器 `getBoundingClientRect()` 表示视觉 border box。计划必须同时保存：

- `boxSizing` 和原始 width/height 长度表达式；
- border-box 与推导后的 content-box 宽高；
- top/right/bottom/left 的 padding、border 和 margin；
- min/max width/height 的原生 border-box 约束；
- transform 原始值；
- fixed、percentage、calculation、viewport-relative、font-relative、intrinsic-keyword 或 automatic 长度分类。

`content-box` 的固定 min/max 约束在进入原生布局前必须加上 padding 与 border，转换成 border-box 参考值。百分比、`calc()` 和相对单位不得被正则截断成错误常量；计划保存 reference axis、relative factor 和 calculation terms，由父容器和响应式规则解析。

原生运行时必须消费可确定的 min/max、padding、border、margin 和 compression 证据。transform 只改变视觉绘制时，不得反向污染正常流尺寸。

## 定位与状态布局

每个节点保存 static、relative、absolute、fixed 或 sticky 定位方案。absolute 使用最近的 positioned ancestor，fixed 使用 viewport，sticky 使用最近 scroll owner；同时保存 containing block、相对偏移、insets、z-index、transform 和 transform origin。定位节点不得默认相对屏幕居中，也不得改变普通流兄弟节点的尺寸分配。

同页状态使用 `stateLayouts` 保存 insert/remove/replace 对应的目标父容器、生成节点布局和原节点基线布局。状态节点仍消费普通 Node Layout Contract；状态变化不得另建一套截图坐标或独立页面。

## 复合控件

包含图标、标题、Badge、计数、尾部图标或 Loading 的 Button/Control 必须生成 `compoundControls` 记录。槽位 ID 和顺序来自最终浏览器几何，不来自 DOM 顺序或文字拼接。

通用槽位包括：

- `leadingIcon`；
- `title`；
- `badge`；
- `trailingIcon`；
- `indicator`；
- `content`。

生成器必须把相同顺序写入 Payload `compoundLayout` 与 `contentItems`。系统控件内部布局无法满足时，保留系统交互语义并使用原生内容容器包装；不得把整个控件退化成无语义截图或普通 View 手势。

## 硬门禁

`validate_native_layout_plan.py` 在 Swift 生成前检查：

1. UI IR、架构计划与关系图 SHA-256 一致；
2. Screen、Node 和 Container 集合闭合；
3. 视觉子节点顺序没有在降级阶段变化；
4. 关系图容器具有 relation 证据；
5. 盒模型尺寸非负，长度表达式具有正确 reference axis，`calc()` 具有可执行 terms；
6. Grid 具有轨道、wrapping container 使用 wrapping 算法、定位节点具有正确 containing block；
7. 状态布局集合与 UI IR state delta 一致；
8. 复合槽位唯一、完整并保持视觉顺序。

生成后，`native-structure-manifest.json` 再逐容器核对算法、axis、child order、row/column gap、wrap、alignment 和 distribution，逐节点核对尺寸/定位契约，逐状态核对布局操作，并逐复合控件核对 `compoundLayout` 与 `contentItems`。任一项未消费时不得接入 Xcode target。

## 单阶段调试

```bash
python3 "$SKILL_ROOT/scripts/build_native_layout_plan.py" \
  --ir page-ui-ir.json \
  --architecture-plan native-architecture-plan.json \
  --layout-graph layout-relation-graph.json \
  --out native-layout-plan.json

python3 "$SKILL_ROOT/scripts/validate_native_layout_plan.py" \
  --plan native-layout-plan.json \
  --ir page-ui-ir.json \
  --architecture-plan native-architecture-plan.json \
  --layout-graph layout-relation-graph.json \
  --out native-layout-plan-validation.json
```

失败时回到 UI IR、关系图或架构计划修复。禁止直接编辑生成后的 Swift 绕过计划。
