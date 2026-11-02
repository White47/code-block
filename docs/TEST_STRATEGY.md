# 测试策略

## Agent 开工前目标与测试审计（2026-10-11）

产品目标仍是源码可追溯的 Python PC 程序理解；本轮扩展为只读代码理解 Agent、可开关侧栏、详细功能路径与可信引用，不做自动编程或运行用户代码。见 [正式设计](superpowers/specs/2026-10-11-code-understanding-agent-design.md)。

开工前基线 867 项 Python 与 415 项前端测试按责任完整归类，实施后的增量、实测、skip原因、影响范围选择和冗余判定见 [测试资产审计](TEST_SUITE_AUDIT.md)。不删除、移动旧测试或修改CI；首先精简重复执行频率。

阶段内小步运行修改模块与直接上下游消费者，阶段结束仍执行完整适用门禁；CI保留全量和平台矩阵；交付验证最终PC包。无关文件管理/真实仓库扫描/完整打包不必每次小步重复。同fixture、相似名字不等于重复；安全矩阵、不同竞态和原生验收保留。

## 分层

- Model：SourceAnchor、Stable ID、Confidence、序列化。
- Parser / Phase 2 Index：Python fixture、编码、大小限制、不执行源码、坏文件隔离、忽略目录、词法 scope、稳定 ID、raw reference 与导入别名重绑定。
- Resolver / Phase 3：绝对与相对 import、`src` 布局、包重导出、真实跨模块循环与纯别名循环、scope shadowing、`global` / `nonlocal`、重复定义及重复函数体、静态成员、第三方 ExternalSymbol、动态属性、缺失本地成员、星号导入、未使用导入、unresolved / possible targets；不得导入或执行被分析项目。
- Graph / Phase 4：Full Graph 的节点与锚点、同一行独立 CallSite、确定 / 模糊 / 外部 / 动态及继承派发调用、分支步骤到调用位置的归属、nested if / loop / with / try / continue / return、定义时调用、递归 / 互调环及长链、关键数据与 `self` 属性读写、隐藏 tmp 来源传递与覆盖、导入别名不误标、单文件阶段失败隔离；Visible Graph 查询留给 Phase 7。
- Model / Phase 5：torch 导入别名、项目内同名 torch 包与局部遮蔽、自定义 Module 继承、常见层、Sequential 顺序、ModuleList 同构 / 共享成员 / 零次 / 动态次数、ModuleDict 部分动态键、共享别名与链式赋值、条件构造、直接 / 索引 / 循环 forward CallSite 关联、未知工厂候选、稳定 ID、坏文件与规则异常隔离、不执行源码；训练规则留 Phase 6。
- Training / Semantic / Phase 6：显式导入与局部遮蔽、接收者 Exact / Probable、train/eval 与 no_grad 区分、epoch / batch 循环归属、多入口共享模型定义、条件重绑定、checkpoint 候选、Attention-like 正反例、模型 Definition / Instance 分离、图端点 / 子节点校验、来源锚点、Probable 梯度静态关系、坏文件隔离。
- Visible Graph / Phase 7：Architecture / Execution 多根与指定根、默认折叠和展开、选中组件 1-hop、单节点预算保留选中项、节点与边预算、执行主控制边优先、关键数据与真实代码级 DATA_FLOW 投影、可追溯溢出聚合、重复结构与共享实例区分、关系白名单、LOOP_BACK 布局、图例、手动位置覆盖及 JSON 数据往返；不得修改分析事实或执行项目代码。
- UI / Phase 8：无显示器测试 Presenter 事实映射、Controller 的显式 Analyze 与源码边界（含检查与打开之间的路径替换）、GraphScene / 边样式、Tk 模块惰性导入；有 Tk 显示时测试 Overview 模型入口、独立 Problems、源文件 / Split 控件释放、跨图选中与 Inspector、再次分析旧结果隐藏、Back / 面包屑、聚合成员重新定位、空图说明、图节点→Inspector→侧边源码定位。
- Search / Navigation / Phase 9：无显示器测试同名定义按 scope 消歧、中文 / 英文语义词、有限结果和空结果、模型 / 训练 / 普通符号 / 关键数据路由、1–5 hop / 方向 / 预算 / 来源锚点；有 Tk 显示时测试 Search→Reveal→Inspector→Split Source，并确认项目切换或再次 Analyze 不复用旧结果。
- Persistence / Phase 10：白名单 JSON 往返、不保存 AST / 源码、内容指纹和配置 / 版本过期、原子写失败回滚、损坏缓存与独立标注、旧图仍可查、Tk 的过期提示与标签恢复；Windows 本机额外模拟 Linux 平台 mypy。
- MVP / Phase 11：固定 `tests/fixtures/mvp_project` 含多模型、ModuleList / Sequential、训练与评估共享模型、同名 loss、动态调用、坏文件和不可执行陷阱；`test_mvp_acceptance.py` 验证三条黄金路径的领域与源码关系，`test_ui_tkapp.py` 以真实 Tk 点击串起同一 fixture；可选基线脚本只写临时目录，不执行目标项目。
- 真实项目交叉验证：`REAL_REPOSITORY_VALIDATION.md` 固定 nanoGPT、PyTorch Examples、YOLOv5 的提交与范围；本地浅克隆只读测试架构、训练、搜索、源码行和规模，不让 CI 下载或执行第三方项目。发现的可复现语义缺口转成小型自有测试用例，纳入日常离线门禁。

