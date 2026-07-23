# Motion and Effects

动效转换必须同时保留触发条件、状态变化、时间曲线和视觉属性。不要只复制 duration，也不要把所有动画统一成 easeInOut。

## 双通道提取

浏览器提取按两阶段执行：

1. Motion pass：不关闭动画，读取 computed transition、CSS animation、Web Animations API、keyframes 和 timing。
2. Static pass：关闭 animation、transition、caret 和 autoplay，等待布局稳定后提取节点树与基准截图。

Motion pass 记录 source selector、属性、duration、delay、timing function、iteration、direction、fill mode 和关键帧。Static pass 是布局基准，两者通过 selector/runtime node ID 合并到 UI IR。

- 生成阶段必须把可支持的 transform/opacity keyframes 绑定到来源节点，至少保留 duration、delay、iteration、direction、rotation、scale 和 opacity 采样；不能只在 IR 中记录后丢弃。
- 正常 App 运行时使用 SwiftUI Timeline/Animation 或 Core Animation 执行动画；视觉验收时通过 `-HTMLToIOSMotionProgress 0...1` 固定采样进度，保证 HTML 与 iOS 对比的是同一关键帧。

## 动效分类

| 类型 | 例子 | 原生策略 |
|---|---|---|
| 状态转场 | 展开、选中、显示隐藏 | SwiftUI `withAnimation` / `UIViewPropertyAnimator` |
| 属性动画 | opacity、transform、color | SwiftUI Animation / Core Animation |
| 关键帧 | 多阶段缩放、路径变化 | KeyframeAnimator（版本允许）/ `CAKeyframeAnimation` |
| 弹簧 | 回弹、拖拽归位 | SwiftUI spring / `UISpringTimingParameters` |
| 页面转场 | push、sheet、custom present | 系统导航转场或 controller transition |
| 手势驱动 | swipe、drag、interactive dismiss | Gesture / recognizer + interactive animator |
| 连续装饰 | pulse、loading、光效 | 可暂停、低功耗的循环动画 |
| 复杂特效 | filter、mask、粒子、shader | Core Image、Core Animation、Metal 或明确降级 |

## 属性映射

- `opacity`、translate、scale、rotate：优先原生直接动画。
- width/height/top/left：优先改为约束或布局状态动画，避免逐帧硬改 frame。
- color/background/border：使用可插值颜色和 shape 状态。
- shadow/blur/filter：CSS 与 iOS 参数不同，先语义映射，再截图校正。
- clip-path/mask/path morph：简单形状使用 Shape/CAShapeLayer；复杂 morph 需要关键帧路径兼容性检查。
- perspective/3D transform：使用 `CATransform3D` 或 SwiftUI 3D transform，并验证锚点。

## 触发与状态

动画必须绑定可解释的原生事件：tap、change、appear、scroll、drag、navigation 或 state change。仅在 CSS 中发现 transition、但无法找到触发状态时，IR 标记 trigger `unknown`，代码生成不得编造业务触发器。

hover 在 iPhone 上没有直接等价。仅装饰 hover 可以忽略；承载信息或操作时转换为 tap、long press、context menu，并记录设计变更。

## 版本与降级

使用 KeyframeAnimator、phase animator、visual effect 等新 API 前执行 SDK availability 检查。最低版本不足时使用 Core Animation、UIViewPropertyAnimator 或较简单但语义一致的状态动画。

不因原型包含 Lottie、GSAP 或其他 Web 动画库就自动向 iOS 工程增加同名依赖。优先从最终 motion 数据重建；只有项目已有依赖或用户明确同意时才复用动画资源。

## 验证

- 静态状态：动画开始和结束截图。
- 关键帧：默认采样 0%、50%、100%；复杂动画增加关键转折点。
- 手势动画：验证起点、交互中间态、完成和取消。
- 循环动画：验证一个完整周期，并检查离屏/后台暂停。
- 开启 Reduce Motion 时提供系统允许的简化效果。

视觉 Agent 不能仅凭单张截图判断动画正确，应结合 motion IR、采样帧和实际触发路径。
