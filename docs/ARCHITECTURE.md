# 系统架构

## 1. 当前约束

Phase 0 初次审计未发现现有代码或技术栈；用户确认从空目录新建。领域层与 UI 使用 Python 3.11+ 标准库；桌面壳采用 Tkinter / ttk / Canvas，持久化采用有大小限制的本地 JSON，见 ADR-005、ADR-006、ADR-007。交互式源码画布只借鉴 draw.io 的分层布局、画布导航和局部容器交互，不捆绑 draw.io、浏览器运行时、WebView 或网络布局服务。

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
  → Visible Graph / Deterministic Layered Layout
  → Canvas Scene
  → Tk Canvas / Search / Navigation / Inspector / Source
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
- View Builder：`views/query.py` 索引语义关系，`views/data.py` 将入口函数或模型 `forward` 作用域内的关键 DataNode 与真实代码级 DATA_FLOW 投影到局部视图；`views/builder.py` 选择根、展开 / 选中邻域和关系白名单；`views/code_context.py` 从已有 Full Code Graph 取 Symbol / Data Flow 的有向 1–5 hop 邻域。输出限制节点与边并保留聚合成员 ID；`views/layout.py` 以确定性的 rank、同层排序和分支列生成从上到下的位置，手动位置最后覆盖。完整代码图不直接交给 UI。
- Search / Navigation：`search.py` 对索引定义及带锚点语义组件做有限结果查询，保留 scope、qualifiedName、SourceAnchor 与 confidence；`navigation.py` 将选中结果映射到 Architecture / Execution / Symbol / Data Flow 及真实根 ID，无法定位时返回显式原因，不猜测动态引用目标。
- Canvas Scene：`ui/canvas_scene.py` 是纯 UI 投影边界；`graph_scene()` 每次把一个 `VisibleGraph` 转为场景节点、关系路由和尺寸，并提供可逆坐标变换、确定性岛放置与避障折线等纯几何函数。它不组合多个图，也不修改 Code Graph、Semantic Graph 或 Visible Graph。
- UI：`ui/presenter.py` 将分析与局部图转为 Overview / Inspector / Problems 纯数据；`ui/controller.py` 管理项目会话、显式一次性 Analyze、每岛最多 18 节点 / 40 边的独立局部查询、View State 和受限只读源码访问；`ui/graph_canvas.py` 接收一个主 `VisibleGraph` 以及零到三个细节图 / 状态对，分别调用 Canvas Scene 投影，组合细节岛和来源连线，再绘制到 Tk Canvas，并分离选择、节点 / 岛拖动、平移、缩放、滚动、Fit、100% 与源码动作；`ui/tkapp.py` 协调标签、主图与岛上下文、统一 Inspector、源码导航、Outdated 警告和后台分析消息队列。
- Cache：Analysis Cache、View State、Annotations 三者分离。View State 记录每视图 viewport 以及细节岛来源 / 位置；无效 View State 不能删除或阻止加载有效 Analysis Cache。

## 4. 关键边界

Parser 只负责语法事实；Resolver 负责名称；Semantic Rules 负责解释；View Builder 负责展示取舍；Canvas Scene 只负责绘制几何。任何层都必须保留回溯链：`Visual Node → Semantic Component → Code Symbol → Source Anchor`。主图与细节岛都由受限 `VisibleGraph` 生成，画布交互不触发重新分析或语义图重建。

## 5. 运行状态

项目分析状态为 Analyzing、Ready、Partial、Outdated、Failed。文件级失败只产生 Problem，不抹掉其他文件的可用结果。Outdated 仍允许读取缓存图并显示原因；单个细节岛无法重建时只清理 / 跳过该岛，主图与源码导航继续可用。

## 6. 后续技术决策

桌面壳、图绘制与源码查看由 ADR-006 的 Tkinter 方案落地；Analysis Cache、Annotations、Recent Projects 和 View State 的 JSON 格式由 Phase 10 落地。若图形壳更换，保留 UI 无关的 Controller / Presenter、VisibleGraph 与 Canvas Scene 契约。`.drawio` 导出仍是独立后续能力，只能从 Visible Graph / Canvas Scene 生成，不能反向成为分析事实来源。
