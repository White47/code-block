# 已知限制

## 通用 Python 增量口径（2026-10-05）

下方 Phase 3 / 4 / 6 段落记载早期阶段能力；本轮增量已实现隐式词法作用域、对象 / callable 候选、普通 def-use 和 match / finally 静态结构，不能再把它们一概当作未实现。各实现阶段需求 / 质量复审和最新产物路径测试已通过；最终整体结论、历史失败与证据以 `GENERAL_PYTHON_VALIDATION.md` 为准。

- 普通库可从 Overview 的具名函数目录进入 FunctionFlow，不需要模型或训练入口。main guard / `__main__.py` 是结构候选，不证明运行入口；CLI 注册、装饰器工厂与配置驱动入口未完整推断。
- lambda / comprehension / match 捕获按词法边界建模；冲突 global / nonlocal 声明产生 Partial，不制造外层参数或假 Exact。定义归属正确不等于该变量在运行时已绑定。
- 实例方法 / super / 函数别名仅作有限可能目标传播，不是完整类型推断、Python MRO 或动态派发模拟。反射、metaclass、descriptor、动态属性、复杂 heap mutation 仍可能未知；候选 / 循环预算截断会报告限制。
- 普通 Data 按需构成有界、过程内 may def-use。分支来源是可能集合，循环近似不是运行时迭代，属性 / 容器修改和任意跨函数值传播不完整。ML 关键数据列表与通用 Data 分开，普通 tmp 不因此出现在训练主图。
- finally 的值来源采用保守合并，读取及后继显式 unknown / Partial，不是精确退出路径数据分析。def-use 单作用域 / 文件累计工作预算与继承 / 重导出累计查询预算可截断结果，包括省略的函数；保留已有事实，不因 AST 解析成功就声称关系完整。
- FunctionFlow 表达 match 的条件分支及 finally 对已知 return / raise / break / continue 出口的作用；隐式异常传播、with 的动态退出效果、await 调度与 generator 暂停 / 恢复时序仍不完整，会标 Partial。训练语义聚合不自动继承完整普通 CFG，复杂异常路径仍需谨慎阅读。
- ML 入口必须有实际框架接收者 / 模型等证据。单独 `import torch`、叫 `optimizer` 的参数或叫 `validate` / `export_csv` 的函数不充分；由外部注入而没有静态证据的训练函数可能不自动归为 ML，但仍可在普通函数流程中阅读。
- Full Graph 更大意味着内存 / JSON 缓存成本增加；Visible Graph 的节点预算不限制分析成本。固定真实语料的解析与源码核对不等于支持所有 Python。

缓存分析版本升级后要求重新 Analyze；独立布局 / 标注不因分析缓存失效被删除。所有验证均禁止执行被分析项目。

## 当前工作区限制

Phase 0 初次审计发现工作区为空且无 Git 仓库；用户随后确认从空目录新建。Phase 1–4 建立 Python 领域核心、一次性扫描 / AST / Resolver、基础 Full Code Graph；Phase 5–6 加入 PyTorch 模型事实、训练事件和 Semantic Component Graph；Phase 7–9 加入局部可见图、Tkinter 桌面阅读与搜索定位；Phase 10 加入本地 Analysis Cache、独立 View State / Annotations 和 Outdated 提示。指定连接器的 `workspace_info` 在 Phase 0 返回内部错误，绑定身份当时未验证；当前阶段的目录与 Git 检查来自直接读取目标路径。

Phase 3 能处理常见绝对 / 相对导入、`src` 布局、词法作用域与显式成员，但不是完整 Python 解释器。星号导入只报 issue，不推断导出名；动态 import、运行时属性绑定、条件 / 顺序敏感的名称重绑定、推导式细分作用域和复杂嵌套 `nonlocal` 仍可能保持 Unresolved 或 Probable。缺少 `__init__.py` 的 namespace package 尚无专门合成包节点；重复定义的发生序号在插入更早的同名定义后可能改变。

