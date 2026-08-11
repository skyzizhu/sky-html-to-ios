# Scroll And Attachment Contract

滚动、Safe Area、顶部/底部区域必须在代码生成前形成独立的可执行契约。目标是避免整页意外双轴滚动、同轴 Scroll 嵌套、固定栏随内容滚走，以及系统安全区被重复扣减。

## 产物与门禁

总控运行 `build_scroll_attachment_plan.py` 生成 `scroll-and-attachment-plan.json`，随后运行 `validate_scroll_attachment_plan.py`。这两个阶段不依赖截图或多模态模型。

计划为每个 screen 保存：

- 根 scroll owner 与唯一轴向；
- 每个滚动节点的 owner、coordinate space 和 directional lock；
- top/bottom region 的 `scroll-content`、`scroll-sticky` 或 `viewport-overlay` 归属；
- Safe Area owner 和系统 `contentInsetAdjustment` 策略；
- `viewportOccupancy`：根容器宽高所有权、底栏 overlay/docked 关系及底部空间唯一 owner；
- 底部输入区域是否使用 keyboard layout guide；
- 是否允许经过明确声明的数据表、画布、地图或图表根双轴滚动。

生成器必须消费该计划，并在 `native-structure-manifest.json` 中写入 `scrollAttachmentConsumption`。最终校验同时核对计划哈希、根轴向、Safe Area owner、区域节点、attachment 和 behavior；只生成计划但 Swift/Payload 未消费时不得接入 Xcode target。

## 区域规则

- `fixed`、`hide-on-scroll`、`collapse`、`appearance-change`：区域从内容树提升为 viewport sibling/overlay，由页面安全区坐标系拥有。
- `sticky`：区域留在对应 scroll owner 中，使用 pinned supplementary 或等价原生 sticky 实现。
- `scroll-away` 与普通文档 header/footer：保留在内容树中，不提升为固定栏。
- 行为 probe 只有 selector/runtime ID/node ID 精确匹配时才能覆盖架构计划；同一 edge 的其他候选不能被误套。
- 自绘栏位高度只追加一次。系统 Safe Area 不从 UIScrollView/ScrollView 的宽高预扣。

## Viewport Occupancy

Screen Container 永远拥有页面根容器的可用宽高，`framePolicy` 固定为 `fill-available-bounds`。来源 Screen Root 的实测高度仅用于校验，不能把 Scroll/Table/Collection/普通根 View 截成内容高度；页面底部存在系统 Home Indicator 时，也不得手工从容器高度再次扣除 Safe Area。

底栏使用 `viewport-overlay` 时，主内容 frame 仍延伸到物理屏幕底部，底栏作为 sibling/overlay 覆盖其上；`subtractBottomBarFromFrame` 必须为 false。内容避让只能由一个 owner 负责：来源滚动根已有 bottom padding 时使用 `source-padding` 且原生 additional inset 为 0；来源没有预留时使用 `native-content-inset` 并按栏高追加一次。`safe-area-inset` 底栏由原生 inset 机制拥有，不得同时保留同等来源 padding。

## 轴向规则

- 普通移动页面根默认只允许 `vertical` 或 `none`。
- 横向 carousel/collection 只拥有横轴，纵向拖动交给页面根。
- 同一轴向的嵌套 Scroll 默认阻断；Table/Collection 替换根滚动时，外层不得再包同轴 Scroll。
- 只有 `data-table`、canvas/diagram/map 或 `data-ios-scroll-root="both"` 的明确契约允许根双轴滚动。
- `overflow:hidden/clip` 只表示裁剪，不能单独证明可滚动。

## HTML 建议

标准 HTML、ARIA、computed CSS 和实测 scroll/client geometry 仍是主要证据。结构无法唯一表达时可使用：

```html
<main data-ios-app-root>
  <header style="position: fixed; top: 0"></header>
  <section data-ios-scroll-root="vertical"></section>
  <footer style="position: fixed; bottom: 0"></footer>
</main>
```

显式契约必须与真实布局和 JavaScript 行为一致；冲突时进入待确认项，不能静默覆盖。
