# Code Generation And Incremental Update

## 目标

把已校验、无未决原生所有权的多页面 UI IR 转换成可编译的 SwiftUI 或 UIKit 原生基线，并允许后续重新提取 HTML、重新生成和局部人工优化共存。

生成器不是架构替换器。现有项目已经有 Router、Coordinator、Design System、页面基类或同类组件时，先按项目模式接入或替换通用生成实现。

## 生成前置条件

开始代码生成前必须满足：

- 每份 UI IR 的 `schemaVersion` 为 `1.2`，且已经通过 `validate_ui_ir.py`。
- 多页面使用稳定且唯一的 screen ID。
- `requiresResolution=true` 的交互已经通过指纹一致的 overrides 解决；默认禁止带未决交互生成。
- SwiftUI/UIKit、target、最低 iOS 和 App 入口已经从工程或用户要求中确定。
- 工程组件发现已完成；明确哪些节点复用现有组件，哪些允许使用通用原生节点运行时。

## 生成命令

完整任务优先使用 `run_html_to_ios.py`；只有在调试代码生成阶段或 Agent 已经显式完成工程选择和 UI IR 校验时，才直接调用本节生成器。

每个 screen 的 IR 使用一个 `--ir`，顺序中的第一项是默认根页面：

```bash
python3 scripts/generate_ios_from_ir.py \
  --ir page1-ui-ir.json \
  --ir page2-ui-ir.json \
  --ir page3-ui-ir.json \
  --out-dir <source-root>/Generated/HTMLToIOS \
  --ui-stack swiftui
```

UIKit 将 `--ui-stack` 改为 `uikit`。如果所有 IR 中的 `target.uiStack` 一致，可以省略该参数。

默认产物按职责分层：

- `Application/HTMLToIOSGeneratedRoot.swift`：可接入 App 的根 View 或根 ViewController。
- `Core/Models/HTMLToIOSGeneratedModels.swift`：结构化页面、节点、交互和样式契约。
- `Core/Data/HTMLToIOSGeneratedData.swift`：bundle 资源加载与解码。
- `Core/Navigation/`：App 级 Navigation/Tab 容器和页面工厂。
- `Core/Runtime/HTMLToIOSGeneratedRuntime.swift`：SwiftUI/UIKit 原生节点、布局和交互运行时。
- `<Module>/Screens` 或 `<Module>/Controllers`：按业务模块归组的完整原生页面。
- `<Module>/Views`：页面内容和模块内封装控件。
- `Resources/Payload/HTMLToIOSGeneratedPayload.json`：多页面节点树、路由、状态和呈现载荷。
- `Resources/Assets/HTMLToIOSGeneratedAssets.xcassets`：转换后的本地资源。
- `.html-to-ios-generation.json`：输入指纹、生成文件哈希、入口符号和冲突报告。

业务模块优先读取 UI IR `screen.moduleId`；没有显式模块时只使用稳定 screen ID 前缀归组。跨模块组件进入 `Shared/Components/`；不生成空目录和空架构文件。完整命名、归属和迁移规则见 `generated-source-layout.md`。

SwiftUI 入口符号是 `HTMLToIOSGeneratedRootView`；UIKit 入口符号是 `HTMLToIOSGeneratedRootViewController`。

## 增量所有权

生成目录必须独立于人工源码目录。每次生成遵循：

1. 新文件直接创建。
2. 上次由生成器拥有且当前哈希未变化的文件可以更新。
3. 内容已经与目标一致的文件保持不变。
4. 人工修改过或没有所有权记录的同名文件绝不覆盖。
5. 新候选写入默认兄弟目录 `<out-dir>.conflicts/<file>.generated`，并在清单中标记 `preserved-user-modified`。
6. 只有用户明确要求放弃人工修改时才允许 `--overwrite-modified`。

冲突后的后续生成仍必须保持 `owned=false`；不能因为清单记录了人工版本的新哈希，就在第三次运行时重新获得覆盖权。

## Xcode 接入

传统 `.xcodeproj` 使用：

```bash
ruby scripts/integrate_generated_sources.rb \
  --project <path/App.xcodeproj> \
  --target <TargetName> \
  --generated-dir <source-root>/Generated/HTMLToIOS
```

脚本只做以下操作：

- 将 `.swift` 加入指定 target 的 Compile Sources。
- 将非隐藏 `.json` 加入 Copy Bundle Resources。
- 复用现有文件引用，重复运行不重复添加。
- 不改 AppDelegate、SceneDelegate、SwiftUI App、Router 或现有根页面。

Xcode 同步文件夹或 SwiftPM target 根据工程现有规则放入目录，不要重复创建传统文件引用。

## 入口接入

入口修改必须由 Agent 在读取现有启动结构后完成：

- SwiftUI：在现有 `WindowGroup`、路由根或目标 feature 中使用 `HTMLToIOSGeneratedRootView()`。
- UIKit：在现有 Coordinator 或 window 启动路径中使用 `HTMLToIOSGeneratedRootViewController()`。
- 现有 App 有登录态、Tab、Deep Link 或依赖注入时，把生成页面接入现有流程，禁止直接替换整个 App 根控制器。
- 新建空工程可以直接将模板 `ContentView`/`ViewController` 替换为生成入口，但仍需构建和启动验证。

## 通用运行时边界

生成器提供的运行时是可编译的原生基线，适合常见文本、容器、按钮、输入、开关、图片占位、进度、push/pop 和 modal 状态。以下情况必须做项目化替换：

- 已发现可复用的项目组件或 Design System 控件。
- Collection/Table 的数据复用、复杂 cell 生命周期或大数据性能要求。
- 自定义绘制、复杂手势、Canvas/WebGL、富文本编辑器或媒体播放。
- Alert、Picker、Menu、Context Menu 等需要精确系统行为和数据模型的控件。
- 交互包含网络、权限、支付、认证、文件访问或其他原型之外的业务副作用。
- CSS 动画、滤镜和混合模式无法由通用样式字段表达。

完成替换后仍保留原 UI IR node ID 作为 `accessibilityIdentifier`，以便状态截图和差异定位。

## 验收

至少验证：

1. 清单中的 screen ID、入口符号、输入哈希和 file status 正确。
2. Payload 中 push/present/pop/dismiss、自动延迟和 presentation state 与已解决交互图一致。
3. 生成目录接入了正确 target，JSON 在 Copy Bundle Resources 中。
4. 使用 deployment target 对应的 iOS Simulator SDK 执行 `xcodebuild`。
5. 进行入口运行、路由状态和视觉截图验证；仅编译成功不等于高保真完成。
