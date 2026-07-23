# Project Component Discovery

生成代码前建立工程组件索引。目的不是最大化复用数量，而是找到能保持项目一致性且不会损害高保真的组件、资源和导航设施。

## 执行

```bash
python3 scripts/inspect_ios_project.py <ios-root> --out ios-project-report.json
python3 scripts/discover_ios_components.py <ios-root> --out ios-component-index.json
```

索引覆盖 SwiftUI View/Style/Modifier、UIKit View/ViewController/Cell/UIControl、Router/Coordinator/Navigator、设计令牌和 Asset Catalog 资源。它是候选清单，不是自动复用结论。

## 复用判定

候选组件同时满足以下条件时优先复用：

1. 语义和可访问性角色一致。
2. 交互、状态和生命周期能够覆盖原型。
3. 尺寸、字体、颜色、形状和内容插槽可配置到目标外观。
4. 位于正确 target/module，且最低 iOS 版本可用。
5. 复用不会引入与当前页面无关的业务依赖。

仅名称相似、截图看起来接近或依赖已经安装，都不足以决定复用。无法满足视觉要求时可组合现有基础组件，或创建局部自定义 View/UIControl/ViewController，并记录不复用旧组件的理由。

## 工程状态

- `existing-xcode`：沿用已有 target、scheme、目录、组件和导航方式。
- `swift-package-only`：先判断 Package 是否就是目标 UI 模块；若用户需要独立 App，创建 Xcode App 工程并按需接入 Package。
- `empty-no-ios-project`：在用户指定或合理推断的输出目录创建可编译 iOS App 工程。

空工程没有可继承的 UI 架构，必须先让用户选择 SwiftUI 或 UIKit + Swift。不要把“Swift”和“SwiftUI”作为同一层级选项。使用：

```bash
ruby scripts/create_ios_project.rb \
  --root <output-root> \
  --name <app-name> \
  --ui-stack swiftui \
  --minimum-ios 16.0 \
  --bundle-id <bundle-id>
```

创建器使用 `xcodeproj` 生成工程，检测到现有 `.xcodeproj` 或 `.xcworkspace` 时会拒绝覆盖。工程名、Bundle ID 或最低版本无法可靠推断且会影响交付时，才询问用户；临时验证工程可使用明确标注的非生产 Bundle ID。

创建后必须重新运行工程检查和组件发现，再开始页面代码生成。若本机缺少 `xcodeproj` gem，应准确报告依赖缺失，不允许手写或正则拼接 `project.pbxproj`。

已有工程的技术栈检测以选定 target 的 source root 为范围，统计 Swift/Objective-C 文件、SwiftUI View 和 UIKit View/ViewController 证据，输出 `project-generation-decision.json`。排除 `Generated`、Pods、DerivedData 等目录，避免上一轮生成代码反过来污染项目判断。目标模块为混合栈或置信度不足时返回 `needs-input`；用户显式选择优先。纯 Objective-C 模块接入 Swift 代码时必须标记 Swift 集成复核，不能悄悄改变工程语言边界。
