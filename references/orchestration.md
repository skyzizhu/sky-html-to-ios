# Workspace Orchestration

## 默认入口

从 Agent 当前工作目录执行总控。`--workspace` 省略时使用当前目录：

```bash
python3 scripts/run_html_to_ios.py --html <prototype.html>
```

已经完成 UI IR 与交互 resolution 时，可以跳过浏览器提取：

```bash
python3 scripts/run_html_to_ios.py \
  --ir page1-ui-ir.json \
  --ir page2-ui-ir.json
```

总控报告默认写入 `<workspace>/.html-to-ios/orchestration-report.json`，每个外部命令有独立日志。

## 总控顺序

1. 检查输入文件和当前工作目录。
2. 扫描 `.xcodeproj`、`.xcworkspace` 与 `Package.swift`。
3. 确定 SwiftUI/UIKit、App target、shared scheme 和 target deployment target。
4. HTML 模式先发现 route/interaction，校验 overrides；未解决交互在创建工程前停止。
5. UI IR 模式先校验所有 IR；无效 IR 在创建工程前停止。
6. 没有 Xcode 工程时创建 App 工程。
7. 发现项目组件并核验本机 SDK。
8. HTML 模式逐 screen 提取 render tree、截图、生成并校验 UI IR。
9. 默认逐 screen 生成文本标定、多尺寸响应式分析、视觉状态清单和 HTML 状态基准图。
10. 生成原生代码和 Payload，接入指定 target。
11. 新建工程自动接入根 View/根 ViewController；现有工程只检测入口，不覆盖启动架构。
12. 默认执行 iOS Simulator `xcodebuild`。
13. 入口已接通时，创建隔离的 generator-owned UI Test target，逐 screen 执行状态动作、导出 xcresult 截图并归一化到目标逻辑 viewport。
14. 对 required states 执行节点分区视觉门禁；任一状态缺失或超阈值时总控返回 `failed`，保留 review bundle 供 Agent 局部纠偏。

## 工程决策

### 没有工程

`empty-no-ios-project` 默认创建 SwiftUI App；用户指定 `--ui-stack uikit` 时创建 UIKit App。项目名默认由 workspace 目录名安全转换，无法转换时使用 `HTMLToIOSApp`。可以用 `--app-name` 和 `--bundle-id` 覆盖。

使用 `--no-create` 可以禁止创建；此时返回 `needs-input`。

### 一个工程

自动选择唯一 `.xcodeproj`。优先选择与工程名同名的非测试 target 和 scheme；无法唯一判断时停止并要求 `--target` 或 `--scheme`。

生成目录默认是 `<project-parent>/<target>/Generated/HTMLToIOS`；目标源码目录不符合该结构时传 `--source-root`。

### 多个工程

禁止猜测。返回候选并要求：

```bash
--project path/App.xcodeproj
```

### Swift Package

只有 `Package.swift` 而没有 App 工程时，不自动假设需要宿主 App。确认需要后传：

```bash
--create-package-host-app
```

### Workspace/CocoaPods

源码仍接入明确的 `.xcodeproj` target。项目旁只有一个 `.xcworkspace` 时优先用它构建；有多个 workspace 时使用 `--xcode-workspace` 明确指定。

## HTML 模式

总控会调用 route discovery、dynamic interaction discovery、render extraction、UI IR build 和 validation。出现原生导航所有权歧义时，使用：

```bash
--interaction-overrides html-to-ios.overrides.json
```

总控不得自动采用 `recommended` 冒充用户 resolution。未解决项返回 `needs-input`，并保留 route graph、interaction graph 和 overrides 草稿。

传入 `--interaction-overrides` 时，该文件视为用户确认契约，只读使用。新的自动发现草稿写入 `html-to-ios.overrides.generated.json`，禁止覆盖用户文件。

本地 HTML 自动寻找可用 Node.js。优先级是 `--node`、`CODEX_NODE`、PATH 和 Codex bundled runtime；bundled runtime 存在时自动设置对应 `NODE_PATH`。

默认响应式探针宽度为 `320,375,393,430`，可用 `--responsive-widths` 调整。`--skip-visual-baselines` 只允许用于定位流水线故障；它会跳过视觉状态 manifest 与 HTML 状态截图，不能用于正式视觉验收。

每个 screen 的默认产物位于 `<report-dir>/screens/<screen-id>/`：

- `render-tree.json` 与 `html-baseline.png`
- `ui-ir.json`
- `text-calibration.json`
- `responsive-layout.json`
- `visual-state-manifest.json`
- `visual-states/html/captures.json` 与同名状态截图
- `visual-states/ios/captures.json` 与同名逻辑尺寸截图
- `visual-review/review-bundle.json`、逐状态 diff/overlay/comparison/regions

## 入口策略

- 新建 SwiftUI 工程：将模板 `ContentView` 接到 `HTMLToIOSGeneratedRootView()`。
- 新建 UIKit 工程：将模板 AppDelegate 根控制器接到 `HTMLToIOSGeneratedRootViewController()`。
- 现有工程：不改 App、SceneDelegate、登录态、Tab、Deep Link、Router 或 Coordinator。入口未接入时状态为 `generated-needs-entry-integration`，由 Agent读取项目架构后完成。

## 状态

- `completed`：代码生成、target 接入、入口确认、构建、required iOS 状态截图和确定性视觉门禁均通过。多模态复核仍按 capability gate 单独记录。
- `built-pending-visual-acceptance`：使用了 `--skip-build`、入口未就绪或其他显式诊断路径，视觉链路尚未执行。
- `generated-needs-entry-integration`：代码已生成并编译，但现有 App 流程还没有使用生成入口。
- `needs-input`：多工程、多 target、混合技术栈、Swift Package 宿主、未解决交互或 API 版本需要人工决定。
- `failed`：输入、工具、生成、工程接入或构建失败。
- `planned`：`--dry-run` 只输出工程决策，不写文件。

`qualityGates` 独立记录 UI IR、文本标定、响应式分析、HTML 基准、构建、iOS 状态截图和 visual diff。`iosStateCapture` 或 `visualDiff` 不是 `passed` 时，只能声称已完成对应前置阶段，不能声称高保真验收通过。

## 安全约束

- 无效 UI IR 或未解决交互必须在创建工程前停止。
- 不覆盖已有 Xcode 工程，不用正则修改 `project.pbxproj`。
- 现有 App 入口不自动改写。
- 生成文件继续遵守增量 ownership manifest。
- target deployment target 从选定 target 的 Build Settings 读取，不使用其他 target 的汇总值代替。
- `--skip-build` 只用于诊断；正式交付仍必须构建。
- `HTMLToIOSVisualTests` 只允许修改带 `HTML_TO_IOS_MANAGED_VISUAL_TESTS=YES` 标记或可证明位于 `.html-to-ios` 的旧托管 target；现有同名业务 target 必须拒绝覆盖。
- UI Test 源码与 DerivedData/xcresult 放在 `.html-to-ios`，业务生成源码仍只位于 `Generated/HTMLToIOS`。
