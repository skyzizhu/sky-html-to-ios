# Generated Source Layout

生成代码必须与人工源码隔离，并使用稳定、可递归接入 Xcode target 的分层目录。禁止将所有 Swift、JSON 和 Asset Catalog 平铺在同一目录。

## 标准目录

```text
Generated/HTMLToIOS/
├── .html-to-ios-generation.json
├── Application/
│   └── HTMLToIOSGeneratedRoot.swift
├── Core/
│   ├── Data/
│   ├── Models/
│   ├── Navigation/
│   │   ├── HTMLToIOSGeneratedNavigation.swift
│   │   └── HTMLToIOSGeneratedScreenFactory.swift
│   └── Runtime/
├── Home/
│   ├── Screens/                 # SwiftUI
│   │   ├── SkyHomeScreen.swift
│   │   └── SkyHomeDetailScreen.swift
│   ├── Controllers/             # UIKit
│   │   ├── SkyHomeViewController.swift
│   │   └── SkyHomeDetailViewController.swift
│   └── Views/
│       ├── SkyHomeContentView.swift
│       └── SkyHomeHeaderView.swift
├── ArticleList/
│   ├── Screens/ | Controllers/
│   └── Views/
├── Shared/
│   ├── Components/
│   ├── Styles/
│   └── Extensions/
└── Resources/
    ├── Payload/
    │   └── HTMLToIOSGeneratedPayload.json
    └── Assets/
        └── HTMLToIOSGeneratedAssets.xcassets
```

最外层页面目录代表业务模块，不是单个 DOM 页面。SwiftUI 模块生成 `Screens/` 和 `Views/`，UIKit 模块生成 `Controllers/` 和 `Views/`；不会同时生成无用的 `Screens` 与 `Controllers`。页面文件和 `ContentView` 必须实际进入路由与渲染链路，不得为了目录外观生成空 View、空 ViewModel、空 Repository。

## 命名规则

- 根生成目录固定为 `Generated/HTMLToIOS`；项目已有生成代码规范时，可调整 `Generated` 上层位置，但保留 `HTMLToIOS` 所有权边界。
- 生成器默认拒绝不以 `Generated/HTMLToIOS` 结尾的输出路径。只有 Agent 已确认项目使用 Tuist、XcodeGen、同步文件夹或既有 feature 生成目录时，才允许显式使用 `--allow-nonstandard-output`，并在交付报告中记录实际映射。
- 禁止直接输出到源码根目录、target 根目录、`Sources/` 根目录或人工维护的 `Features/` 根目录。
- Swift 类型、Screen 目录和文件使用 `UpperCamelCase`；HTML screen ID 保持稳定的 `lower-kebab-case`，生成 Swift 名时做确定性转换。
- 页面文件名与主要类型使用 `native-naming-plan.json` 的同一前缀。新项目默认 `Sky`；已有项目按显式参数、上一轮计划、稳定模块前缀、target 名回退的顺序决定。通用生成运行时继续使用 `HTMLToIOSGenerated`，便于识别所有权。
- HTML 使用 `data-ios-module="home"` 声明业务模块；生成目录为 `Home/`。同模块的 `home`、`home-detail` 等页面进入同一模块目录。
- Screen 文件使用 `<Prefix><Name>Screen.swift` 或 `<Prefix><Name>ViewController.swift`；可复用控件使用 `<Prefix><Purpose>View.swift`、`<Prefix><Purpose>Cell.swift` 或 `<Prefix><Purpose>Control.swift`。
- 禁止 `View1.swift`、`NewView.swift`、`Common.swift`、`Utils.swift`、`Temp.swift` 等无职责名称。
- Asset 名使用 `html_<screen>_<role>`；同一资源复用同一 Asset 名，不按节点重复复制。
- 一个 Swift 文件原则上只放一个主要 public/internal 类型；紧密私有辅助类型可与所有者同文件。
- 生成前将计划类型与目标模块现有类型集合比较；发生同名冲突时停止并要求更换前缀或复用已有组件，不得自动追加随机数字。

## 归属规则

- `Application/`：App 接入入口和根容器，不放页面细节。
- `Core/Data` 与 `Core/Models`：Codable 契约和资源加载。
- `Core/Navigation`：App 级 NavigationStack、UINavigationController、TabView、UITabBarController、Router/Coordinator 和页面工厂。
- `Core/Runtime`：跨 screen 的通用渲染与状态运行时。
- `<Module>/Screens`：SwiftUI 完整页面及页面生命周期入口。
- `<Module>/Controllers`：UIKit 完整 ViewController；不放 Cell、Header 或局部控件。
- `<Module>/Views`：该模块的页面内容、Header、Cell、Control 和局部复用组件。
- `Shared/Components/`：至少被两个 screen 使用且语义稳定的组件。
- `Shared/Styles/`：设计令牌和样式适配，不放业务状态。
- `Resources/`：Payload、Asset Catalog、字体和本地化资源，不放 Swift。

不要把页面文件放进 `Core`，不要把业务组件放进 `Runtime`，不要把人工维护文件写入生成目录。

App 级 Navigation/Tab 放入 `Core/Navigation`。只服务于单个业务模块的 Coordinator 或自定义容器应放入 `<Module>/Navigation`，不得把 `Core` 变成无边界公共目录。

## 模块归组

1. 优先使用 screen 根上的 `data-ios-module`，并写入 UI IR `screen.moduleId`。
2. 未显式声明时，只有存在稳定 screen 前缀关系才自动归组，例如 `home` 与 `home-detail` 归入 `Home`。
3. 无公共前缀证据时，每个 screen 使用自己的完整 ID 作为模块，禁止凭页面标题或跳转来源强行归组。
4. 两个 module ID 转换后得到相同 Swift 目录名时停止生成并要求消歧。
5. `List`、`View` 等泛化名称应尽量改成 `ArticleList`、`ProductList` 等业务名称；无法修改 HTML 时仍必须保证生成类型有稳定前缀且不与系统类型同名。

## 增量与迁移

- `.html-to-ios-generation.json` 记录相对路径、哈希和所有权。
- 只覆盖清单确认由生成器拥有且当前哈希未变化的文件。
- 人工修改过的目标写入平行 `.conflicts` 目录，保留相同相对层级。
- 目录规则升级时，可以删除清单拥有且未修改的旧路径；修改过的旧路径必须保留并报告冲突。
- Xcode 接入只移除生成目录内、磁盘已不存在的失效引用；不得清理生成目录外的项目引用。
- 不允许同一个主要 Swift 类型同时存在于旧扁平路径和新分层路径。
- `Resources/Assets/HTMLToIOSGeneratedAssets.xcassets` 由生成器完整管理：每轮按当前 IR 重建，已删除或改名的 HTML 资源不得残留。旧扁平 Asset Catalog 相同则删除，不同则移入 `.conflicts/Legacy/`，并从 Xcode target 移除旧引用。

## 现有项目

若目标工程已有 `Features/<Feature>`、Tuist、XcodeGen 或同步文件夹规范，先将上述职责映射到项目已有层级。目录名称可以适配，但以下边界不能取消：应用入口、核心契约、通用运行时、screen 专属代码、共享组件和资源必须可区分；生成所有权必须与人工源码隔离。
