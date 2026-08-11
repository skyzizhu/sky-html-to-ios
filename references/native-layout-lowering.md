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

当前输出 schema 是 `native-layout-plan-1.2`。代码生成与下游验证仍可读取旧版 `native-layout-plan-1.1`，但只有 `1.2` 强制每个可执行容器提供 `container-geometry-system-1.0`。计划按 Screen 保存 Content Container、所有有意义容器、每个节点的盒模型、Flex/Grid item、定位参照系、状态布局增量和复合控件槽位。

## 容器降级

每个容器必须固定记录并由两套生成器共同消费：

- `axis`：horizontal、vertical、grid 或 overlay；
- `layoutAlgorithm`：stack、wrapping-stack、grid 或 positioned-overlay；
- `orderedChildNodeIds`：浏览器最终视觉顺序；
- `paintOrderNodeIds`：浏览器 stacking contract 推导的稳定绘制顺序；
- 独立 row/column gap、alignment、distribution、wrap、reverse 和 writing direction；
- 每个子项的 fixed、intrinsic 或 flexible 尺寸策略；
- measured width/height、aspect ratio、flex grow/shrink 和 compression resistance；
- 关系图中支撑该决策的 relation IDs。

跨父容器但处于同一页面坐标系的 leading、trailing 或 center-x 边缘必须形成 `alignmentLanes`。例如 section header 与其下方横向 Collection 的首个 item 即使父节点不同，也应共享同一 leading lane。Lane 记录目标坐标、参与节点、不同父容器数量、最大偏差和容差；Native Layout Plan 必须逐节点保留 lane ID。不得分别从每个局部容器重新猜一份 padding，也不得用视觉截图纠正本可由 DOM 几何确定的跨容器对齐。

SwiftUI 使用这些证据选择 Stack/Grid/Overlay、spacing、frame 和 layout priority。UIKit 使用相同证据配置 `UIStackView`、Auto Layout、Table/Collection item sizing 与 hugging/compression priority。禁止技术栈各自重排子节点。

固定 border-box 的尺寸与盒内内容对齐是两个独立契约。SwiftUI 所有 fixed/min/max frame 必须显式传入由容器 axis、`justify-content`、`align-items`、Grid `justify-items` 和文字 `text-align` 推导的 Alignment，不能使用 `.frame` 的默认居中；UIKit 使用同一证据配置 Stack alignment/distribution 与文字 alignment。横向复合控件中的 flexible 文本槽先获取剩余宽度，再在槽内按来源 left/center/right 对齐，禁止扩展槽位后把文字默认居中。

`widthFraction` 始终等于节点 border box 宽度除以直接父内容框宽度；只有 screen root 才使用目标 viewport 宽度。接近父宽的普通流节点优先生成父级填充约束，absolute/fixed、Overlay、横向滚动 item、动画节点和紧凑文字保留独立尺寸。CSS Grid 若没有子 View、只有一个直接文本或图标槽，只承担单槽对齐职责，SwiftUI/UIKit 使用填满父级的 Stack/ZStack/普通 View；不得创建会按内容收缩的 Lazy Grid 或 Collection。

混合普通流与 positioned 子节点时，先用浏览器矩形判断实际重叠。若 positioned 子节点覆盖普通流内容且承担同一视觉层级，所有相关子节点按最终绘制顺序进入同一 Overlay/ZStack，并保留容器实测尺寸；若没有实质重叠，普通流继续参与 Stack/Grid 测量，positioned 子节点单独挂到 overlay 层。禁止把所有 absolute 节点一概移出后破坏前后层级。

`orderedChildNodeIds` 与 `paintOrderNodeIds` 承担不同职责：前者用于 Stack/Grid、intrinsic size 和普通流测量，后者只决定重叠绘制的前后层级。关系图对直接子节点的真实矩形交叠生成 overlap-order 关系，并保存 paint group、stacking level 与 source order 证据；SwiftUI ZStack/overlay 和 UIKit overlay subviews 必须消费同一顺序。不得只比较 `z-index`，也不得为了层级正确而重排普通流测量顺序。

