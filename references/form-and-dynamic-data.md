# 表单与动态数据

## 目标

输入控件和动态内容既要复刻 HTML 首帧，也要具备合理的原生运行时结构。首帧样例、用户编辑状态和外部业务数据必须分开所有权，不能为了截图把内容永久写死。

## 输入控件判定

- 单行可编辑、搜索、数字、密码输入映射 `TextField/SecureField` 或 `UITextField`。
- 多行可编辑内容映射 `TextEditor` 或 `UITextView`。
- 普通展示文字优先 `Text/UILabel`；只有来源明确要求选择、内部滚动或富文本编辑器式展示时才使用只读 TextView，并关闭编辑。
- `readonly` 输入仍保留输入控件语义；`disabled` 同时关闭编辑和交互。
- 必须保留 placeholder、initial value、secure、maxlength、keyboard/content type、return key、autofocus、自动大写、自动纠错与验证规则标识。

输入值进入 screen store 或项目 ViewModel。生成代码必须在每次编辑后更新状态并执行长度限制；不得只在首帧设置一次文字。Return 键仅在存在已确认 action 时触发业务动作。

## 键盘与滚动

- SwiftUI 由系统 keyboard safe area 管理；UIKit 由页面主滚动容器或项目键盘协调器管理。
- 页面滚动容器铺满父容器，禁止从 frame 高度中减去 keyboard 或 safe area。
- 主纵向滚动容器使用 interactive dismiss；多行 TextView 仅在 HTML 证明确有内部滚动时拥有自己的纵向滚动。
- 固定底部操作栏只接受一次系统键盘避让。禁止同时修改 safe-area padding、scroll inset 和 frame，造成重复抬升。
- 输入聚焦后的关键状态需要验证首个字段、末个字段、键盘弹出、交互式收起和底部按钮可达性。

## 动态数据分类

1. `static-fixture`：HTML 中的固定内容。可抽取复用 Cell/View，但不创建 ViewModel。
2. `local-prototype-state`：JavaScript 行为会在本地改变筛选、展开、步骤或列表内容。生成本地状态模型。
3. `external`：HTML 明示 `data-ios-data-source`，或已有工程存在可确认的数据模型/Provider。首帧是样例数据，必须接入 ViewModel 或项目数据层。

重复 DOM 本身不是接口证据。不得根据几条列表内容猜测网络请求、分页协议、字段名或缓存策略。

## 状态模型

外部数据区域至少区分已声明的 `loading`、`content`、`empty`、`error` 角色；分页只使用 `none/page/cursor/infinite`。`data-ios-item-id` 用于 Diffable Data Source、`ForEach` 或项目列表组件的稳定身份。

生成器可以创建协议、ViewModel 接入口和样例 fixture，但不得伪造 endpoint。已有工程优先复用其状态管理、依赖注入、错误模型、分页器和刷新控件。

## 视觉验收

- 初始 fixture 必须与 HTML 首帧一致。
- 声明为 required 的 loading/content/empty/error 分别截图。
- 长列表验证复用、末项可达性、分页触发边界与滚动轴隔离。
- 输入状态验证 placeholder、空值、最大长度、多行增长/内部滚动、键盘类型和底部区域避让。
