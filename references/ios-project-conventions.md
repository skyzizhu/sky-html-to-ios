# iOS Project Conventions

生成前先理解现有工程。项目已有模式优先于本文件。

## 检查顺序

1. 读取用户当前指令。
2. 读取目标模块附近的 2–4 个同类页面。
3. 读取 `AGENTS.md`、`CLAUDE.md`、`.cursorrules`、`.cursor/rules/*.mdc`、Copilot instructions 和项目明确引用的规范。
4. 检查 package、Podfile、工程配置和最低 iOS 版本。
5. 运行组件发现，索引可复用 View、Control、Cell、Router、设计令牌和资源。
6. 汇总本次转换的生效约定。

不要递归读取所有 `README` 和任意包含 `plan` 的文档；只读取与目标工程或目标模块直接相关的规则，避免把历史方案误当成当前规范。

## 技术栈推断

- 目标目录和相邻页面使用 SwiftUI：使用 SwiftUI。
- 目标目录和相邻页面使用 `UIViewController`：使用 UIKit。
- 两者都有：以目标导航入口和相邻功能为准。
- 新项目：没有现有约定可继承，必须由用户明确选择 SwiftUI 或 UIKit + Swift。

工程为空时按 `project-component-discovery.md` 创建真实可编译 App 工程，再重新执行工程与组件扫描。存在 Swift Package 但不存在 App 工程时，不把两者混为一谈。

不要把“Swift”列成 UIKit、SwiftUI 之外的第三种 UI 技术栈。

## 架构

- 项目已有 MVVM、TCA、VIPER、Coordinator、Router 或自定义基类时沿用。
- 静态展示页面可以只有 View；不要为形式完整强制生成空 ViewModel。
- 有可测试状态、异步数据或多处状态共享时，再引入项目现有状态管理方式。
- 不创建全局 Router 覆盖项目已有导航体系。

## 依赖

- 检测到 SnapKit、Kingfisher、SDWebImage 等依赖，只表示可以复用，不表示必须使用。
- 不为一个页面增加新的第三方依赖，除非原生实现成本明显更高且用户同意。
- 已有 Design System、颜色 Token、字体 Token 和组件库优先复用；视觉不一致时记录局部覆盖原因。

## 命名与文件组织

- 沿用目标模块命名方式、访问控制、文件头和本地化策略。
- 通过 `native-naming-plan.json` 固化页面文件和类型前缀；新项目默认 `Sky`，已有项目优先复用稳定前缀，后续生成继承上一轮计划。
- 组件按职责和复用拆分。以下情况通常值得独立组件：重复出现、有独立状态、有复杂绘制、明显降低父 View 复杂度。
- “三个以上子控件”不是拆分依据。
- 新增公共扩展前先搜索项目是否已有等价实现。

## 默认值

仅在项目完全没有约定时使用：

- SwiftUI：按 screen 建 View，局部状态使用 `@State`，可观察模型按项目支持的系统版本选择 Observation 或 `ObservableObject`。
- UIKit：`UIViewController` + 独立根 View，使用原生 Auto Layout。
- 颜色：sRGB，保留 alpha。
- 字体：iOS 系统字体或原型提供的本地字体。
- 导航：使用页面入口上下文中最简单的原生方式。
- 最低系统版本：从工程设置取得，不自行提高。
