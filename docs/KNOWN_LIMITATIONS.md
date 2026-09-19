# 已知限制

## 当前工作区限制

Phase 0 初次审计发现工作区为空且无 Git 仓库；用户随后确认从空目录新建。Phase 1–4 建立 Python 领域核心、一次性扫描 / AST / Resolver、基础 Full Code Graph；Phase 5–6 加入 PyTorch 模型事实、训练事件和 Semantic Component Graph；Phase 7–9 加入局部可见图、Tkinter 桌面阅读与搜索定位；Phase 10 加入本地 Analysis Cache、独立 View State / Annotations 和 Outdated 提示。指定连接器的 `workspace_info` 在 Phase 0 返回内部错误，绑定身份当时未验证；当前阶段的目录与 Git 检查来自直接读取目标路径。

Phase 3 能处理常见绝对 / 相对导入、`src` 布局、词法作用域与显式成员，但不是完整 Python 解释器。星号导入只报 issue，不推断导出名；动态 import、运行时属性绑定、条件 / 顺序敏感的名称重绑定、推导式细分作用域和复杂嵌套 `nonlocal` 仍可能保持 Unresolved 或 Probable。缺少 `__init__.py` 的 namespace package 尚无专门合成包节点；重复定义的发生序号在插入更早的同名定义后可能改变。

Phase 4 处理基础 if / for / while、顺序、break / continue、return / raise，并会进入 `with` / `try` 的内部步骤；但异常跳转、`finally` 的所有进入路径、`match`、生成器等尚无完整 CFG。装饰器、默认参数和类基类表达式中的调用会记录在定义所处作用域。CallSite 只对可静态证明且无明显动态派发风险的函数 / 方法标 Exact；`self.method()` 保留可能目标，参数、变量、动态表达式的实际可调用目标仍 Unresolved。关键 DataNode 只来自有限名称规则；普通变量留在底层符号图，直线赋值链上的关键来源可经隐藏临时变量传递，但分支 / 循环后的来源采取保守处理。DATA_FLOW 只表达赋值语法依赖，不证明运行时值流或梯度。UI 不应直接渲染 Full Graph；Phase 7 仅把语义组件图按需转换为局部可见图。符号链接测试在当前 Windows 环境因无法创建测试链接而跳过，代码仍明确不遍历链接。单文件默认 5 MB 上限；项目级文件数和总字节数上限尚未配置，分析大项目仍需谨慎。

Phase 5 只在可追到显式且未被项目内同名 `torch` 路径遮蔽的有限导入语法上识别模型；不执行 PyTorch，也不验证运行时注册或 `super().__init__()` 是否成功。`__init__` 中常见条件 / 循环 / try 语句的 `self` 赋值可以记录为 Probable，但尚未做路径敏感的最终实例状态分析。`ModuleList.append`、复杂容器构造 / 切片、动态工厂、跨函数别名与重绑定尚未完整建模。`forward` 识别直接 `self.child(...)`、已知容器索引和简单 `for layer in self.layers`，其他间接调用仍留在通用代码图；容器动态索引不假定唯一目标。`ModuleList` 静态同构折叠次数不是运行时观察；动态次数仍未知，但可识别的推导式成员现在保留 Probable 模板，不推断 `×N`。`ModuleDict` 只读取字典字面量和未遮蔽的 `dict(具名参数=...)`，其他构造方式仍可能保持动态。模型 Instance / Invocation 仍是独立事实；Phase 6 语义图引用部分模型事实，但没有把它们并入 Full Code Graph。模块调用目标仍可在运行时变化，因此只给 Probable 或 Unresolved。未知工厂若在 forward 中被调用，仅保留带锚点的 Unresolved 候选，不猜测 PyTorch 角色。

Phase 6 的入口检测主要依赖函数名，或名称暗示用途且包含静态训练事件的顶层脚本，仍可能漏掉通过 CLI、装饰器或配置注册的入口。训练角色只覆盖有限的显式 PyTorch 调用与接收者赋值；GradScaler 的 `scale(loss).backward()` / `step(optimizer)` 仅在接收者可追踪时给 Probable，运行时可能跳过更新。`torch.no_grad` 装饰器、autocast 独立事件、gradient clipping、复杂异常路径 / match 分支和深层数据依赖尚未建模。事件顺序是词法顺序而不是完整执行轨迹；条件内调用可能不发生。`torch.save/load` 只表示 Probable checkpoint 意图。Attention-like 是基于 Q/K/V、矩阵得分和 softmax 的保守结构候选，不证明模型实际执行该算法。语义图的梯度边只表达同一循环、同一静态 `if`／条件表达式分支路径内 Backward→Optimizer 的模式；跨分支可能关系暂不推断，也不是 autograd 图。控制关系的分支汇合仍为静态近似，异常、提前返回等复杂路径不保证完整。完整语义插件生态留待后续阶段。

