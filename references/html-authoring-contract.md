# HTML 作者契约

该契约用于减少 HTML 高保真原型到 iOS 原生页面之间的语义猜测。它是渐进增强规则，不要求普通 HTML 为转换而重写。

## 目录

1. 合规等级
2. 优先级
3. 最小推荐结构
4. 页面与系统区域
5. 控件与稳定标识
6. 路由与 Presentation
7. 状态与动画
8. CSS、资源与确定性
9. 校验

## 合规等级

- `L0-inferred`：没有专用标注。Skill 依赖语义 HTML、ARIA、运行时行为、DOM 和计算样式推断；可转换，但交互所有权和页面边界置信度较低。
- `L1-structured`：至少标注应用根和 screen；关键交互具有稳定 ID、action 与 target。适合日常多页原型。
- `L2-deterministic`：进一步标注原生控件、presentation、状态、动画和视觉验收态，且关键交互标注覆盖率至少 80%。作为高保真交付目标。

无需为了达到 L2 给每个 `div` 添加属性。只标注决定原生结构、行为和验收结果的节点。

## 优先级

解析优先级为：有效 `data-ios-*` 契约 > 语义 HTML/ARIA > 实际运行时行为 > DOM 结构 > class 名与 CSS 外观。

显式标注与实际行为冲突时不得静默选择。校验器或交互发现阶段应报错或产生待确认项。例如标注为 `switch` 的元素必须具备二元状态，`push` 必须存在目标 screen。

## 最小推荐结构

```html
<main data-ios-app-root data-ios-system-chrome="native">
  <section data-ios-screen="home" data-ios-module="home" data-ios-screen-initial data-ios-screen-title="首页">
    <button data-ios-node-id="home.open-detail"
            data-ios-component="button"
            data-ios-action="push"
            data-ios-target="home-detail">查看详情</button>
  </section>

  <section data-ios-screen="home-detail" data-ios-module="home" data-ios-screen-title="详情" hidden>
    <button data-ios-action="pop">返回</button>
  </section>
</main>
```

展示板、设备外壳和说明文字使用 `data-ios-shell`；不参与原生转换的内容使用 `data-ios-ignore`。不要把手机外壳的圆角、阴影和刘海当作 App UI。

## 页面与系统区域

- `data-ios-app-root`：一个 HTML 文档最多一个应用根。
- `data-ios-screen="id"`：稳定 screen ID；同一文档唯一。
- `data-ios-module="id"`：业务模块 ID，使用 `lower-kebab-case`；相关页面共享同一模块并生成到同一原生目录。
- `data-ios-screen-title="..."`：原生页面标题。
- `data-ios-screen-initial`：入口页面；最多一个。
- `data-ios-system-chrome="native|custom|none"`：系统导航栏、状态栏和底部区域的所有权。
- `data-ios-safe-area="top,bottom|all|none|background"`：内容与背景如何处理 safe area。
- `data-ios-owner="screen|navigation|sheet|overlay|child-controller"`：不明显区域的生命周期所有者。
- `data-ios-scroll-root`：页面主要滚动容器。
- `data-ios-navigation-style="native|custom|hidden|immersive"`：顶部导航所有权。
- `data-ios-title-mode="inline|large"`、`data-ios-scroll-edge="automatic|opaque|transparent"`。
- `data-ios-back-button="system|custom|hidden"`；自定义返回仍须明确 `pop` 或 `dismiss`。

固定顶部栏、底部操作栏、Tab Bar、悬浮操作和遮罩必须位于对应 screen 内，或通过 owner 明确归属。不要只用 `position: fixed` 和模糊 class 名表达其语义。

`data-ios-module` 表达业务归属，不表达视觉嵌套。不要因为页面从 Home 跳转，就把商品、购物车等独立业务全部标成 `home`；应分别使用 `product`、`cart`。未标注模块时，转换器仅在 screen ID 存在稳定前缀关系时自动归组。

## 控件与稳定标识

优先使用 `button`、`input`、`select`、`textarea`、`details`、`dialog` 及正确 ARIA。自定义组件或 `div` 控件再用：

