# 系统架构

## 1. 当前约束

Phase 0 初次审计未发现现有代码或技术栈；用户确认从空目录新建。领域层使用 Python 3.11+，持久化采用有大小限制的本地 JSON，见 ADR-005、ADR-007。桌面展示正按 ADR-008 从 Tkinter fallback 迁移到 bundled React Flow + pywebview；生产无 CDN，打包静态资源只由随机端口、仅绑定 loopback 的服务器提供，应用 bridge 不走 HTTP。Tk fallback 保留到桌面验收通过。

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
  → Visible Graph / Deterministic Layout Hints
  → Web Contract / Canvas Scene
  → React Flow Desktop (primary migration target) / Tk Canvas (temporary fallback)
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
- View Builder：`views/query.py` 索引语义关系，`views/data.py` 将入口函数或模型 `forward` 作用域内的关键 DataNode 与真实代码级 DATA_FLOW 投影到局部视图；`views/builder.py` 选择根、展开 / 选中邻域和关系白名单；`views/code_context.py` 从已有 Full Code Graph 取 Symbol / Data Flow 的有向 1–5 hop 邻域。输出限制节点与边并保留聚合成员 ID；`views/layout.py` 按各视图明确关系白名单做稳定纵向分层，SCC 压缩后对 DAG 有界排层；Symbol / Data Flow 查询焦点不强制置顶，Data 使用已投影 VisibleEdge 方向，不再次反转 READS。结构 / 控制视图保留显式根在上，回边不制造无限深度。分层算法异常时仅在布局边界捕获，返回 Stable ID 顺序的纵向位置及结构化 `LAYOUT_FALLBACK`；Controller 有界去重并交给 Problems，分析事实和缓存序列化不变。手动位置最后覆盖，完整代码图不直接交给 UI。前端相机以项目会话 / view / root 为 context，在节点测量就绪后定位，不从固定延迟猜测完成，也不因同 context 选中 / 查询刷新反复重挂载。
- Search / Navigation：`search.py` 对索引定义及带锚点语义组件做有限结果查询，保留 scope、qualifiedName、SourceAnchor 与 confidence；`navigation.py` 将选中结果映射到 Architecture / Execution / Symbol / Data Flow 及真实根 ID，无法定位时返回显式原因，不猜测动态引用目标。
- Canvas Scene：`ui/canvas_scene.py` 是纯 UI 投影边界；`graph_scene()` 每次把一个 `VisibleGraph` 转为场景节点、关系路由和尺寸，并提供可逆坐标变换、确定性岛放置与避障折线等纯几何函数。它不组合多个图，也不修改 Code Graph、Semantic Graph 或 Visible Graph。
- UI：`ui/presenter.py` 将分析与局部图转为 Overview / Inspector / Problems 纯数据；`ui/controller.py` 管理项目会话、显式一次性 Analyze、每岛最多 18 节点 / 40 边的独立局部查询、View State 和受限只读源码访问；`ui/web_contract.py` 将 session、search + Python 规划的 RevealTarget、ViewState、preferences 及既有 presenter / graph 数据转换为版本化纯 JSON DTO，拒绝非有限坐标并丢弃未知 metadata / query context。pywebview bridge 是 React 的唯一应用 API，不经 HTTP；随机 loopback 服务器只提供本地构建静态资源且不开放远程监听。`ui/graph_canvas.py` / `ui/tkapp.py` 在桌面验收前继续作为 fallback。
- Cache：Analysis Cache、View State、Annotations 三者分离。View State 记录每视图 viewport 以及细节岛来源 / 位置；无效 View State 不能删除或阻止加载有效 Analysis Cache。

## 4. 关键边界

### 通用 Python 分析增量

- `analysis/entries.py` 识别 main guard、`__main__.py` 与仅凭名称的 Probable main 候选；普通库不必有自动入口。Overview 的模块、类、具名函数 / 方法目录独立于 ML 入口。
- `views/function_flow.py` 从所选词法作用域的 Code Graph 投影独立 `FunctionFlow`，不复用训练 Execution，不自动内联被调函数或嵌套定义。缓存恢复后不需要 AST 即可构图。
- `analysis/lexical_scopes.py` 为 Symbol / Resolver / Call / Data 共用作用域边界。lambda / comprehension 使用非定义 `LexicalScope`；默认值与首 iterable 留在创建作用域，延迟主体不冒充外围即时执行。
- `analysis/relations.py` 使用已有 Resolver 生成 IMPORTS / INHERITS。`analysis/value_candidates.py` 单独跟踪有限实例 / callable 候选；词法定义归属和可能运行时值不是一件事。重绑定、分支未知和预算截断必须保留不确定性。
- `analysis/def_use.py` 补充普通参数 / 赋值 / 返回值的有界语法依赖，标记 `generic_data`。`FlowAnalysis.data_nodes` 仍是 ML 关键数据集合；普通变量只在局部 Symbol / Data Flow 查询中按需显示，不灌入模型架构和训练主图。
- FunctionFlow 的分支、循环及 finally 退出路径来自静态 CFG；隐式异常、协程调度和 generator 恢复时序不完整时提供 Partial 诊断。训练图仍由独立语义层构建，不能把普通 CFG 的完整性直接套用于训练语义。

