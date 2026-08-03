# Generated Native Structure Consumption

生成前的 `structural-fidelity-report.json` 证明 UI IR、布局关系图与六层原生架构计划彼此一致；它不能单独证明生成器最终写出的 Swift 与 Payload 没有遗漏这些契约。因此，代码生成后、Xcode target 接入前必须执行第二道确定性结构门禁。

该门禁不依赖截图、Simulator 或模型多模态能力。

## 固定产物

- `native-structure-manifest.json`：由生成器写出，记录实际原生消费证据。
- `native-structure-validation.json`：独立验证器输出的门禁报告。

`native-structure-manifest-1.0` 必须绑定当前 `native-architecture-plan.json`、`layout-relation-graph.json` 与 `.html-to-ios-generation.json` 的 SHA-256，并按 screen 保存：

- 每个关系图节点是 `represented`、`optimized-equivalent` 还是 `missing`；
- containment、视觉顺序、等宽/等高、正方形比例、对齐、重叠层级与滚动轴的消费策略和证据；
- Content Container 及 top/bottom Screen Region 的实际所有权；
- 承载该 screen 的 Swift、Runtime 与 Payload 文件路径和内容哈希；
- 未消费关系、缺失节点与生成冲突汇总。

## 原生等价转换

HTML 节点不一定仍作为页面 Content 子树中的普通节点存在。以下转换属于允许的原生等价消费，但必须保留稳定 node ID 和所有权证据：

- top/bottom region 从滚动内容中提升为独立原生栏位；
- sheet、popover、full-screen cover 等节点提升为 presentation；
- Cell 左滑、上下文菜单等节点提升为 contextual action；
- 没有视觉内容和行为的结构包装被原生 Stack/View 合并；
- 装饰性内部节点由父级系统控件、Shape、Layer 或项目组件吸收。

节点同时存在于 Payload 且被提升到原生层时，按“该约束是否仍有有效原生证据”判断，不能用 Payload 是否包含该 ID 作为唯一依据。例如，提升后的状态层可能保留追溯记录，但不再携带页面内 `preferredWidth`。

## 生成后硬门禁

`scripts/validate_native_structure_manifest.py` 必须在 `integrate_generated_sources.rb` 之前运行。以下任一情况停止接入：

1. 原生架构计划、布局关系图或生成清单哈希不一致，或 screen/node/relation 集合不一致。
2. 节点状态为 `missing`，且没有受支持的原生等价优化证据。
3. 任一布局关系状态不是 `consumed` 或 `optimized-equivalent`。
4. Content Container、top region 或 bottom region 与架构计划的 owner 不一致。
5. 结构消费文件缺失，或文件哈希与生成清单不一致。
6. 增量生成保护了用户改动，导致本轮结构消费者没有实际更新。
7. 生成清单仍包含未解决冲突。

门禁失败必须回到 UI IR、架构计划或生成器修复。禁止通过删除关系、伪造 manifest、放宽哈希检查或直接修改用户工程中的 Swift 绕过。

## 单阶段调试

生成器读取关系图并写出消费清单：

```bash
python3 "$SKILL_ROOT/scripts/generate_ios_from_ir.py" \
  --ir page-ui-ir.json \
  --out-dir App/Generated/HTMLToIOS \
  --ui-stack swiftui \
  --architecture-plan native-architecture-plan.json \
  --layout-relation-graph layout-relation-graph.json \
  --native-structure-manifest native-structure-manifest.json
```

随后执行独立门禁：

```bash
python3 "$SKILL_ROOT/scripts/validate_native_structure_manifest.py" \
  --manifest native-structure-manifest.json \
  --layout-graph layout-relation-graph.json \
  --architecture-plan native-architecture-plan.json \
  --generated-dir App/Generated/HTMLToIOS \
  --generation-manifest App/Generated/HTMLToIOS/.html-to-ios-generation.json \
  --out native-structure-validation.json
```

只有验证通过后才能把生成目录关联到指定 Xcode target。
