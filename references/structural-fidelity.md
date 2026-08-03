# Structural Fidelity Without Screenshots

结构还原是核心转换门禁，不依赖截图、多模态模型或 Simulator。它用于确认浏览器事实经过 UI IR 和六层原生架构后没有发生结构性丢失。

## 产物

总控在 `native-architecture-plan.json` 之后、Swift 代码生成之前固定生成：

- `layout-relation-graph.json`
- `structural-fidelity-report.json`

`layout-relation-graph-1.0` 保存：

- source node 与原生节点可追溯 ID；
- parent-child containment；
- 每个容器的视觉子节点顺序，而不只是 DOM 顺序；
- horizontal、vertical、grid 与 overlay 轴向；
- 相邻节点、实测 gap、对齐和等宽/等高关系；
- 接近正方形节点的 aspect-ratio 证据；
- scroll axis 的唯一 owner；
- overlap 与前后层级关系。

## 来源覆盖

HTML 模式的每个 Screen 必须在 UI IR 中写入 `sourceCoverage`：

- `rootSubtreeNodeCount`
- `routeScopedNodeCount`
- `mappedNodeCount`
- `excludedByRouteCount`
- `excludedNonVisualOrUnsupportedTagCount`
- `mappedRatio`

被其他 route 排除的节点与 script/style/hidden input 等非视觉节点必须分别计数。禁止用一个总数掩盖未映射的可见节点。

直接传入已有 UI IR 时，缺少 `sourceCoverage` 只标记为 `not-applicable`，因为总控没有浏览器来源可重新核算；其余结构检查仍然执行。

## 硬门禁

以下任一问题必须阻止原生代码生成：

1. UI IR 节点没有进入关系图，或关系图出现未知节点。
2. parent-child ownership 缺失、重复或指向错误。
3. 容器视觉顺序丢失、重复子节点或改变 child set。
4. 原生架构计划改变浏览器推导出的视觉顺序。
5. scroll axis 与 owner 不一致。
6. 六层架构漏掉叶子原生组件。
7. 自绘顶部栏、底部栏或 Tab 的 owner 与 Screen Regions 不一致。
8. HTML 模式下交互控件缺少 system-first 决策或丢失系统交互语义。
9. 来源覆盖计数无法闭合，或非 synthetic 的基础节点无法追溯到 runtime ID。状态容器、伪元素包装等明确标记的 synthetic 节点必须保留生成来源，但不要求伪造浏览器 runtime ID。

结构门禁通过只说明布局与原生规划契约一致，不等于像素级视觉验收通过。颜色、阴影、字体栅格化和系统控件内部绘制差异仍可在用户选择 `visual` 时进入截图验收。

## 单阶段调试

```bash
python3 "$SKILL_ROOT/scripts/build_layout_relation_graph.py" \
  --ir page-ui-ir.json \
  --out layout-relation-graph.json

python3 "$SKILL_ROOT/scripts/validate_structural_fidelity.py" \
  --ir page-ui-ir.json \
  --layout-graph layout-relation-graph.json \
  --architecture-plan native-architecture-plan.json \
  --out structural-fidelity-report.json
```

修复应回到 render extraction、UI IR 或 architecture planning。禁止直接修改生成后的 Swift 顺序来绕过门禁。
