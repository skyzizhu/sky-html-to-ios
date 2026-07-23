# Edge Case Policy

## 封装组件

React、Vue、Web Component、CSS Module 和设计系统组件都以运行时输出为准。读取最终 DOM、computed style、accessibility role、状态和交互，不根据组件名猜原生控件。

- open Shadow DOM：递归提取并保留 host 关系。
- closed Shadow DOM：使用 accessibility tree、外部交互和截图，降低置信度。
- framework event delegation：记录可观察结果；无法定位 handler 时不编造业务回调。
- pseudo-element：生成 synthetic IR node；边界为估算值时必须进入视觉纠偏。

## CSS 边界

- Grid/subgrid/container query：保留响应式规则，不只保存当前 frame。
- sticky/fixed：区分滚动容器、页面根层和 safe area。
- logical properties/RTL：转换为 leading/trailing 语义。
- viewport unit/env safe area：使用目标设备和原生 safe area 重新计算。
- filter/blend/backdrop/mask：评估 Core Image、Core Animation 或局部位图资源。
- multiple background/shadow：拆为明确绘制层，避免把整个组件截图化。

## 不透明内容

- same-origin iframe：可作为独立页面递归分析。
- cross-origin iframe：默认 unsupported，除非业务明确要求合法 Web 容器。
- Canvas：静态装饰可转位图/原生绘制；交互 Canvas 单独评估。
- WebGL/游戏/复杂编辑器：不自动承诺原生等价转换。
- SVG：简单图标转矢量资源或 Shape；复杂 filter/animation 保留资源或局部 fallback。

## 数据与稳定性

冻结时间、随机数、异步加载、轮播、光标和动态计数。提供 fixture 数据或网络录制，确保 HTML 和 iOS 状态一致。登录、支付、私有接口和第三方 SDK 不从原型自动补全。

## 文本与资源

检查字体文件、weight、variable font axis、语言、换行、line clamp、emoji 和富文本。资源无法访问或许可不明时使用明确占位并报告，不从互联网随意替换。

## 输入、键盘与手势

验证焦点顺序、键盘类型、secure entry、提交、校验、键盘避让和关闭。检查 nested scroll、interactive pop、drag、swipe、long press 和 hit-testing 冲突。

## 设备、系统能力与性能

最低验证目标设备；共享组件还要抽查一个窄屏和一个宽屏。保留 Dynamic Type、VoiceOver、RTL、Dark Mode、Reduce Motion、Increase Contrast 和点击区域。

相机、相册、定位、通知、联系人、文件、分享、邮件和支付需要权限、entitlement、隐私文案与真机能力检查。HTML 控件只代表入口，不证明系统配置已经存在。

长列表必须复用/惰性加载；大图正确缩放；连续动画支持暂停；复杂 blur、shadow 和离屏渲染需要性能抽查。不能为了像素相似产生不可接受的内存或帧率问题。

## 结果分类

每个边界项必须落入：`native`、`native-fallback`、`project-component`、`placeholder` 或 `unsupported`。不得静默遗漏，也不得以不透明截图隐藏未实现内容。
