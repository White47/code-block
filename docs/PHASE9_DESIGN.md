# Phase 9 搜索与图中定位设计

本阶段沿用已批准的 `UX_SPEC.md` 与 `MVP_ACCEPTANCE.md`，不更改产品方向。Phase 8 的 Tkinter 外壳、`ProjectAnalysis.symbol_index`、语义组件图、Code Graph IR 和 `VisibleGraph` 是现成基础；仍只读取本地 Python 源码，不执行项目。

## 查询与结果

新增纯数据搜索层，索引一次分析的定义（文件、模块、类、函数、方法、变量、参数、属性）及语义组件。文本匹配真实名、限定名、文件路径，并为训练角色提供中英文别名（至少“反向传播 / backward”、“参数更新 / optimizer step”）。结果明确区分代码符号与语义组件，显示限定名、类型、文件行、confidence 和定位能力；同名符号靠 scope 与文件行消歧。空查询用于 Symbols 浏览；结果数有上限，截断时明示。搜索不把未解析引用伪装成定义，也不重新解析源码。

## Reveal 路由

模型定义 → Architecture；训练事件 → 对应入口的 Execution；其余代码符号 → 局部 Symbol 图；关键 DataNode → 局部 Data Flow 图。若当前 Architecture / Execution 已能表达目标，则优先留在当前图；切换视图时明确呈现新标签。每个可定位结果通过真实 ID 选择节点，显示默认 1-hop，可按上游、下游扩展至最多 5-hop；输出遵守 100 节点 / 300 边预算。若某个结果无可构建图目标，标记原因并仍允许直接打开真实 SourceAnchor。不得静默跳到错误组件。

`ViewBuilder` 保留现有 Architecture / Execution 逻辑；为 Symbol / Data Flow 增加基于 `flow.graph` 的局部查询，只取被选中节点和必要的邻居，不直接把 Full Graph 交给 Tk。代码图边只使用已有证据，保留 `CALLS` 与 `DATA_FLOW` 等语义；不存在的数据流不补造。`VisibleNode` 保留 code ID、真实名称和锚点，Inspector 与源码导航继续用既有安全读取路径。普通变量若不在关键 DataNode 集合中，走 Symbol 图而非强行贴 Data Flow 标签。

## UI 与边界

Search Activity 提供输入框、结果列表、Reveal in Graph、Open Source；Symbols Activity 复用同一结果列表并允许浏览。查询、路由与局部图可在无显示器 CI 中测试；Tk 只负责事件。结果行显示路径和行号，图节点仍只显示易读名与真实代码名。Source → Graph 反查预留到后续，不增加源码编辑、运行时探针、AI 命名或新依赖。分析重跑 / 切换项目时清除旧结果，避免跨项目定位。

## 验证

红绿测试覆盖同名作用域、中文 / 英文语义词、模型 / 训练 / 普通符号路由、关键数据、1-hop 与方向扩展、预算和锚点；Tk 实例测试验证真实查询→Reveal→Inspector→Open to Side。阶段末运行 unittest、Ruff、mypy、wheel，并记录与旧基线的差异。
