# 系统架构

## 1. 当前约束

Phase 0 初次审计未发现现有代码或技术栈；用户确认从空目录新建。Phase 1–8 的领域层与 UI 均使用 Python 3.11+ 标准库；Phase 8 采用 Tkinter / ttk / Canvas 本地桌面壳，不新增运行时依赖，见 ADR-005、ADR-006。持久化格式仍留 Phase 10 决定。

## 2. 分层

```text
Project Source / Config
  → File Discovery / Language Detection
  → Python Parser (AST)
  → Symbol Index / Symbol Resolver
  → Code Graph IR
  → Call / Control / Data Flow
  → Framework Semantic Rules
  → Semantic Component Graph
  → View Builder / Graph Query
  → Visible Graph / Layout
  → Search / Navigation / Inspector / Source
```

## 3. 模块责任

- File Discovery：扫描 `.py`，忽略 `.git`、虚拟环境、`node_modules`、缓存、构建目录、权重、数据集、二进制；支持用户 Ignore Paths。
- Parser：将 Python AST 转为带 SourceAnchor 的解析结果；部分语法失败不能阻断其他文件。
- Symbol Index：登记 definition / raw reference、scope、qualifiedName、parentSymbol 和稳定 ID；Phase 2 不做跨文件目标解析。
- Symbol Resolver：`analysis/imports.py` 只由已扫描文件构造项目模块目录；`analysis/resolution.py` 独立处理 import、词法作用域、静态成员和 unresolved / possible targets，并输出 External / Unresolved 节点及 issues。`analyze_project` 汇总解析结果，不把名称解析散落在 UI 或 parser 中。
- Code Graph IR：`analysis/graph_build.py` 将已解析的 Project、File、Module、定义、ExternalSymbol、UnresolvedSymbol 装入 `core.CodeGraph`；调用位置、控制块、流程步骤和关键数据是带源码锚点的附加事实节点。
- Flow Analysis：`analysis/flow.py` 产生调用位置与 CALLS，`control_flow.py` 产生顺序 / if / loop 边，`data_flow.py` 产生名称线索的关键 DataNode 与静态读写 / 依赖，`call_cycles.py` 汇总确定调用环。`ProjectAnalysis.flow` 暴露图、关键数据、调用环与局部 issues；不执行代码。
- Semantic Rules：`semantic/rules.py` 定义 `ModelRule` 协议；`semantic/pytorch.py` 匹配模型和常见层；`semantic/training.py` 定义训练角色与接收者证据。规则不导入 PyTorch；完整 Generic Python / Transformer 插件仍待后续阶段。
- Model Analysis：`analysis/model.py` 在 Resolver 和 Flow 之后提取带源码锚点的模型 Definition、子模块 Instance、`forward` Invocation；通过 CallSite ID 与已有 Full Code Graph 关联。`ModuleList` 仅在静态可见的同构结构上给出折叠次数；模型事实不直接插入 Code Graph。
- Training Analysis：`analysis/training.py` 将 AST 调用与 Resolver / Model / Flow 证据组合成独立入口、训练事件和循环归属；函数参数名称仅形成 Probable 线索。
- Semantic Graph：`semantic/components.py` 组合模型定义、实例、入口和事件；关系含源码锚点与证据。控制流边表达 AST 路径的静态近似，梯度边只表示 Probable 静态模式；Attention-like 是结构性 Probable 候选。
- View Builder：`views/query.py` 索引语义关系，`views/data.py` 将入口函数或模型 `forward` 作用域内的关键 DataNode 与真实代码级 DATA_FLOW 投影到局部视图；`views/builder.py` 选择根、展开 / 选中邻域和关系白名单，限制可见节点与边并保留聚合成员 ID；`views/layout.py` 提供确定性层级 / 流程位置，`views/state.py` 只序列化展开和手动位置。完整代码图不直接交给 UI，磁盘持久化留 Phase 10。
- UI：`ui/presenter.py` 将分析与局部图转为 Overview / Inspector / Problems 纯数据；`ui/controller.py` 管理项目会话、显式一次性 Analyze、ViewState 和受限只读源码访问；`ui/graph_canvas.py` 只绘制 VisibleGraph；`ui/tkapp.py` 用 Tkinter 实现 Activity Bar、Sidebar、Editor Tabs / Split、Inspector、Problems 和后台分析消息队列。真实 Search / Symbols 导航留 Phase 9。
- Cache：Analysis Cache、View State、Annotations 三者分离。

## 4. 关键边界

Parser 只负责语法事实；Resolver 负责名称；Semantic Rules 负责解释；View Builder 负责展示取舍。任何层都必须保留回溯链：`Visual Node → Semantic Component → Code Symbol → Source Anchor`。

## 5. 运行状态

项目分析状态为 Analyzing、Ready、Partial、Outdated、Failed。文件级失败只产生 Problem，不抹掉其他文件的可用结果。

## 6. 后续技术决策

桌面壳、图绘制与源码查看由 ADR-006 的 Tkinter 方案落地；会话内 ViewState 仍沿用 Phase 7 数据类型。Phase 10 再决定 Analysis Cache、Annotations 和 ViewState 的磁盘格式；Phase 9 实现 Symbol Search / Reveal in Graph。若图形壳更换，保留 UI 无关的 Controller / Presenter 与 VisibleGraph 契约。