- `data-ios-node-id="screen.role.name"`：跨状态稳定的 accessibility/test ID。
- `data-ios-component="..."`：原生语义提示，例如 `button`、`text-field`、`text-editor`、`switch`、`checkbox`、`segmented-control`、`picker`、`date-picker`、`slider`、`stepper`、`table`、`collection`、`navigation-bar`、`tab-bar`、`sheet`、`alert`。
- `data-ios-project-component="Module.ComponentName"`：已确认应复用的现有 SwiftUI/UIKit 项目组件；只有组件发现报告中存在该类型时才直接采用。

该值表示语义，不绑定具体 Swift 类。系统没有直接对应控件时，生成器按项目组件 > 原生组合 View/UIControl > 自定义 View > 自定义 ViewController 的顺序降级。

## 路由与 Presentation

- `data-ios-action`：`push`、`present-sheet`、`present-fullscreen`、`present-popover`、`show-alert`、`pop`、`pop-to-root`、`dismiss`、`replace-root`、`select-tab`、`open-url`、`toggle-state`、`update-state`、`add-child`、`remove-child`。
- `data-ios-target`：目标 screen、状态、节点 ID 或 URL。需要目标的 action 不得省略。
- `data-ios-presentation-style="automatic|page-sheet|form-sheet|full-screen|popover"`。
- `data-ios-detents="medium,large"`：sheet detents。
- `data-ios-backdrop-dismiss="true|false"` 与 `data-ios-interactive-dismiss="true|false"`。
- `data-ios-container="navigation|tab|split|page|child-controller"`。
- 主 Tab 子项使用 `data-ios-tab-id`、`data-ios-tab-title`、`data-ios-icon`、`data-ios-selected-icon`、`data-ios-badge` 和 `data-ios-action="select-tab"`。
- Tab 容器可声明 `data-ios-reselect="keep|pop-to-root|scroll-to-top"` 与 `data-ios-tab-visibility="always|hide-on-push|automatic"`。

原型展示用的页面切换器应放在 `data-ios-shell` 中，不要误标为 App 内导航。

## 状态与动画

- `data-ios-state="id"`、`data-ios-state-kind="boolean|enum|value|loading|error|empty"`。
- `data-ios-visible-when="state=value"`：状态驱动的可见性。
- `data-ios-visual-state="id"`：可截图验收的稳定场景。
- `data-ios-required-state`：该场景必须进入 HTML 与 iOS 双端截图矩阵。
- `data-ios-animation="fade|slide-up|slide-leading|slide-trailing|scale|spring|matched|progress|custom|none"`。
- `data-ios-duration-ms`、`data-ios-delay-ms`、`data-ios-easing`、`data-ios-repeat`、`data-ios-reduced-motion`。

优先声明动画意图而不是强迫 iOS 逐帧复制 CSS。`custom` 动画必须保留关键帧采样和视觉验收；系统 Reduce Motion 开启时必须提供静态或弱化路径。

## CSS、资源与确定性

- HTML 可以使用 Flex、Grid、绝对定位、伪元素、变量和媒体查询；最终尺寸仍以浏览器计算样式与实际矩形为准。
- 页面根必须能在目标 viewport 独立渲染。展示板尺寸不能代替移动页面 viewport。
- 响应式间距表达为容器约束或 design token，不要求 HTML CSS px 永久等于所有设备上的固定 pt。
- 使用本地或可稳定访问的图片、SVG 和字体；所有图像提供 `alt` 或 accessibility label。
- 验收内容必须确定：禁用随机数据、当前时间漂移、未固定网络内容和不可控动画。可通过测试模式或 fixture 固定。
- Canvas/WebGL、跨域 iframe 和封闭第三方组件需要单独降级策略，不能承诺自动还原为完整原生控件树。

## 校验

总控会自动运行：

```bash
python3 scripts/validate_html_authoring_contract.py prototype.html \
  --out html-authoring-contract-report.json
```

重复 screen/node ID、非法枚举、缺失或未知 action target 属于错误并停止转换。缺少 app root 或 screen 标注属于警告，按 L0 继续运行时发现。报告中的等级代表语义确定性，不替代最终编译、模拟器截图和视觉差异验收。
