# Resource Conversion

资源处理分为盘点、去重、转换、接入和视觉验收。转换工具只处理用户提供或本地可访问的资源，不自动下载许可不明的远程内容。

## 执行

先使用 `render-tree-1.2` 提取 `assetDetails`，再运行：

```bash
python3 scripts/prepare_ios_assets.py render-tree.json \
  --source-root <html-project-root> \
  --out-dir <asset-staging>
```

输出 `GeneratedAssets.xcassets`、可接入字体目录和 `asset-manifest.json`。

## 转换规则

- 内联 SVG：保存为矢量 imageset，保留 path、viewBox、gradient 和透明度。
- PNG/JPEG：保留原编码，按内容哈希去重。
- WebP/HEIC/HEIF/BMP/TIFF：转换为 PNG 并保留 alpha。
- data URI：解码后按实际 MIME 处理。
- CSS gradient：标记为原生 gradient，不栅格化。
- PDF/SVG：设置 vector preservation。
- GIF/动画 WebP/Lottie/视频：不静默压成静态图；进入动画或媒体专项实现。
- 字体：复制到暂存目录并标记 target membership/Info.plist 要求；WOFF/WOFF2 仍需取得可用于 iOS 的 TTF/OTF。

远程 URL、找不到的本地文件和不支持格式进入 `deferred`。只有用户明确允许、来源可信且许可可接受时才下载远程资源。

## 接入检查

1. 与现有 Asset Catalog 做内容哈希和语义名称双重去重。
2. 资产名遵循项目规则，不用临时路径或 URL 当 Swift 标识符。
3. 核对 1x/2x/3x、rendering intent、preserve vector、template/original 和色域。
4. 核对 `aspectFit`/`aspectFill`、裁剪、alignment 和九宫格拉伸。
5. 内联 SVG 若依赖 CSS 变量或外部 filter，必须先展开依赖或改用 Shape/CALayer。
6. 生成的暂存目录不是自动 target membership 证明；接入后必须构建并检查运行时资源。

`generate_ios_from_ir.py` 对内联 SVG、PNG/JPEG、data URI 和可由 Pillow 解码的 WebP/HEIC/HEIF/GIF/BMP/TIFF 直接生成独立 Asset Catalog。动画 GIF/动画 WebP 不得使用这条静态首帧路径，必须在 IR 中保留动画语义并进入媒体实现。转换库缺失或解码失败时跳过写入并将资源保留为 deferred，禁止生成损坏 imageset。