computed style 对普通块元素同样可能返回默认 `flex-direction: row`，它不能单独证明横向布局。只有 `display:flex/inline-flex`、Grid/row mode 或浏览器实测的单行 inline 文本证据可以选择横向容器；普通 `display:block` 默认保持纵向文档流。混合普通流与 positioned 子节点时，结构契约必须分别保存 flow order、positioned ownership 和覆盖全部直接子节点的 paint order。

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

子项尺寸策略必须优先读取获胜的 authored declaration。浏览器 computed `width`/`height` 通常都会解析成 px，它只代表当前采样结果；只有 authored 固定长度才能生成 required fixed constraint。authored 百分比、`width:100%`、含百分比的 `calc()` 和 fill-available 类语义必须保持 parent-filling/flexible，由父内容框和 Auto Layout/SwiftUI Layout 决定最终尺寸。

原生运行时必须消费可确定的 min/max、padding、border、margin 和 compression 证据。transform 只改变视觉绘制时，不得反向污染正常流尺寸。

## 叶子内容几何

`native-layout-plan.json` 的每个节点必须包含 `contentGeometry`。它不替代盒模型，而是描述边框盒内部最终原生内容如何保持浏览器度量：

- `sourceWidthPt`、`sourceHeightPt` 和槽位 `gapBeforePt` 使用 UI IR 已归一化的目标 pt，不得再次乘 `designScale`；CSS padding、margin、字号等来源样式 token 仍按既有规则只缩放一次；
- `boxModel` 中的 border/content box 与 min/max `*Pt` 字段同样已经归一化为目标 pt，Payload 生成不得二次缩放。百分比子项相对父内容框解析；UIKit 父级以 layout margins 表达 padding 时，约束必须指向 `layoutMarginsGuide`，不能指向外层 bounds 后再额外保留 padding。
- padding 必须只有一个原生 owner。普通 Stack 容器可用 layout margins 表达；复合 `UIControl`/Button wrapper 若已用四边约束把内容 Stack inset 到 content box，内层 Stack 必须关闭自身 padding 消费。禁止 wrapper insets 与 Stack layout margins 叠加两次。
- 圆角背景/渐变与外阴影必须分层消费：宿主层保留不裁剪以绘制阴影，背景图或 `CAGradientLayer` 自身应用统一 corner radius 或逐角 mask。禁止为了保留阴影而让渐变恢复成矩形，也禁止裁剪宿主层导致阴影消失。
- `widthMode` / `heightMode` 区分 fixed、intrinsic、flexible 与 parent-relative；
- 图标、图片和紧凑视觉包装保存来源宽高与宽高比，防止 Stack/Grid 拉伸成错误形状；
- 单行文字、徽标和图标保存 compression resistance 与 intrinsic-width 所有权；
- 媒体保存 object-fit/object-position 对应的 content mode 和位置；
- 普通响应式大图不得仅因浏览器测得一个宽度就降级成固定宽度。
- 横向复合槽位用浏览器实测高度、行高和 `white-space` 共同判断单行。测得的文字宽度是 intrinsic/ideal 证据，不自动生成 required 固定宽度；否则原生字体略宽时会被裁切。
- `normal`/`flex-start` 容器的未占用宽度由尾部弹性 Spacer 吸收，不能拉伸首个文字槽并把相邻 Badge 或图标推到末端。
- CSS 重叠圆角在目标盒子归一化后缩减；父控件状态色不得覆盖具有独立计算色或富文本 run 的后代。

复合控件的 `orderedSlots` 在视觉顺序之外还必须保存每个槽位的 `contentGeometry`、`gapBeforePt` 和 `flexibleGapBefore`。普通 gap 使用实测固定间距；`space-between` 或明确的 auto margin 使用弹性 Spacer。生成器消费槽位契约后，必须清除已经由父布局消费的对应 margin，避免重复占宽。

