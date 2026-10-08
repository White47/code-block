# 结构展开与图阅读修复（2026-10-07）

用户已确认以下方案。此次保留此前未提交修改；不修改被分析源码、不执行源码、不上传项目、不自动提交 Git。

## 已复现问题

自写 MVP Classifier 在关系 hops=1/2/3/5 时均只有 Classifier、embedding、encoder、head 四个节点；不选择节点时也不变。Classifier.forward 的 FunctionFlow 在 hops=1/5 时均为七节点。原按钮操作参数正确，不等于实际组件展开。encoder 实例的内部结构属于独立 Encoder 定义，必须沿真实定义引用解析；邻域跳数不能替代结构深度。

## 产品合同

1. 项目结构、模型组件、函数流程、调用／数据关系分别说明主语义。普通 Python 没有模型时仍能浏览真实模块、类、函数的项目结构；不推断不存在的模型或业务框架。
2. 架构及项目结构的更多／更少层控制结构深度 1–5。默认保留概览和直接组件；增加层数必须展示真实可展开的下层，减少后移除超出深度的节点，不能仅更改数字。
3. 自定义组件通过 `target_definition_id` 查看定义内部，投影节点标明定义来源与实例上下文；底层 Semantic Graph / Code Graph 的事实不变。重复结构、共享实例、定义与运行实例仍区分。循环定义有界，第三方组件无源码内部时明确说明。
4. Symbol／DataFlow 才提供关系跳数及上游／下游。FunctionFlow 展示选定函数的静态流程，不显示未参与查询的层数控制。Execution 保留已有循环／分支的展开入口，不把关系跳数伪装成结构展开。
5. 操作过程中显示更新状态；完成后显示实际新增／收起节点数、已到底、预算截断或暂无可展开内容。无变化也有明确原因。没有分析信息时显示解释与可用入口，不给空画布冒充项目架构。
6. 节点、边及图例使用中英文可读标签；保留真实代码名、源码跳转和 Inspector。结构图从上到下、同级并列。图标题／说明提供当前层级与来源，不能把包含关系画成真实执行顺序。
7. 增层后新节点自动可见；同图选择及正常用户视角不反复适应。保留上一轮测量复用、即时相机、手动位置及标签／分屏／缓存修复。

## 实现边界

新增局部 View Builder 投影与项目结构视图，复用 Symbol Index、定义引用和既有图 DTO；不迁移技术栈、不新增图形依赖、不改 Parser 语义识别。新增投影身份必须确定性、作用域隔离、可验证，不能放宽任意 ID 的写接口校验。深度、节点和边预算均有界；分析缓存事实格式不变，持久化兼容须测试。

## 必须验收

开发前主代理已独立确认 RED：后端新套件 14 个明确断言失败、0 errors；前端新套件 7 FAIL / 2 PASS，包含 FunctionFlow / Execution 仍出现无效按钮、Architecture 无结构控制及无模型时缺少解释入口。此记录不是完成证明；生产修改后须验证真实节点增减及最终 PC 窗口。

- 真正比较 Classifier 深度 1/2/3/4 的节点集合：1 为直接组件，2 出现 Encoder.layers，3 出现 ModuleList 成员，4 出现 Block.projection；降低后集合缩小。不得为了更少层数抹去真实容器层级；源码锚点正确。
- 同一定义的两个实例不混淆；递归引用不无限展开；错误／未知引用与预算截断可解释。
- 普通 Python 无模型项目结构非空，模块／类／函数源自 Symbol Index，能选中、拖动、打开源码并重开缓存。
- 前端从实际 get_view 结果反馈增减数；无变化、错误、等待及切图旧响应分别验证。
- FunctionFlow 不再显示无效层数按钮；Symbol／DataFlow 的关系范围仍可用。
- 最终冻结 PC 包实际点击层数按钮，读取节点集合而非只看按钮数字；图结构、详情、搜索、源码、标签、语言、缓存及原生项目打开回归。
- 前后端 lint、typecheck、tests、build、独立代码复审；未通过真实 PC 门禁不宣布交付。

## 当前验证进度