Phase 4 处理基础 if / for / while、顺序、break / continue、return / raise，并会进入 `with` / `try` 的内部步骤；但异常跳转、`finally` 的所有进入路径、`match`、生成器等尚无完整 CFG。装饰器、默认参数和类基类表达式中的调用会记录在定义所处作用域。CallSite 只对可静态证明且无明显动态派发风险的函数 / 方法标 Exact；`self.method()` 保留可能目标，参数、变量、动态表达式的实际可调用目标仍 Unresolved。关键 DataNode 只来自有限名称规则；普通变量留在底层符号图，直线赋值链上的关键来源可经隐藏临时变量传递，但分支 / 循环后的来源采取保守处理。DATA_FLOW 只表达赋值语法依赖，不证明运行时值流或梯度。UI 不应直接渲染 Full Graph；Phase 7 仅把语义组件图按需转换为局部可见图。符号链接测试在当前 Windows 环境因无法创建测试链接而跳过，代码仍明确不遍历链接。单文件默认 5 MB 上限；项目级文件数和总字节数上限尚未配置，分析大项目仍需谨慎。

Phase 5 只在可追到显式且未被项目内同名 `torch` 路径遮蔽的有限导入语法上识别模型；不执行 PyTorch，也不验证运行时注册或 `super().__init__()` 是否成功。`__init__` 中常见条件 / 循环 / try 语句的 `self` 赋值可以记录为 Probable，但尚未做路径敏感的最终实例状态分析。`ModuleList.append`、复杂容器构造 / 切片、动态工厂、跨函数别名与重绑定尚未完整建模。`forward` 识别直接 `self.child(...)`、已知容器索引和简单 `for layer in self.layers`，其他间接调用仍留在通用代码图；容器动态索引不假定唯一目标。`ModuleList` 静态同构折叠次数不是运行时观察；动态次数仍未知，但可识别的推导式成员现在保留 Probable 模板，不推断 `×N`。`ModuleDict` 只读取字典字面量和未遮蔽的 `dict(具名参数=...)`，其他构造方式仍可能保持动态。模型 Instance / Invocation 仍是独立事实；Phase 6 语义图引用部分模型事实，但没有把它们并入 Full Code Graph。模块调用目标仍可在运行时变化，因此只给 Probable 或 Unresolved。未知工厂若在 forward 中被调用，仅保留带锚点的 Unresolved 候选，不猜测 PyTorch 角色。

Phase 6 的入口检测主要依赖函数名，或名称暗示用途且包含静态训练事件的顶层脚本，仍可能漏掉通过 CLI、装饰器或配置注册的入口。训练角色只覆盖有限的显式 PyTorch 调用与接收者赋值；GradScaler 的 `scale(loss).backward()` / `step(optimizer)` 仅在接收者可追踪时给 Probable，运行时可能跳过更新。`torch.no_grad` 装饰器、autocast 独立事件、gradient clipping、复杂异常路径 / match 分支和深层数据依赖尚未建模。事件顺序是词法顺序而不是完整执行轨迹；条件内调用可能不发生。`torch.save/load` 只表示 Probable checkpoint 意图。Attention-like 是基于 Q/K/V、矩阵得分和 softmax 的保守结构候选，不证明模型实际执行该算法。语义图的梯度边只表达同一循环、同一静态 `if`／条件表达式分支路径内 Backward→Optimizer 的模式；跨分支可能关系暂不推断，也不是 autograd 图。控制关系的分支汇合仍为静态近似，异常、提前返回等复杂路径不保证完整。完整语义插件生态留待后续阶段。

Phase 7–9 为 Architecture / Execution / Symbol / Data Flow 建立局部视图；Calls、Gradient 的独立视图仍需后续扩展。Architecture 以组件包含关系为主；只有模型 `forward` 作用域内可静态确认的关键 DataNode 及代码级 DATA_FLOW 会进入视图，尚未把数据边解释为组件间真实张量流。Execution 的关键数据仅来自所选入口函数作用域，不能由此推断动态调用中的值传递。收起组件及预算截断会隐藏事实，但返回 hiddenCounts、可继续下钻提示和聚合成员 ID；聚合节点不是新源码定义。自动位置按各视图关系方向稳定纵向分层，Symbol / Data Flow 上游允许在查询焦点上方；环内、断开的同级节点和手动移动后的节点仍可并列，不保证消除所有横向宽度。布局只是阅读提示，不是运行时顺序或完整 CFG。关系范围可在 1–5 层间增加 / 减少；换 root 自动定位，正常可见的同 context viewport 保留。ViewState 保存在应用数据目录，不新增按 root 的持久格式；取消分析和 Partial 状态的复杂恢复交互仍需完善。默认上限 100 节点、300 边限制的是输出量，不是输入项目的解析成本。