Phase 7–9 为 Architecture / Execution / Symbol / Data Flow 建立局部视图；Calls、Gradient 的独立视图仍需后续扩展。Architecture 以组件包含关系为主；只有模型 `forward` 作用域内可静态确认的关键 DataNode 及代码级 DATA_FLOW 会进入视图，尚未把数据边解释为组件间真实张量流。Execution 的关键数据仅来自所选入口函数作用域，不能由此推断动态调用中的值传递。收起组件及预算截断会隐藏事实，但返回 hiddenCounts、可继续下钻提示和聚合成员 ID；聚合节点不是新源码定义。布局是确定性的阅读提示，不是运行时顺序或完整 CFG。ViewState 现保存在应用数据目录；取消分析和 Partial 状态的复杂恢复交互仍需完善。默认上限 100 节点、300 边限制的是输出量，不是输入项目的解析成本。

Phase 8–10 的 Tkinter Canvas 只渲染有限局部图；自动布局使用单列纵向阅读顺序，高图需上下滚动，不保证所有节点都能同时显示在一屏。回环和用户手动移动后的位置可能仍需水平滚动。拖拽位置、已打开标签与选中节点已独立持久化，当前没有交互式缩放、平移或关系过滤控件。应用默认简体中文并支持 English，但语义节点的项目自有名称、源码和部分技术诊断不做机器翻译。Search / Symbols 查询已索引的定义与有限训练语义别名，默认最多返回 100 项；未解析引用不作为确定的定义结果，源码文件内容不做全文搜索。Symbol / Data Flow 图只显示现有 Code Graph 的 1–5 hop 邻域，不是完整调用图或运行时数据流；Source → Graph 反查仍待后续实现。Recent Projects、Outdated 检测和缓存已实现；Annotations 有独立持久化 API，但暂无桌面编辑器或 Settings 中的 Ignore Paths 表单。Analysis Issues 可单独查看，但复杂问题的自动修复不提供。源码查看仅支持项目内 `.py` 文件，单文件 5 MB 上限，UTF-8 解码错误以替代字符显示；不编辑源码。桌面启动依赖 Python 发行版实际提供 Tk 与图形显示环境，CI 无显示器时只运行可跳过的 Tk 实例测试和纯数据测试。分析目录在后台线程完成；不支持取消或增量更新。

Phase 10 缓存是全量 JSON 快照，适合中小型项目；打开项目仍需按内容计算指纹，因此不是增量索引。缓存文件上限 64 MB，单个待指纹 `.py` 文件上限 50 MB；超过或无法核验真实文件句柄时不会宣称旧缓存 Ready。分析器本身仍以 5 MB 为单文件解析上限。若应用数据目录位于被分析项目内，缓存写入会被拒绝但可继续静态分析。源码与缓存加载所需的已打开句柄校验目前支持 Windows 和带 `/proc/self/fd` 的 Linux；其他平台缺少可靠路径核验时会保守拒绝。

Phase 11 固定夹具及自动化测试已覆盖三条黄金路径的可操作性和源码行定位；它不代替第一次使用者的理解测试。50 / 200 文件性能样本是简单生成模块，不代表复杂真实 PyTorch 项目。小项目的完整 JSON 缓存解码仍可能比重新分析慢；Type Hints 的重复求值已优化，但后续仍需在真实大项目评估全量快照的内存与耗时。故意语法错误的 fixture 已仅从 Ruff 排除，产品与测试代码仍由 Ruff 检查。

真实仓库试用见 `REAL_REPOSITORY_VALIDATION.md`。3 个公开 PyTorch 仓库、4 种范围的静态图和源码路径已核验，但 UI 仍把数千条动态 / 未解析引用逐条列为 Analysis Issues；这些不等于数千个解析失败。Problems 尚无按种类 / 文件汇总与筛选；部分仅凭函数名识别的入口没有可识别的执行事件，打开后可能是空流程。不要把这些视图当成完整运行时事实。

源码读取的最终路径核验当前支持 Windows 文件句柄和 Linux `/proc/self/fd`；若平台无法可靠取得已打开文件的真实路径，或文件系统不提供可比较的文件身份，查看器会拒绝读取而不是忽略安全检查。复杂图的隐藏成员可从聚合节点进入侧栏选择并重新定位，但聚合成员侧栏尚无独立过滤；超大成员列表的交互优化留后续阶段。

## MVP 限制

- Python + PyTorch 优先，其他语言只保留 Adapter 接口。
- 静态推断不等于 runtime observation；shape / dtype / device 只展示静态可确认值。
- DDP、FSDP、DeepSpeed、复杂 distributed、自定义 autograd 深层分析、复杂 hooks、完整 autograd graph、动态 graph surgery 后置。
- `.ipynb`、YAML、JSON、TOML、Hydra 等只预留接口，首版重点 `.py`。
- 复杂动态 import、monkey patch、反射和运行时生成代码可能保持 Unresolved。
- 语义组件不是类 / 函数的一一映射，某些 Probable 结果必须保留原名。