每个节点的 `compositing` 契约保存 source order、paint group、stacking level、stacking context owner、创建原因、clip owner、clip-path、mask、blend mode 与 isolation。圆角绘制和子树裁剪必须分开消费：corner radius 可以只作用于背景/边框，只有 `clipsOwnContent` 或等价来源证据才裁剪后代。复杂 blend/filter/mask 没有原生等价实现时保留证据并显式降级，不能静默当成普通透明度。

## 定位与状态布局

每个节点保存 static、relative、absolute、fixed 或 sticky 定位方案。absolute 使用最近的 positioned ancestor，fixed 使用 viewport，sticky 使用最近 scroll owner；同时保存 containing block、相对偏移、insets、z-index、transform 和 transform origin。定位节点不得默认相对屏幕居中，也不得改变普通流兄弟节点的尺寸分配。

浏览器提取的 `offsetParentRuntimeId`、`scrollAncestorRuntimeId` 与对应 rect 是定位所有权的首选证据。只有这些节点不在当前 Screen 闭包内时，才允许按 CSS positioned ancestor/scroll ancestor 规则回退。生成 Payload 时定位节点必须重挂到 `nativeOwnerNodeId`，偏移也必须在同一 owner 坐标系计算。

horizontal/vertical 容器必须逐项保留 `gapBeforePt`，整体 row/column gap 只作为缺失几何时的 fallback。逐项 spacing contract 同时保存 signed 实测 border-box gap、authored CSS gap、前项 trailing margin、当前项 leading margin、残差和 `fixed|flexible|overlap` 模式，便于区分真正 gap、margin 分组、弹性分配和负间距重叠。实测最终距离拥有首次生成的几何优先级；这些 `*Pt` 已经处于目标坐标系，生成器不得再次乘设计倍率。原生端消费后必须清除相邻主轴 margin，防止 CSS margin 与 Spacer 被重复计算。

## 容器几何系统

每个可执行容器都必须包含 `geometrySystem`，由布局计划在代码生成前完成主轴尺寸决策。求解顺序固定为：父内容盒、intrinsic 测量、父相对尺寸、剩余空间分配、交叉轴对齐。每个直接子项保存 `mainAxisSizingMode`、来源模式、来源尺寸、权重、min/max、抗压缩和前置间距。

主轴默认是 `source-sized`。只有以下强证据可以使用 `equal-share`：全部子项具有相等的正 `flex-grow`；全部子项具有相等的父相对尺寸；或横向 Stack 中没有显式固定宽度，实测宽度稳定相等且连同间距完整占满父内容盒。显式固定宽度、横向滚动 item、intrinsic 图标/标题/计数混排不能因为节点重复或宽度接近而均分。

Payload 必须保存容器的 `stackDistributionMode` 与 `geometrySolveOrder`，子节点 Layout Contract 必须保存 `mainAxisSizingMode` 和权重。SwiftUI/UIKit 只执行该结果；生成后结构清单逐容器核对分配模式、求解顺序和全部子项，不允许 UIKit 根据当前 arrangedSubview 数量临时选择 `.fillEqually`，也不允许 SwiftUI 重新猜测无限宽度。

对齐值先归一化为 `start|center|end|stretch|baseline`，再按轴映射。横向 Stack 的 cross-axis 对应 top/center/bottom/fill/firstBaseline，纵向 Stack 对应 leading/center/trailing/fill；`text-align` 只控制文字绘制，不能偷偷改变容器子项对齐。Grid 的 `justify-items` 与 `align-items` 分别控制水平和垂直 item 对齐。

控件 border box 与控件内容具有独立对齐层。普通 `button`、`UIControl`、胶囊和卡片先读取 computed `text-align`、`justify-content`、`align-items`，再用子内容左右/上下剩余空间做几何校验；两侧剩余空间在容差内相等时确认 center，单侧贴边时确认 start/end。浏览器原生按钮的默认居中可作为语义 fallback，但不得覆盖明确 CSS 或实测几何。UIKit 不得把所有按钮统一设为 `.leading`，SwiftUI 不得依赖未声明的 `.frame` 默认值。

