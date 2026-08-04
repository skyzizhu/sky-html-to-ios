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

混合普通流与 positioned 子节点时，先用浏览器矩形判断实际重叠。若 positioned 子节点覆盖普通流内容且承担同一视觉层级，所有相关子节点按最终绘制顺序进入同一 Overlay/ZStack，并保留容器实测尺寸；若没有实质重叠，普通流继续参与 Stack/Grid 测量，positioned 子节点单独挂到 overlay 层。禁止把所有 absolute 节点一概移出后破坏前后层级。

复用容器另外生成 `collectionLayouts`：

- `adaptiveColumns`：保存 `auto-fit`/`auto-fill`/实测响应式列变化及最小 item 宽度；
- `responsiveBreakpoints`：保存每个目标宽度下的实际容器宽度、列数、item 宽高和最大文字行数；
- `itemSizingByNodeId`：逐 item 保存宽高模式、估算高度、宽高比、Grid span 与各宽度文字行数；
- section 级 `itemSizing` 只作为 fallback，不能覆盖已存在的 item 级证据。

`responsive-layout.json` 是该契约的结构化输入，不是截图附件。总控必须把每个 Screen 对应的响应式分析传给 `build_native_layout_plan.py` 和验证器，并保存 SHA-256 来源。`repeat(auto-fit|auto-fill, minmax(...))` 降级为 adaptive Grid；媒体查询导致的列数变化降级为实测断点。SwiftUI 使用 adaptive `GridItem` 与 item modifier，UIKit Flow/Compositional Layout 使用实际 `bounds`/`effectiveContentSize` 选择断点。禁止只按 393pt 中位数生成。

- `layoutEngine`：table、flow 或 compositional；
- `itemNodeIds`：排除 supplementary 后的视觉 item 顺序；
- `headerNodeId`/`footerNodeId` 与独立 pinning；
- `columnCount`、content insets、main/cross-axis spacing；
- `widthMode`：full-width、fixed、fractional 或 estimated；
- `heightMode`：fixed、estimated 或 aspect-ratio；
- measured estimate、width fraction、fixed dimensions 与 aspect ratio；
- directional lock 和禁止同轴嵌套滚动。

单列 Table 默认 full-width，只有来源 authored height 明确固定且所有行测量一致时才固定 row height，否则使用自适应行高和浏览器测量值作为 estimate。横向 Collection 在 `flex-shrink:0`、nowrap、固定 width/flex-basis 等证据成立且实测一致时保留 fixed width；纵向 Grid 按轨道或实测列位置推断 fractional width，稳定比例 item 使用 aspect-ratio。

Wrapping stack 在 SwiftUI 中使用原生 `Layout` 协议实现，在 UIKit 中使用独立 wrapping `UIView`；不得用横向滚动或缩小文字代替换行。Grid 必须保存 column/row tracks、auto flow、auto tracks，以及每个 item 的 start/end/span。固定轨道映射为固定原生尺寸，其他轨道保留 flexible/intrinsic 语义。显式 Grid placement 必须由 SwiftUI `Layout` 或 UIKit 原生布局容器执行，不能只记录 span 后仍按普通顺序平均分列。

## CSS 盒模型

浏览器 `getBoundingClientRect()` 表示视觉 border box。计划必须同时保存：

- `boxSizing` 和原始 width/height 长度表达式；
- border-box 与推导后的 content-box 宽高；
- top/right/bottom/left 的 padding、border 和 margin；
- min/max width/height 的原生 border-box 约束；
- transform 原始值；
- fixed、percentage、calculation、viewport-relative、font-relative、intrinsic-keyword 或 automatic 长度分类。

`content-box` 的固定 min/max 约束在进入原生布局前必须加上 padding 与 border，转换成 border-box 参考值。提取器同时保存最终 computed style 和当前 cascade 中获胜的 authored layout 声明；前者证明浏览器最终结果，后者保留 `%`、`calc()`、Grid track 和 flex basis 等表达式语义。跨域不可读样式表允许 computed-only 降级，但不得伪造原始表达式。

百分比、`calc()` 和相对单位不得被正则截断成错误常量。只含 `%` 与 `px` 的表达式必须降级为 `parent * affineMultiplier + affineConstantPt`，由 SwiftUI `Layout` 或 UIKit Auto Layout 相对父容器执行；无法确定性求解的 viewport/font 相对表达式才进入 measured fallback，并保留原表达式和 reference axis。

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

生成后，`native-structure-manifest.json` 再逐容器核对算法、axis、child order、row/column gap、wrap、alignment 和 distribution，逐集合核对 item order、尺寸模式、列数、supplementary、pinning、insets、间距和滚动隔离，逐节点核对尺寸/定位契约，逐状态核对布局操作，并逐复合控件核对 `compoundLayout` 与 `contentItems`。Manifest 还必须证明生成运行时具备并消费当前页面要求的相对约束、显式 Grid placement、集合尺寸、pinned supplementary 和状态重排能力。任一项未消费时不得接入 Xcode target。

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
