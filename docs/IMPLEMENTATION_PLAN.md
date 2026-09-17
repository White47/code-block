# 实施计划

## 当前实施前提

Phase 0 审计时间：2026-09-17。建立文档前 `E:\Codex\代码块` 为空且不是 Git 仓库；用户随后确认从空目录新建项目，Phase 1 最小技术栈见 `adr/005-stdlib-core-first.md`。指定连接器的 `workspace_info` 尚未成功，连接器身份核验待完成。Phase 1 使用实际路径，后续阶段仍用“目标职责路径”描述并在进入时落实。

## Phase 0 — Workspace Audit + Docs

- Goal：锁定产品边界、领域契约和验收方式。
- Deliverables：本 docs 目录、4 份 ADR、审计报告。
- Files likely affected：`docs/**`。
- Dependencies：无。
- Tests：检查必需文档存在且非空、根目录与 Git 状态；当前无代码测试，也未验证文档内部链接。
- Acceptance Criteria：所有必需文档存在；技术栈未知被明确标注；连接器身份与真实仓库位置确认或明确记录为待核验；不覆盖未提交修改。
- Out of Scope：核心代码、依赖、UI、构建系统。

## Phase 1 — Core Types / SourceAnchor / Stable IDs / Graph IR

- Goal：建立可追溯、稳定、与 UI 解耦的事实模型。
- Deliverables：SourceAnchor、Confidence、CodeNode、CodeEdge、DataNode、SemanticComponent、VisibleGraph、Stable ID。
- Files likely affected：`src/codeviz/core/identity.py`、`model.py`、`graph.py`，`tests/test_identity.py`、`test_graph.py`，`pyproject.toml`、`README.md`。
- Dependencies：Python 3.11+ 标准库；无运行时第三方依赖。
- Tests：字段校验、同一作用域中多次 Instance / Invocation 的 ID 唯一性与稳定性、序列化往返、边类型约束、traceability。
- Acceptance Criteria：Definition / Instance / Invocation 可区分且 ID 不碰撞；ID 不只依赖行号；有用户源码来源的抽象可回到源码锚点，合成或外部节点显示来源或无法跳转的原因。
- Out of Scope：AST、PyTorch、UI。

## Phase 2 — Python File Discovery + AST Parser + Symbol Index

- Goal：扫描 Python 并产生可解释的语法事实与符号目录。
- Deliverables：忽略规则、Python AST、文件级错误隔离、SymbolIndexEntry、definition / reference 初稿。
- Files likely affected：`analysis/discovery`、`analysis/python`、`analysis/symbols`。
- Dependencies：现有 Python 解析能力；不得先换栈。
- Tests：fixture 覆盖 class/function/import/assignment/if/loop/async 与坏文件。
- Acceptance Criteria：部分失败不阻断；每个符号有 scope、qualifiedName、anchor。
- Out of Scope：完整跨文件解析、语义命名。

## Phase 3 — Symbol Resolver + Imports + Definitions/References

- Goal：将名称引用解析为确定、可能或未解析目标。
- Deliverables：模块 / 相对 import、scope、属性、ExternalSymbol、UnresolvedSymbol、possible targets。
- Files likely affected：`analysis/resolution`、`analysis/imports`。
- Dependencies：Phase 1–2。
- Tests：同名符号、shadowing、循环 import、动态调用、第三方 external node。
- Acceptance Criteria：dynamic call 不伪造唯一目标；Resolver 不散落在 UI / parser。
- Out of Scope：运行时导入、执行用户代码。

## Phase 4 — Call Graph + Basic Control Flow + Critical Data Flow

- Goal：恢复调用、顺序、分支、循环、递归和关键数据关系。
- Deliverables：CALLS、CONTROL_FLOW、BRANCH_TRUE/FALSE、LOOP_BACK、READS/WRITES、关键 DataNode。
- Files likely affected：`analysis/flow`、`analysis/graph-build`。
- Dependencies：Phase 2–3。
- Tests：nested if/loop、recursion、call cycle、return、关键变量和普通 tmp 隐藏。
- Acceptance Criteria：可构造 Full Graph；关系类型可区分；不把 Data Flow 当 Gradient Flow。
- Out of Scope：完整 CFG、完整 tensor propagation。

## Phase 5 — Generic PyTorch Model Recognition

- Goal：识别 Module、子模块、容器和常见层，恢复自定义模块结构。
- Deliverables：模型实体、Definition / Instance / Invocation、ModuleList ×N、Shared 标记。
- Files likely affected：`semantic/pytorch`、`analysis/model`。
- Dependencies：Phase 1–4、规则插件接口。
- Tests：常见层、自定义 Module、Sequential、ModuleList / Dict、共享实例、未知模块。
- Acceptance Criteria：模型组件均有 evidence 与源码锚点；重复结构与共享权重不混淆。
- Out of Scope：执行 forward、真实 shape、分布式框架。

