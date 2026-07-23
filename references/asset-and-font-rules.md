# Asset and Font Rules

高保真页面应尽可能使用原型提供的真实资源。资源处理同时考虑视觉、工程和许可。

批量盘点、去重和格式准备按 `resource-conversion.md` 运行 `scripts/prepare_ios_assets.py`。工具输出是接入暂存目录，不代替 target membership、许可核查和运行时视觉验收。

## 图片

- 本地 PNG、JPEG、HEIC、WebP 等先检查项目支持和目标系统版本，再决定是否转换。
- 保持像素尺寸、scale、透明通道、render mode、裁剪和 content mode。
- 优先复用 Asset Catalog 中内容相同的资源，避免重复文件。
- 文件名转换为稳定、合法、符合项目约定的 iOS asset name，并在 UI IR 中记录映射。
- 远程图片不能取得时，用明确占位资源保留布局，不编造图片内容。

## SVG 与图标

- 简单矢量可以作为单尺度 PDF vector 或项目支持的 SVG 资源。
- 需要动态填色的简单 path 可以转换为 Shape/CAShapeLayer。
- 复杂 SVG 优先保持为矢量资源，不手工近似绘制。
- SF Symbols 只有在轮廓、粗细、填充方式和语义都足够接近时才能替换。
- 自定义品牌图标、Logo 和产品图标不得擅自替换。

## 背景图和 CSS 图像

- 解析 `background-image`、多层背景、gradient、size、position 和 repeat。
- 纯渐变使用原生 gradient。
- 图片与渐变叠加时保留层次顺序。
- `cover`、`contain`、center crop 和 tile 映射到对应原生 content mode/绘制策略。

## 字体

1. 记录浏览器实际计算出的 font family、weight、style、size 和 line-height。
2. 检查字体是否为 iOS 系统字体或项目已有字体。
3. 原型包含本地字体且许可允许时，将字体加入 target 并完成注册。
4. 字体缺失或许可不明时回退到最接近系统字体，并在差异报告中标记。
5. 可变字体需要检查 iOS 支持和 weight axis；不能把所有 weight 映射到同一个文件。

浏览器与 iOS 的字体 rasterization 不完全一致。验收重点包括字号、字重、行高、baseline、换行位置和文本容器尺寸，像素比较应允许少量抗锯齿差异。

## 许可与网络

- 用户提供的本地资源标记为 `local-provided`。
- 不自动抓取来源不明或可能受限的远程资源。
- 需要下载远程资源时先确认来源和用途，并记录 URL。
- 不把临时缓存路径直接写入项目。

## 占位规则

只有以下情况使用占位：

- 源文件不存在；
- 网络资源不可访问；
- 格式无法安全转换；
- 许可不明确；
- 用户明确要求忽略资源。

占位必须保持原始尺寸、比例、圆角、裁剪和背景层次，并在交付报告中逐项列出。