Tkinter Canvas 只渲染有限局部图；复杂交叉边、同 rank 回退、回环、手动位置和多个大岛仍可能需要水平 / 纵向滚动，自动避障在所有候选路径受阻时会退回直接连线。细节岛各自限制为最多 18 节点 / 40 边，每视图最多三个，且首版不支持从岛内继续嵌套新岛。画布已支持并持久化每视图的 canvas pan、缩放、Fit / 100% 结果和岛位置；原生滚动条、普通滚轮与 Page Up / Page Down 的滚动偏移只是当前会话状态。当前没有通用关系过滤器或自由绘图 / 编辑能力；`.drawio` 导出仍属后续范围，产品不捆绑 draw.io runtime、WebView 或网络布局依赖。应用默认简体中文并支持 English，但语义节点的项目自有名称、源码和部分技术诊断不做机器翻译。Search / Symbols 查询已索引的定义与有限训练语义别名，默认最多返回 100 项；未解析引用不作为确定的定义结果，源码文件内容不做全文搜索。Symbol / Data Flow 图只显示现有 Code Graph 的 1–5 hop 邻域，不是完整调用图或运行时数据流；Source → Graph 反查仍待后续实现。Recent Projects、Outdated 检测和缓存已实现；Annotations 有独立持久化 API，但暂无桌面编辑器或 Settings 中的 Ignore Paths 表单。Analysis Issues 可单独查看，但复杂问题的自动修复不提供。源码查看仅支持项目内 `.py` 文件，单文件 5 MB 上限，UTF-8 解码错误以替代字符显示；不编辑源码。桌面启动依赖 Python 发行版实际提供 Tk/Tcl 与图形显示环境；无显示器时 live-Tk 测试会跳过，不能视为人工视觉验收。分析目录在后台线程完成；不支持取消或增量更新。

Phase 10 缓存是全量 JSON 快照，适合中小型项目；打开项目仍需按内容计算指纹，因此不是增量索引。缓存文件上限 64 MB，单个待指纹 `.py` 文件上限 50 MB；超过或无法核验真实文件句柄时不会宣称旧缓存 Ready。分析器本身仍以 5 MB 为单文件解析上限。若应用数据目录位于被分析项目内，缓存写入会被拒绝但可继续静态分析。源码与缓存加载所需的已打开句柄校验目前支持 Windows 和带 `/proc/self/fd` 的 Linux；其他平台缺少可靠路径核验时会保守拒绝。

Phase 11 固定夹具及自动化测试已覆盖三条黄金路径的可操作性和源码行定位；它不代替第一次使用者的理解测试。50 / 200 文件性能样本是简单生成模块，不代表复杂真实 PyTorch 项目。小项目的完整 JSON 缓存解码仍可能比重新分析慢；Type Hints 的重复求值已优化，但后续仍需在真实大项目评估全量快照的内存与耗时。故意语法错误的 fixture 已仅从 Ruff 排除，产品与测试代码仍由 Ruff 检查。

真实仓库试用见 `REAL_REPOSITORY_VALIDATION.md`。3 个公开 PyTorch 仓库、4 种范围的静态图和源码路径已核验，但 UI 仍把数千条动态 / 未解析引用逐条列为 Analysis Issues；这些不等于数千个解析失败。Problems 尚无按种类 / 文件汇总与筛选；部分仅凭函数名识别的入口没有可识别的执行事件，打开后可能是空流程。不要把这些视图当成完整运行时事实。