## Phase 6 — Training Rules + Semantic Component Graph

- Goal：生成训练执行语义和可读组件抽象。
- Deliverables：Training Mode、Epoch / Batch、Forward、Loss、Backward、Optimizer、Scheduler、Gradient Flow。
- Files likely affected：`semantic/training`、`semantic/components`。
- Dependencies：Phase 4–5。
- Tests：训练 / 评估 / 推理多入口、no_grad、validation、checkpoint、uncertain attention-like。
- Acceptance Criteria：Exact / Probable / Unresolved 正确传播；Exact 有可检查的接收者与调用证据；提供规则依据；多入口独立 flow。
- Out of Scope：复杂 distributed、完整 autograd、无限层级。

## Phase 7 — View Builder + Architecture / Execution Visible Graph

- Goal：从 Full Graph 生成受控局部 Visible Graph。
- Deliverables：Graph Query、聚合 / 折叠、1-hop 上下文、Architecture 层级布局、Execution 流程布局、Legend。
- Files likely affected：`views`、`graph-query`、`layout`。
- Dependencies：Phase 1–6。
- Tests：节点阈值、聚合、视图关系过滤、展开 / 收起、布局稳定性。
- Acceptance Criteria：默认几十到约 100 节点；超过阈值不直接铺满；用户位置可保存。
- Out of Scope：最终 UI 皮肤、实时增量更新。

## Phase 8 — VSCode-style UI + Tabs/Split + Inspector

- Goal：打通 Overview、视图、源码和 Inspector 的阅读体验。
- Deliverables：Activity Bar、Sidebar、Editor Tabs / Split、Inspector、Problems、Source viewer。
- Files likely affected：`ui/layout`、`ui/views`、`ui/inspector`、`ui/source`。
- Dependencies：Phase 7；复用真实仓库已有 UI / graph 组件。
- Tests：单击、双击、Open Source、Split、面包屑、键盘操作、空状态和 Partial。
- Acceptance Criteria：完成架构与训练两条视觉链路；节点默认显示易读名 + 真实名。
- Out of Scope：源码编辑 / 重构。

## Phase 9 — Search / Reveal in Graph / Source Navigation

- Goal：闭环 Search → Graph → Inspector → Source。
- Deliverables：Symbol Search、Semantic Search、scope disambiguation、local context、Reveal in Graph。
- Files likely affected：`search`、`ui/search`、`navigation`。
- Dependencies：Phase 3、6–8。
- Tests：同名 symbol、中文 / 英文语义词、不同视图定位、1-hop、Open to Side。
- Acceptance Criteria：每条结果都能定位或明确标记无法定位的原因。
- Out of Scope：复杂语义编辑器、模糊 AI 命名。

## Phase 10 — Cache / Health / Problems / Layout Persistence

- Goal：提供可恢复、可解释的本地持久化。
- Deliverables：Analysis Cache、View State、Annotations 分离；fingerprint、version、Outdated、health、Problems。
- Files likely affected：`persistence`、`cache`、`health`、`problems`。
- Dependencies：Phase 1–9；存储格式按真实仓库 ADR 决定。
- Tests：源码、Ignore Paths、分析配置、解析器 / 规则版本变化均标 Outdated 或使缓存安全失效；无变化加载、缓存损坏、annotation 保留、源码仓库无污染。
- Acceptance Criteria：不保存完整源码副本；旧结果可查看但显式 Outdated。
- Out of Scope：增量 watcher、远程缓存。

## Phase 11 — MVP Hardening + Fixtures + Acceptance Tests

- Goal：以用户黄金路径完成发布前验证。
- Deliverables：稳定 fixture、回归测试、性能基线、验收报告、已知限制。
- Files likely affected：`fixtures`、`tests`、`docs/KNOWN_LIMITATIONS.md`。
- Dependencies：Phase 1–10。
- Tests：lint、typecheck、unit、integration、UI / acceptance、build（以真实仓库脚本为准）。
- Acceptance Criteria：三条黄金路径通过；原有失败与新增失败分开记录；不执行用户代码。
- Out of Scope：后置能力。

## 阶段门禁

每阶段结束必须记录 changed files、实现摘要、适用的 lint / typecheck / test / build 结果、原有失败与新增失败、下一阶段。文档与实现冲突时同一阶段内修正文档或实现，并在 ADR / changelog 留痕。
