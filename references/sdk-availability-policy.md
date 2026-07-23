# SDK Availability Policy

系统组件选择必须同时满足三个版本事实：Apple 当前文档、开发机已安装 SDK、目标工程最低部署版本。三者不能混为“最新版”。

## 生成前检查

1. 运行 `xcodebuild -version`。
2. 运行 `xcrun --sdk iphoneos --show-sdk-version` 和 `--show-sdk-path`。
3. 读取工程的 `IPHONEOS_DEPLOYMENT_TARGET`。
4. 运行 `scripts/inspect_ios_sdk.py --minimum-ios <version>` 检查计划使用的 UIKit/SwiftUI 符号。
5. 对关键新 API 再核对 Apple 官方文档和 SDK interface/header 的 availability。

本技能不得把某个固定 Xcode/iOS 版本永久写成“最新”。日期变化、Xcode 更新或目标工程变化时重新检查。

## 选择规则

- SDK 中不存在：禁止生成，改用已安装版本支持的方案。
- SDK 中存在，最低部署版本早于 introduced version：使用 `if #available`、`@available` 或稳定降级实现。
- API 已 deprecated：新代码优先替代 API；只有维护既有架构时保留，并记录迁移风险。
- API unavailable 于 iOS：不得因其他 Apple 平台存在同名 API 而生成。
- SwiftUI 修饰器与 UIKit 类型分别检查，不能用 UIKit 的 introduced version 推断 SwiftUI 包装层。
- 第三方/其他系统框架还需检查 import、link、entitlement 和隐私清单。

## IR 记录

```json
{
  "nativeMapping": {
    "swiftUI": "ContentUnavailableView",
    "uiKit": "UIContentUnavailableView",
    "availability": {
      "verifiedSDK": "iphoneos-26.2",
      "minimumIOS": "16.0",
      "swiftUI": {"status": "requires-fallback", "fallback": "custom empty-state view"},
      "uiKit": {"status": "requires-fallback", "fallback": "custom empty-state UIView"}
    }
  }
}
```

脚本无法可靠解析 introduced version 时使用 `status: review-required`，不得编造版本号。

## 降级要求

降级实现必须保持：

- 相同用户任务和主要交互；
- 可接受的视觉结构；
- 相同导航/关闭所有权；
- 可访问性和状态语义；
- 编译期不会引用旧 deployment target 无法解析的符号。

降级不要求完全复刻最新系统材质。视觉高保真与版本兼容冲突时，在交付报告中说明取舍，并以可编译、可操作为底线。

## 维护策略

- 静态组件目录按语义分类，避免跟随每个 SDK 辅助类频繁膨胀。
- SDK 脚本负责发现新增、移除、deprecated 和 unavailable 符号。
- 新增自动映射时必须同时加入 availability 与 fallback。
- 每次 Xcode 大版本升级后运行目录审计和端到端 fixtures。