该增量的验收与未完成项见 `GENERAL_PYTHON_PLAN.md`、`GENERAL_PYTHON_VALIDATION.md`，不能以模块存在替代验收。

Parser 只负责语法事实；Resolver 负责名称；Semantic Rules 负责解释；View Builder 负责展示取舍；Canvas Scene 只负责绘制几何。语义图保留 `Visual Node → Semantic Component → Code Symbol → Source Anchor`；普通 FunctionFlow / Symbol / Data 图可直接经 CodeNode / Code Symbol 追溯 SourceAnchor，不强制创建语义组件。主图与细节岛都由受限 `VisibleGraph` 生成，画布交互不触发重新分析或语义图重建。

## 5. 运行状态

### 结构探索修复合同（2026-10-07）

Architecture 使用独立结构深度，`ProjectStructure` 从既有 Symbol Index 投影模块 / 类 / 函数的真实所有权；Symbol / DataFlow 的 hops 仍是关系邻域，不复用为结构层级。自定义组件的内部通过真实 `target_definition_id` 进行带实例上下文的局部投影，Code Graph / Semantic Graph 不增加伪造运行实例。投影身份解析验证当前项目、根、真实定义成员路径和深度上限，查询、Inspector、位置写入、细节岛及状态清理共用校验；合法持久身份与当前可见身份分开。深度临时，重开默认 1 层，不以新缓存版本强迫重分析；合法但暂不可见的手动位置保留。完整合同与实施证据见 `STRUCTURE_EXPLORATION.md`。

项目分析状态为 Analyzing、Ready、Partial、Outdated、Failed。文件级失败只产生 Problem，不抹掉其他文件的可用结果。源码 / 配置过期且版本兼容时允许读取旧缓存图并显示原因；版本不兼容的 Analysis Cache 拒绝解码、要求重分析，独立状态 / 标注保留。单个细节岛无法重建时只清理 / 跳过该岛，主图与源码导航继续可用。

## 6. 后续技术决策

桌面壳、图绘制与源码查看按 ADR-008 迁移到 React Flow + pywebview；ADR-006 的 Tkinter 实现暂作 fallback。Analysis Cache、Annotations、Recent Projects 和 View State 的 JSON 格式由 Phase 10 落地。壳层必须保留 UI 无关的 Controller / Presenter、VisibleGraph 与版本化 Web Contract；`ignore_paths` 后续作为独立分析设置，不进入语言 preferences DTO。`.drawio` 导出仍是独立后续能力，只能从 Visible Graph / Canvas Scene 生成，不能反向成为分析事实来源。

## 项目理解版本与解释来源层（2026-10-29）

静态 Parse / Resolve / Code Graph / Semantic Graph 保持独立。显式项目任务基于当前授权的项目索引、代表文件和真实跨文件关系取得证据；其已校验 Answer 在原 ConversationStore 事务中保留一个 ProjectUnderstanding 引用。三个主视图消费该引用，成功更新原子替换，失败保留原有效结果；普通追问不会写入此项目版本。

项目理解是解释层，不向静态 IR、C3 视图映射或用户文件注入 AI 节点。ProjectStructure 与 Architecture 始终分别消费原静态 GraphEditor；AI 七段解释只在同主区附加，不生成第三张合成图或独立 AI 布局。静态位置/相机继续走原规则。语义分组与关系两端没有确定映射时，不构造伪造的调用/包含边。主区C6导航附加 artifact/revision/item 约束，保留原lease、授权、源码新鲜度、精确身份与发布CAS；面板显示状态及当前聊天不授予引用权威。

跨会话条目追问分别核对原项目解释owner和新问题owner。先前解释是最多1000个Unicode字符的不可信衍生数据，使用服务拥有的 AnswerOriginManifest / PriorInterpretationOrigin 说明其完整可能披露来源；不伪装为 AST 源码、不授予新的证据或组成员。Manifest 绑定原 Answer 摘要、原owner、当前项目/分析/配置/授权/来源范围以及全部依赖文件的内容指纹，与原保留事务一起计容量，不保存原prompt或完整wire。

私有 interpretation occurrence 在 generic / DeepSeek 组装与最终插入后均需保持精确归属，SDK 不向服务商转发内部标签或manifest。C5在原子准入中将衍生解释字符与源码字符相加，依赖文件取并集，重复发送重复计量，原公开额度不变。缺闭包、复制未归属、源码变化或权限失效必须拒绝；旧证据ID不能成为新任务的provided。新的事实仍由只读工具重新取得并绑定；派生来源回合不因没有新的read而被误标source-free。具体契约见项目理解实施计划及设计规格。
