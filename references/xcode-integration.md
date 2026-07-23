# Xcode Integration

目标是让源码和资源加入正确 target，并通过命令行构建验证。不要只把文件放进目录后假设工程已经识别。

## 工程类型

### Swift Package

- 读取 `Package.swift` 中 target 的真实 path。
- 文件放进对应 `Sources/<Target>` 或自定义目录。
- 运行 `swift build` 或项目要求的测试命令。

### Xcode 16 文件系统同步组

- 在 `project.pbxproj` 检测 `PBXFileSystemSynchronizedRootGroup` 或 `fileSystemSynchronizedGroups`。
- 放入同步根覆盖的目录。
- 检查 target membership exclusion 和 build phase 异常。

### 传统 `.xcodeproj`

- 优先使用 Ruby `xcodeproj` 或成熟的 Python pbxproj 库。
- 明确选择 target；不要用“第一个 target”或“第一个有 source phase 的 target”猜测。
- 修改前保存可恢复副本，修改后再次解析工程并运行 `xcodebuild -list`。
- 不使用正则直接拼接 PBXFileReference、PBXBuildFile 或 UUID。
- 工具不可用或 target 不明确时，不修改 `project.pbxproj`，准确报告未关联状态。

## 资源接入

- 图片优先进入项目已有 `Assets.xcassets`。
- 字体文件必须加入 target，并按项目形式更新 Info.plist 或构建配置。
- 不重复创建项目已有的颜色、图片或字体资源。
- 资源名变更必须同步 UI IR asset mapping。

## 构建

先运行：

```bash
xcodebuild -list -project App.xcodeproj
```

再使用明确参数：

```bash
xcodebuild \
  -project App.xcodeproj \
  -scheme App \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 15 Pro' \
  -derivedDataPath /tmp/html-to-ios-derived-data \
  CODE_SIGNING_ALLOWED=NO \
  build
```

工作区项目使用 `-workspace`，不要同时传 `-project`。选择已安装模拟器；指定设备不可用时先列出 destinations，再选择等价逻辑尺寸。

## 构建错误处理

- 只修复由本次生成或接入造成的错误。
- 项目原有错误、证书、依赖下载和环境故障应单独归类。
- 不通过删除文件、关闭 warning-as-error 或修改全局 build setting 掩盖问题。
- 构建成功后记录 scheme、destination 和命令摘要。

