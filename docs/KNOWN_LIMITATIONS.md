# 已知限制

## 当前工作区限制

Phase 0 初次审计发现工作区为空且无 Git 仓库；用户随后确认从空目录新建。Phase 1–4 建立 Python 领域核心、一次性扫描 / AST / Resolver、基础 Full Code Graph；Phase 5 加入 PyTorch 模型事实；Phase 6 加入训练事件与 Semantic Component Graph；Phase 7 加入 Architecture / Execution 的局部 Visible Graph；Phase 8 加入 Tkinter 本地桌面阅读界面。仍无磁盘 ViewState 持久化或 Analysis Cache。指定连接器的 `workspace_info` 在 Phase 0 返回内部错误，绑定身份当时未验证；当前阶段的目录与 Git 检查来自直接读取目标路径。

Phase 3 能处理常见绝对 / 相对导入、`src` 布局、词法作用域与显式成员，但不是完整 Python 解释器。星号导入只报 issue，不推断导出名；动态 import、运行时属性绑定、条件 / 顺序敏感的名称重绑定、推导式细分作用域和复杂嵌套 `nonlocal` 仍可能保持 Unresolved 或 Probable。缺少 `__init__.py` 的 namespace package 尚无专门合成包节点；重复定义的发生序号在插入更早的同名定义后可能改变。

Phase 4 处理基础 if / for / while、顺序、break / continue、return / raise，并会进入 `with` / `try` 的内部步骤；但异常跳转、`finally` 的所有进入路径、`match`、生成器等尚无完整 CFG。装饰器、默认参数和类基类表达式中的调用会记录在定义所处作用域。CallSite 只对可静态证明且无明显动态派发风险的函数 / 方法标 Exact；`self.method()` 保留可能目标，参数、变量、动态表达式的实际可调用目标仍 Unresolved。关键 DataNode 只来自有限名称规则；普通变量留在底层符号图，直线赋值链上的关键来源可经隐藏临时变量传递，但分支 / 循环后的来源采取保守处理。DATA_FLOW 只表达赋值语法依赖，不证明运行时值流或梯度。UI 不应直接渲染 Full Graph；Phase 7 仅把语义组件图按需转换为局部可见图。符号链接测试在当前 Windows 环境因无法创建测试链接而跳过，代码仍明确不遍历链接。单文件默认 5 MB 上限；项目级文件数和总字节数上限尚未配置，分析大项目仍需谨慎。

Phase 5 只在可追到显式且未被项目内同名 `torch` 路径遮蔽的有限导入语法上识别模型；不执行 PyTorch，也不验证运行时注册或 `super().__init__()` 是否成功。`__init__` 中常见条件 / 循环 / try 语句的 `self` 赋值可以记录为 Probable，但尚未做路径敏感的最终实例状态分析。`ModuleList.append`、复杂容器构造 / 切片、动态工厂、跨函数别名与重绑定尚未完整建模。`forward` 识别直接 `self.child(...)`、已知容器索引和简单 `for layer in self.layers`，其他间接调用仍留在通用代码图；容器动态索引不假定唯一目标。`ModuleList` 静态同构折叠次数不是运行时观察；动态次数为未知，推导式模板为 Probable。模型 Instance / Invocation 仍是独立事实；Phase 6 语义图引用部分模型事实，但没有把它们并入 Full Code Graph。模块调用目标仍可在运行时变化，因此只给 Probable 或 Unresolved。未知工厂若在 forward 中被调用，仅保留带锚点的 Unresolved 候选，不猜测 PyTorch 角色。

Phase 6 的入口检测主要依赖函数名，可能漏掉通过 CLI、装饰器或配置注册的入口。训练角色只覆盖有限的显式 PyTorch 调用与接收者赋值；`torch.no_grad` 装饰器、autocast / GradScaler、gradient clipping、复杂异常路径 / match 分支和深层数据依赖尚未建模。事件顺序是词法顺序而不是完整执行轨迹；条件内调用可能不发生。`torch.save/load` 只表示 Probable checkpoint 意图。Attention-like 是基于 Q/K/V、矩阵得分和 softmax 的保守结构候选，不证明模型实际执行该算法。语义图的梯度边只表达同一循环、同一静态 `if`／条件表达式分支路径内 Backward→Optimizer 的模式；跨分支可能关系暂不推断，也不是 autograd 图。控制关系的分支汇合仍为静态近似，异常、提前返回等复杂路径不保证完整。完整语义插件生态留待后续阶段。

Phase 7 只为 Architecture / Execution 建立局部视图；Calls、Data Flow、Gradient 和 Symbol 的独立局部 Code Graph 视图仍需后续阶段扩展。Architecture 以组件包含关系为主；只有模型 `forward` 作用域内可静态确认的关键 DataNode 及代码级 DATA_FLOW 会进入视图，尚未把数据边解释为组件间真实张量流。Execution 的关键数据仅来自所选入口函数作用域，不能由此推断动态调用中的值传递。收起组件及预算截断会隐藏事实，但返回 hiddenCounts、可继续下钻提示和聚合成员 ID；聚合节点不是新源码定义。布局是确定性的阅读提示，不是运行时顺序或完整 CFG。ViewState 只支持 JSON 数据往返，尚未写入应用数据目录；取消分析和 Partial 状态尚未接入视图查询。默认上限 100 节点、300 边限制的是输出量，不是输入项目的解析成本。

Phase 8 的 Tkinter Canvas 只渲染有限局部图；视觉布局与拖拽位置仅供阅读，重新启动后位置不保存。Search / Symbols 在界面中是明确的 Phase 9 占位，尚不能按符号定位或 Source → Graph 反查；Recent Projects、Outdated 检测、缓存和注释属 Phase 10。Analysis Issues 可单独查看，但复杂问题的自动修复不提供。源码查看仅支持项目内 `.py` 文件，单文件 5 MB 上限，UTF-8 解码错误以替代字符显示；不编辑源码。桌面启动依赖 Python 发行版实际提供 Tk 与图形显示环境，CI 无显示器时只运行可跳过的 Tk 实例测试和纯数据测试。分析目录在后台线程完成；不支持取消或增量更新。

## MVP 限制

- Python + PyTorch 优先，其他语言只保留 Adapter 接口。
- 静态推断不等于 runtime observation；shape / dtype / device 只展示静态可确认值。
- DDP、FSDP、DeepSpeed、复杂 distributed、自定义 autograd 深层分析、复杂 hooks、完整 autograd graph、动态 graph surgery 后置。
- `.ipynb`、YAML、JSON、TOML、Hydra 等只预留接口，首版重点 `.py`。
- 复杂动态 import、monkey patch、反射和运行时生成代码可能保持 Unresolved。
- 语义组件不是类 / 函数的一一映射，某些 Probable 结果必须保留原名。