源码读取的最终路径核验当前支持 Windows 文件句柄和 Linux `/proc/self/fd`；若平台无法可靠取得已打开文件的真实路径，或文件系统不提供可比较的文件身份，查看器会拒绝读取而不是忽略安全检查。复杂图的隐藏成员可从聚合节点进入侧栏选择并重新定位，但聚合成员侧栏尚无独立过滤；超大成员列表的交互优化留后续阶段。

## React 原生桌面与分发边界

结构探索修复的最新合同见 `STRUCTURE_EXPLORATION.md`：模型架构与项目结构使用 1–5 级结构深度，Symbol / Data Flow 使用关系跳数，FunctionFlow / Execution 不提供无效跳数按钮。上文“关系范围可增加 / 减少”不可理解为所有图均支持邻域展开。组件内部沿已有定义引用展示，不补造动态工厂、运行注册、张量数据流或实际调用次数；循环引用及单图预算均有界。深度是临时查询状态，重新打开默认深度 1；合法隐藏节点的位置仍保留。Calls / Gradient 的独立视图仍未实现。新合同的真实 PC 验收以对应验证记录为准，不由旧相机测试代替。

React 的 Inspector 暂未把所有关系 / 成员 ID 转换为易读名称，也未实现 Inspector 内源码预览；完整源码通过标签或侧边查看。旧 Tk 的所有键盘 / 滚动交互不应视为新 React 界面的承诺，具体差距见 UX_SPEC 的 React 实施口径。Windows 原生入口已显式指定 Edge Chromium；仅安装 Linux WebView 依赖不会使该入口自动支持 Linux。

主命令 `codeviz` 已转为 pywebview / React 桌面，`codeviz-tk` 保留上文 Tk 基线能力与限制。Windows 原生窗口需要系统 WebView2 Runtime；PyInstaller onedir 不内置该系统运行时，必须分发整个 `dist/CodeViz`，不能只复制 exe。Linux core 安装不需要 GTK / Qt；Linux 桌面后端配置与跨平台安装包不属于当前 Windows 交付。

运行前端资源全部本地分发，无 CDN；首次依赖安装与构建仍需获取依赖。静态 loopback 服务只允许 frontend 资源根，不得暴露项目根、源码、缓存或应用 HTTP API。wheel 包含 `codeviz/ui/assets`，冻结包包含 `_internal/frontend/dist`，源码运行使用 `frontend/dist`。无 GUI CI 与 CheckOnly 只能证明依赖和产物检查，不能代替 WebView2 窗口、视觉交互与离线黄金路径验收。packaging 锁定直接工具版本，未提供全部传递依赖的哈希锁。

## MVP 限制

- Python + PyTorch 优先，其他语言只保留 Adapter 接口。
- 静态推断不等于 runtime observation；shape / dtype / device 只展示静态可确认值。
- DDP、FSDP、DeepSpeed、复杂 distributed、自定义 autograd 深层分析、复杂 hooks、完整 autograd graph、动态 graph surgery 后置。
- `.ipynb`、YAML、JSON、TOML、Hydra 等只预留接口，首版重点 `.py`。
- 复杂动态 import、monkey patch、反射和运行时生成代码可能保持 Unresolved。
- 语义组件不是类 / 函数的一一映射，某些 Probable 结果必须保留原名。

## 架构画布与局部详情

- 模型首屏的深度 2 提示只来自该根的直属、已确认容器；不能据此声称所有模型或动态容器均已恢复完整结构。
- 详情预算为相对 3 层、全局 5 层、18 节点／40 边。达到层级或预算上限须报告截断；未知 `config.n_layer` 仍是未知次数模板，不静态冒充运行时 12 个实例。
- 自动卡片／详情岛布局和正交路由避免第三方卡片遮挡；手动保存的重叠不会被擅自清除。复杂关系仍可能交叉或共线，路由预算耗尽时保留关系并显示警告，而不是删除边。
- 实际 SVG 验收按不超过 0.75 屏幕像素的间隔采样；这不是解析几何证明。报告中的交叉／共享数量为采样折线段对数，不是唯一拓扑交点数；不能把节点避障通过说成全图零交叉。