本地后端全量 653 项，结果 OK（2 项跳过）；前端 144 项通过，Ruff、Windows / Linux mypy（59 个源码文件）、前端 lint / typecheck / build 通过。真实 Tk 测试已运行；部分销毁时的 Tk 主题回调输出警告，但没有新增测试失败。新增的投影身份、跨模型布局保留、详情岛选择、聚合节点校验和实时深层选择均有回归测试。独立 reviewer 已批准最终后端及前端修改。跳过项不代表视觉验收通过。

初次冻结包与仓库外 wheel 发现的源码标签返回时重置层级问题已修复；缓存标签顺序倒置时返回错误标签的问题也已修复。返回来源标签是临时 UI 状态，不改变持久化格式。最终重建后的冻结包和独立安装 wheel 均通过 9 项真实 WebView2 结构验收：Classifier 节点 4→5→6→7→6→5→4，实际新增／收起反馈、无须点击适应的结构布局、投影 Inspector 与真实源码、普通项目结构、FunctionFlow 控件及 Symbol 关系查询。报告分别为 `build/structure-delivery-frozen-native/report.json` 和 `build/structure-delivery-wheel-native/report.json`，状态均为 passed。冻结包另通过普通 Python 5 项回归，包括搜索、源码、标签／分屏、语言及缓存重开。

冻结包训练补充回归 5 项通过，报告 `build/structure-delivery-frozen-ml-native/report.json`：架构详情岛、真实层级变化、Epoch／Batch 展开及 Forward→Loss→Backward→Optimizer→Source、语义搜索／分屏、语言持久化。长图最小缩放时无法全部入屏，验收通过真实鼠标拖动画布后点击可命中节点；没有 force、JS 状态注入或写入 bridge 绕过。独立 reviewer 已批准该脚本调整。

项目选择器最终 7 项通过，报告 `build/structure-delivery-picker-native-retry/report.json`：等待超过 17 秒、两次重新分析、取消后继续分析、切换第二项目及缓存重开。首轮脚本在取消后的关闭观察中误读已隐藏控件，修复只读等待并经独立 reviewer 复核；原生写入前的 PID／控件验证没有放宽。

独立安装 wheel 普通 Python 5 项通过，报告 `build/structure-delivery-wheel-general-native-unique/report.json`，无跳过及错误。此前 9313 调试端口存在旧 WebView2 的 IPv4 监听和新窗口 IPv6 监听，导致连接旧页面、截图超时；更换唯一端口 9315 后直接通过，无应用代码修改。首轮失败报告保留，不作为成功证明。

本次真实 PC 验收合计：冻结包结构 9、普通 Python 5、训练 5、选择器 7；独立 wheel 结构 9、普通 Python 5。仅适用于上述 fixture／操作，不能泛称所有 Python 项目或所有运行时均已支持。隔离冻结包缓存中的两份 `structure-test-note` 注释在分析重建及重开后仍保留；没有触碰用户缓存。

最终 wheel SHA-256：`971e7856911113a84dea3e5aae5a42518d471c60af287f3a67f6d5120849c266`。前端构建、冻结包和独立安装 wheel 的 `index-DJCfJi8q.js` 均为 `077b6ee37c0f9133d3a6fa980522985397a0cf11c51949d32adf0f692ddbb0ec`。仅核对 exe 不足以证明 onedir 内的界面资源一致。

## 交付内容与边界

- 后端：新增 `architecture_structure.py`、`project_structure.py`、`structure_identity.py`；接入 Graph IR 视图类型、View Builder、布局、Controller、Presenter 和桌面 bridge 白名单。没有重写 Parser，也没有新增依赖。
- 前端：结构查询和反馈、Overview 入口、动态 Inspector、可读标签、测量后可见性、源码返回来源标签，以及相应回归测试。保留旧有项目打开、搜索、设置和缓存保护。
- 测试：新增结构套件、真实 PC 结构验收；调整旧训练测试 fixture，使其依赖真实类型证据而不是变量名猜测。
- 安装包：`dist/structure-exploration/CodeViz/CodeViz.exe`，必须保留整个 CodeViz 文件夹及 `_internal`。使用系统 WebView2；冻结包不要求安装 Python/PyTorch。没有提交或推送 Git，没有清理用户缓存。
- 分析仍是静态证据，不代表执行过项目；重复 ModuleList 模板不是运行时观察。结构深度最多 5，并受图预算限制。长执行流程可通过画布平移浏览，不承诺所有图在最小缩放下仍能一屏显示。
