# HTML Runtime Extraction

高保真转换必须基于浏览器真实渲染结果。源码分析用于补充语义和交互，不代替 computed style 与实际几何信息。

`render-tree-1.2` 额外包含 `textMetrics`、`assetDetails` 和 `document.loadedFonts`。文字 Range 行框用于 `text-calibration.md`，内联 SVG markup 和资源 URL 用于 `resource-conversion.md`。

## 运行方式

优先使用工作区捆绑的 Node 与 Playwright。先调用工作区依赖加载能力取得实际路径，再执行：

```bash
NODE_PATH="<node_modules>" "<node>" scripts/extract_render_tree.cjs \
  --html /absolute/path/index.html \
  --width 393 --height 852 \
  --out /tmp/render-tree.json \
  --screenshot /tmp/html-baseline.png
```

对于 URL 使用 `--url`。对于大展示板中的单个画板，可第二次使用 `--selector` 精确截图和提取。
单文档虚拟页面使用路由图中的 activation selector，例如 `--activate-selector '[data-page="page2"]'`，再用目标容器或页面 selector 提取。

## 稳定渲染条件

提取前必须：

1. 设置固定 viewport、方向、浅色/深色和 device scale factor。
2. 等待 `DOMContentLoaded`。
3. 尝试等待 `networkidle`，超时后记录 warning 而不是无限等待。
4. 等待 `document.fonts.ready`。
5. 先执行 motion pass，读取 CSS transition、CSS animation、Web Animations API 和 keyframes。
6. 再禁用 CSS animation、transition、caret 和自动播放，形成稳定 static pass。
7. 等待至少两个 animation frame，使同步布局完成。
8. 对会变化的时间、随机数、轮播和远程数据固定状态，或在报告中标记不稳定区域。

## 安全边界

- 本地 HTML 优先通过临时 localhost 静态服务器加载，避免 `file://` 模块和资源限制。环境禁止本地端口时可以降级到 `file://`，但文件访问必须限制在 HTML 所在目录，并记录警告。
- 默认阻止页面来源之外的远程请求，防止追踪请求与不稳定依赖。
- 只有用户提供的页面确实需要远程资源时才启用 `--allow-remote`。
- 不向页面注入浏览器账号、Cookie、密钥或用户主目录访问能力。
- 对不可信 HTML 使用隔离浏览器上下文，并在完成后关闭浏览器和临时服务器。

## 必须提取的信息

- DOM 父子关系和稳定 node ID
- tag、id、class、role、aria label 和表单属性
- 表单控件当前运行时属性：value、checked、selected、disabled、readonly、required、multiple、open 和 focus
- `getBoundingClientRect()`
- 关键 computed styles
- `::before` 与 `::after`，并将可见伪元素转成带 owner 和估算边界的 synthetic node
- scrollWidth、scrollHeight、clientWidth、clientHeight
- 可见性、裁剪、position、z-index 和 transform
- 直接文本内容，避免父节点重复吞并子节点文字
- `src`、`currentSrc`、SVG、背景图片和字体引用
- href、target、form action、inline onclick
- animation/transition 的属性、时长、延迟、曲线、循环和关键帧
- 页面 viewport、文档尺寸和 meta viewport
- 完整文字行框、行数、字体加载状态和裁剪信息
- 内联 SVG markup、图片 URL 和 CSS background 资源详情

## 多页面状态

先运行 `discover_html_routes.cjs`。每个 URL、Hash/History route 或单文档虚拟页面状态分别提取，并保留 activation selector 与路由关系。`.page[id]`、tabpanel 和 `data-page` 控制的页面不能因为共用一个 HTML 文件而混成一棵树。

## 手机画板识别

候选画板应采用评分、结构过滤和父子归类，而非单条件判断。可参考：

- 宽度约 280–500 CSS px
- 高宽比约 1.5–2.4
- class/id 含 `phone`、`iphone`、`device`、`screen`、`artboard`、`mockup`
- 与页面背景存在明显边界、圆角或阴影
- 包含状态栏、底部指示器或完整页面结构
- 外层页面明显比候选宽

候选输出区分：

- `device-frame`：展示用手机外壳或画板框。
- `app-root`：真正的 App 内容根节点。
- `containedByRuntimeId`：候选被哪个外层候选包含。
- `recommendedRootRuntimeId`：转换时推荐使用的内容根节点。
- `isPrimary`：是否为一个独立画板入口。

普通 `main`、卡片等只有手机宽度但没有手机命名或外框特征的节点不得进入候选列表。外层 `device-frame` 与内部同尺寸 `app-root` 应归为同一组，不重复当成两张页面。

单个候选仍可能位于大展示板中，不能因为只有一个候选就默认使用整个 `body`。候选只用于辅助选择，最终根节点应结合截图和页面结构判断。

## 源码补充分析

运行时提取无法完整获得通过 `addEventListener` 注册的业务语义。按 `dynamic-interaction-discovery.md` 运行 AST + runtime probe，继续分析源码中的：

- `<a href>`、`target`、`form action`
- `window.location`、`location.assign/replace`
- `window.open`、History API
- `alert`、`confirm`、`prompt`
- 显隐 class 切换和状态变量
- SPA Router 配置

不得只用正则推断 JavaScript 调用链。交互、状态和自动跳转写入独立 `interaction-state-graph.json`，不要塞回 render tree；两者通过 screen ID 和 selector/node ID 对齐。

不要迁移接口实现、数据抓取、埋点、SEO 和浏览器兼容代码，除非用户明确要求。

## 失败降级

- Playwright 不可用：先报告依赖问题，再使用浏览器控制能力获取截图和 DOM 信息；不要假装已经取得 computed style 全量数据。
- 页面无法离线运行：列出失败资源；用户允许后再加载远程资源。
- 动态页面无法稳定：固定一个可复现状态，并把其他状态列为未验证。
- WebGL/Canvas：仅能把纯装饰输出为合法资源；交互式 Canvas 不在默认范围。

默认启用 motion pass；只有诊断静态问题时才使用 `--skip-motion`。closed Shadow DOM、跨域 iframe 和运行时不可访问的绘制内容必须产生边界报告。