## 必备 fixture

通用增量使用 `tests/fixtures/general_python_project` 与 bindings / objects / data / control 独立契约测试，覆盖 main guard、普通库、闭包 / 推导式 / match、实例与 callable 候选、普通 def-use、finally 各退出类型。旧 ML 正例必须提供真实框架证据，不能仅凭 `optimizer`、`loss` 或 `train` 名称；Full Graph 的普通 Data 与 ML 默认关键数据切片分别断言。

`scripts/verify_general_python.py` 对固定 Click / Requests / HTTPX 提交仅静态扫描，报告包上下文、源码锚点、诊断、预算与无 AST 缓存往返；不安装或执行目标仓库。`scripts/verify_general_python_native.py` 使用实际 WebView2 / bridge 检查普通函数阅读链路，原 `verify_native_desktop.py` 保留 ML 黄金路径。验证报告不等于支持所有 Python。

小型纯 Python 项目、PyTorch 自定义模块、Transformer-like 模块、训练 / 验证 / 推理多入口、同名变量、if / loop / recursion、动态调用、坏文件、忽略目录和共享模块实例。

## 验证原则

核心 unittest 必须在未安装可选浏览器工具的环境中运行；不能因为开发机安装了 Playwright 就假定 CI 也存在。`test_general_python_verifier.py` 使用全新子进程阻止 Playwright 导入，验证 native verifier 的纯选择器／会话辅助可加载，真实 native main 执行时仍明确要求浏览器依赖，不跳过该选择器回归。Playwright 仅在实际浏览器断言或原生验收入口延迟导入，不进入解析核心／dev 必需依赖；真实 WebView2 验收仍在单独配置的桌面环境运行。

每个阶段运行真实仓库已有的 lint、typecheck、test、build；空仓库阶段不伪造通过结果。Phase 2 后的质量门禁已配置 Ruff（`src tests`）和 mypy 严格模式（`src/codeviz`），具体命令见 `README.md` 与 `QUALITY_GATES.md`。失败分为原有失败、环境失败、新增失败。禁止为了测试自动执行被分析项目。

## 验收

跨多个页面、会话及异步任务的完整 UI 场景可以声明局部总时限，逐动作的 `waitFor`/`findBy` 期限和全部断言仍须保留，不放宽全局配置或产品延迟要求。
H3 的项目理解整合用例串联19次交互、16个 await 和3次真实250ms轮询：单跑1767ms通过，两次全量均触发整条用例5000ms总时限，未出现具体行为断言失败。局部10000ms下复查同一组断言用时6670ms通过，进一步证明5000ms会截断正常完成的场景。因此仅将该用例总时限明确为10000ms；诊断和原始失败记录保留在本地阶段证据目录。

结构展开专项必须比较真实节点集合：MVP Classifier 从深度 1 的直接组件扩展到 Encoder.layers 与 Block.projection，减少深度时节点集合缩小；不能只验证按钮数字 / RPC 参数。另验证无模型的项目结构、源锚点、投影 Inspector、两实例 / 循环 / 预算、伪造投影拒绝、重开保留合法隐藏位置，以及无变化 / 等待 / 失败反馈。最终原生脚本通过真实控件操作，不用 API 写请求代替 UI。合同与结果见 `STRUCTURE_EXPLORATION.md`。

以 `MVP_ACCEPTANCE.md` 三条黄金路径为集成门禁。来自用户源码的图节点必须能定位到 SourceAnchor；Project、聚合和 ExternalSymbol 等节点至少须显示来源依据或明确的不可跳转原因，不得出现失效的 Open Source 动作。

画布专项包括真实 App 的层数 1→5→3→1、查询参数与 context 保留、待返回查询不卸载同 context 画布；低层相机测试覆盖节点测量 / 容器非零就绪、可见 / 离屏 viewport、换 root 与迟到完成隔离。布局测试按视图关系白名单检查纵向层次、分支并列、环有界、手动坐标与事实不变。低层 rig 不能替代实际 WebView2 测量：`scripts/verify_canvas_recovery_native.py` 从最终冻结包通过真实鼠标、按钮和节点矩形验收，不点击适应、不以 bridge 写调用绕过 UI。hops / direction 从真实工具栏读取，它们不是持久化 ViewState 字段。结果见 [画布恢复](CANVAS_RECOVERY.md)。

实际画布矩形还必须填满扣除工具栏 / 静态说明后的可用编辑区，不能仅以节点与一个窄条相交判为通过。`--geometry-only` 只读取当前实际窗口，不导航、不点击、不调用 bridge，可独立捕获容器高度回归。自动定位操作不得使用可能晚到的库内 fit 队列：用真实 React Flow 受控节点路径验证 A→未就绪 B 时没有旧请求移动 B，再以最终 PC 包实际操作复核。