同页状态使用 `stateLayouts` 保存 insert/remove/replace 对应的目标父容器、生成节点布局和原节点基线布局。状态节点仍消费普通 Node Layout Contract；状态变化不得另建一套截图坐标或独立页面。

没有重复状态画板时，JavaScript/CSS class toggle 仍可能改变目标节点几何。隔离浏览器 probe 的 before/after width、height 与 overflow 差异必须生成可逆的 layout-only State Variant；首次触发应用尺寸/滚动覆盖，再次触发恢复基线。内容没有变化时禁止用空 items 覆盖并删除原子树。若被选作 Screen Content Root 的节点位于 HTML 可滚动祖先内，而该祖先不进入原生内容树，则将其唯一纵向或横向 Scroll ownership 继承到 Screen Root，并保留来源 runtime ID/selector 证据。

交互归属与视觉语义分开处理。可点击文本、图片或组合 Stack 必须由原生 Control 宿主接收事件，显示子视图关闭 hit testing；输入控件、系统 Control 和 Scroll 容器继续保留自身交互。Presentation 必须同时保存触发器 anchor rect 与内容 panel rect：系统 Popover 消费 anchor，自定义 Overlay/Popover 消费 panel 的位置和尺寸。

系统导航栏显隐是 Application Container 的首帧职责。UIKit 在创建 `UINavigationController`、push 与 replace 前同步应用目标页面策略，不能只依赖被嵌入 child controller 的 `viewWillAppear`；SwiftUI 的 toolbar visibility 也必须与目标路由首帧一致。

Screen Content Root 的宽度由 Screen Container 响应式约束持有，来源单次实测宽度不能继续作为根节点 fixed-width。非滚动静态页面从系统 Safe Area 顶部开始，保留内容的 intrinsic/measured height，并以底部 `lessThanOrEqual` 限制可用范围；不能同时保留固定根高度又用 top/bottom 等式把它强制拉满屏幕。滚动页面仍使用完整父 bounds 与系统自动 inset。

Screen Root 的来源高度同样只作为几何验收证据，不进入 Payload `fixedHeight`。根内容由自身 intrinsic height、唯一 Scroll owner 和 Screen Container 可用高度共同解析；子级 authored 固定卡片/行高仍可保留。生成后门禁必须同时验证根 fixed height 已清除，以及 SwiftUI/UIKit 均消费盒内内容对齐契约。

UIKit 页面级 typed wrapper 和模块 ContentView 必须在加入 Screen Container 前设置 `translatesAutoresizingMaskIntoConstraints = false`。结构清单不能只证明文件和类型存在，还要保证根 wrapper 由 Auto Layout 接管；运行时根 frame 为零而子树依靠 unclipped overflow 显示属于硬失败。

透明文字颜色配合 CSS 渐变和 `background-clip:text` 时，渐变边界是字形，不是节点 border box。原生优先使用文字遮罩；无法精确遮罩时，使用首个有效渐变色作为稳定文字前景，禁止把渐变层绘制成 UILabel 或文本 View 的矩形背景。

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

固定 Visual Root 使用 cover 归一化时，布局契约保存居中裁切 inset。viewport-fixed chrome 按对应边补偿锚点；普通内容仍由 Auto Layout/SwiftUI Layout 与系统 Safe Area 管理。

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
9. 每个容器的 paint order 完整、唯一并满足全部 overlap-order 关系；stacking context owner 和 overflow clip owner 必须闭合。
10. 每个容器具有完整 `geometrySystem`，其 owner、axis、视觉子项顺序、尺寸模式、equal-share 权重和五阶段求解顺序可执行。
11. 跨容器 Alignment Lane 与每个节点的 lane membership 完整一致，且最大偏差不超过来源容差；内容水平/垂直对齐值可被两套原生栈执行。

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
