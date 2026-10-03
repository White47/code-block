# 实施计划

## 当前实施前提

Phase 0 审计时间：2026-09-17。建立文档前 `E:\Codex\代码块` 为空且不是 Git 仓库；用户随后确认从空目录新建项目，Phase 1 最小技术栈见 `adr/005-stdlib-core-first.md`。指定连接器的 `workspace_info` 在 Phase 0 未成功；当前阶段直接审计目标路径与 Git 状态。Phase 1–8 已按实际代码路径落实，后续阶段进入时再细化。

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
- Files likely affected：`src/codeviz/analysis/discovery.py`、`python.py`、`symbols.py`、`project.py`，`tests/test_discovery.py`、`test_parser.py`、`test_symbols.py`。
- Dependencies：Phase 1 领域类型与 Python 3.11+ 标准库；不新增运行时依赖。
- Tests：fixture 覆盖 class/function/import/assignment/if/loop/async 与坏文件。
- Acceptance Criteria：部分失败不阻断；每个成功解析的符号有 scope、qualifiedName、anchor；raw reference 保持未解析，交 Phase 3 独立 Resolver。
- Out of Scope：完整跨文件解析、语义命名。

## Phase 3 — Symbol Resolver + Imports + Definitions/References

- Goal：将名称引用解析为确定、可能或未解析目标。
- Deliverables：常见项目内绝对 / 相对 import、词法 scope、静态成员、ExternalSymbol、UnresolvedSymbol、possible targets；导入声明即使未被使用也检查，解析结果保留原始 SymbolIndex 的独立副本。
- Files likely affected：`src/codeviz/analysis/imports.py`、`resolution.py`、`symbols.py`、`project.py`，`tests/test_resolution.py`，相关文档。
- Dependencies：Phase 1–2、Python 3.11+ 标准库；无新增运行时依赖。
- Tests：同名符号 / 作用域遮蔽、重复函数体、绝对 / 相对 / `src` 布局导入、包重导出、真实跨模块循环与纯别名循环、动态属性、第三方 external、缺失本地成员、星号导入、未使用导入、`global` / `nonlocal`。
- Acceptance Criteria：能唯一证明的引用才填 `resolved_definition_id`；其余保留 possible targets 或 Unresolved 与 issue；动态属性不伪造唯一目标；Resolver 独立于 UI / parser；不执行用户代码。
- Out of Scope：运行时导入、完整 Python import 语义、调用图 / 控制流 / 数据流。

## Phase 4 — Call Graph + Basic Control Flow + Critical Data Flow

- Goal：恢复调用、顺序、分支、循环、递归和关键数据关系。
- Deliverables：Full Code Graph、独立 CallSite、CALLS、CONTROL_FLOW、BRANCH_TRUE/FALSE、LOOP_BACK、READS/WRITES、关键 DataNode 与静态 DATA_FLOW、确定调用环。
- Files likely affected：`src/codeviz/analysis/graph_build.py`、`flow.py`、`control_flow.py`、`data_flow.py`、`call_cycles.py`，`src/codeviz/core/identity.py`、`analysis/project.py`、`symbols.py`，`tests/test_flow.py` 与相关文档。
- Dependencies：Phase 1–3、Python 3.11+ 标准库；无新增运行时依赖。
- Tests：同一行多个调用、动态 / 模糊 / 外部 / 多态派发调用、分支步骤与调用位置归属、nested if/loop/with/try、continue、return、定义时调用、recursion / call cycle / 长链、关键变量与 `self` 属性读写、普通 tmp 隐藏及来源传递 / 覆盖、导入别名不误标数据、单文件 flow 失败隔离。
- Acceptance Criteria：可从 `ProjectAnalysis.flow.graph` 构造 Full Graph，节点与边有源码锚点；关系类型可区分；参数 / 变量调用不伪造 Exact；Data Flow 不等于 Gradient Flow；单文件阶段失败保留其他结果。
- Out of Scope：完整 CFG、运行时调用目标、tensor shape propagation、真实 gradient flow、PyTorch 语义、Visible Graph。

## Phase 5 — Generic PyTorch Model Recognition

- Goal：识别 Module、子模块、容器和常见层，恢复自定义模块结构。
- Deliverables：模型实体、Definition / Instance / Invocation、ModuleList ×N、Shared 标记。
- Files likely affected：`src/codeviz/semantic/rules.py`、`semantic/pytorch.py`、`analysis/model.py`、`analysis/project.py`、`tests/test_model.py`。
- Dependencies：Phase 1–4、Python 3.11+ 标准库；`ModelRule` 插件协议由本阶段建立，无新增运行时依赖。
- Tests：torch 导入别名 / 遮蔽、常见层、自定义 Module / 子类、Sequential、ModuleList / Dict、同构折叠与动态次数、共享实例及链式赋值、forward CallSite、未知模块、稳定 ID、坏文件。
- Acceptance Criteria：模型组件均有 evidence 与源码锚点；重复结构与共享权重不混淆。
- Out of Scope：执行 forward、真实 shape、分布式框架。

## Phase 6 — Training Rules + Semantic Component Graph

- Goal：生成训练执行语义和可读组件抽象。
- Deliverables：Training Mode、Epoch / Batch、Forward、Loss、Backward、Optimizer、Scheduler、Gradient Flow。
- Files likely affected：`src/codeviz/semantic/training.py`、`components.py`、`src/codeviz/analysis/training.py`、`project.py`、`src/codeviz/core/model.py`、`tests/test_training.py`、`test_training_rules.py`、`test_semantic_graph.py`、`test_semantic_components.py`。
- Dependencies：Phase 4–5。
- Tests：训练 / 评估 / 推理多入口、no_grad、validation、checkpoint、uncertain attention-like。
- Acceptance Criteria：Exact / Probable / Unresolved 正确传播；Exact 有可检查的接收者与调用证据；提供规则依据；多入口独立 flow。
- Out of Scope：复杂 distributed、完整 autograd、无限层级。

## Phase 7 — View Builder + Architecture / Execution Visible Graph

- Goal：从 Full Graph 生成受控局部 Visible Graph。
- Deliverables：Graph Query、聚合 / 折叠、1-hop 上下文、Architecture 层级布局、Execution 流程布局、Legend。
- Files likely affected：`core/model.py`、`views/query.py`、`views/data.py`、`views/builder.py`、`views/layout.py`、`views/state.py`、`tests/test_views.py`。
- Dependencies：Phase 1–6。
- Tests：节点阈值、聚合、视图关系过滤、展开 / 收起、布局稳定性。
- Acceptance Criteria：默认不超过 100 节点 / 300 边；超过阈值不直接铺满；用户位置可序列化并在下一次构图时恢复。磁盘保存由 Phase 10 接入。
- Out of Scope：最终 UI 皮肤、实时增量更新。

## Phase 8 — VSCode-style UI + Tabs/Split + Inspector

- Goal：打通 Overview、视图、源码和 Inspector 的阅读体验。
- Deliverables：本地 Tkinter Activity Bar、Sidebar、Project Overview、Architecture / Training Flow Canvas、Editor Tabs / Split、Inspector、独立 Problems、只读 Source viewer、显式后台 Analyze。
- Files likely affected：`src/codeviz/ui/presenter.py`、`controller.py`、`graph_canvas.py`、`theme.py`、`tkapp.py`、`pyproject.toml`、`tests/test_ui_*.py`、相应文档。
- Dependencies：Phase 7 的 VisibleGraph / ViewState；Python 3.11+ 标准库 Tkinter / ttk（见 ADR-006），不新增运行时依赖。
- Tests：Overview / Inspector 事实映射、项目切换与源码路径限制、图场景和关系线型、惰性导入、Tk 可用时的模型直达 / Problems / Split / Inspector / 源码定位 / 控件释放；全量 unittest、Ruff、mypy、离线 wheel，Windows 桌面烟测。
- Acceptance Criteria：Overview 可选择模型或入口，架构和训练局部图可下钻；单击节点高亮一跳并在 Inspector 显示证据与源码，Open Source / Open to Side 独立；Problems 可查看；Analyze 不执行用户代码，分析中不能切换项目；节点仅显示易读名与真实名。
- Out of Scope：真实搜索与符号定位（Phase 9）、磁盘缓存 / Recent Projects / Outdated（Phase 10）、源码编辑 / 重构。

## Phase 9 — Search / Reveal in Graph / Source Navigation

- Goal：闭环 Search → Graph → Inspector → Source。
- Deliverables：Symbol Search、Semantic Search、scope disambiguation、local context、Reveal in Graph。
- Files likely affected：`src/codeviz/search.py`、`navigation.py`、`views/code_context.py`、`ui/tkapp.py`、`ui/controller.py`、`ui/graph_canvas.py`；执行细节见 `PHASE9_PLAN.md`。
- Dependencies：Phase 3、6–8。
- Tests：同名 symbol、中文 / 英文语义词、不同视图定位、1-hop、Open to Side。
- Acceptance Criteria：每条结果都能定位或明确标记无法定位的原因。
- Out of Scope：复杂语义编辑器、模糊 AI 命名。

## Phase 10 — Cache / Health / Problems / Layout Persistence

- Goal：提供可恢复、可解释的本地持久化。
- Deliverables：白名单 JSON Analysis Cache、独立 View State / Annotations / Recent Projects；内容指纹、分析 / parser / rule version、Outdated 与 Re-analyze；复用 Phase 8 的 Health / Problems。
- Files likely affected：`src/codeviz/persistence/{codec,fingerprint,store}.py`、`source_handles.py`、`analysis/python.py`、`views/state.py`、`ui/{controller,presenter,tkapp}.py`、`tests/test_persistence_*.py`、`tests/test_ui_tkapp.py`；细节见 `PHASE10_PLAN.md` 和 ADR-007。
- Dependencies：Phase 1–9；Python 标准库 JSON / hashlib / tempfile，无新运行时依赖或 GUI 迁移。
- Tests：往返后搜索与视图等价；AST / 源码不入缓存；改 / 增 / 删源文件、Ignore Paths 和版本变化；损坏与原子写失败；标注独立；旧图带过期警告；重开恢复图 / 源码标签与选中节点；Ruff、Windows / Linux mypy、unittest、wheel。
- Acceptance Criteria：不保存完整源码副本、不写入被分析仓库；旧结果可查但 Overview / Graph / Inspector / Source 显示 Outdated；缓存失败不抹掉用户数据。
- Out of Scope：增量 watcher、远程缓存、Settings 通用配置面板、复杂标注编辑器、尚无 UI 操作的 zoom / pan / filters。

## Phase 11 — MVP Hardening + Fixtures + Acceptance Tests

- Goal：以用户黄金路径完成发布前验证。
- Deliverables：稳定 fixture、回归测试、性能基线、验收报告、已知限制。
- Files likely affected：`fixtures`、`tests`、`docs/KNOWN_LIMITATIONS.md`。
- Dependencies：Phase 1–10。
- Tests：lint、typecheck、unit、integration、UI / acceptance、build（以真实仓库脚本为准）。
- Acceptance Criteria：三条黄金路径通过；原有失败与新增失败分开记录；不执行用户代码。
- Out of Scope：后置能力。

## React 桌面迁移 — Task 5 打包与 CI

- 主入口 `codeviz` → `codeviz.ui.desktop:main`，旧入口 `codeviz-tk` → `codeviz.ui.tkapp:main`；Tk 实现保留至迁移验收完成。
- `[desktop]` 固定 pywebview 6.2.1，`[packaging]` 固定 PyInstaller 6.22.3 / setuptools 84.0.0；纯分析核心不引入 Linux GUI 系统库。
- `scripts/build-desktop.ps1` 探测或接受指定 Python，完整构建先 npm ci / build，再输出 `dist/CodeViz/CodeViz.exe`；CheckOnly 必须检查现有前端依赖与产物。
- `scripts/desktop_entry.py` 的 setuptools build_py 命令将 `frontend/dist` 复制到 wheel 的 `codeviz/ui/assets`；`scripts/codeviz.spec` 将资源放到冻结目录 `frontend/dist`。启动器须覆盖源码、wheel assets 与 `_MEIPASS` 三种资源路径。
- CI 执行前端 lint / typecheck / test / build，Python core lint / mypy / unit / wheel 和 Windows 无 GUI 打包；WebView2 原生窗口、离线使用与三条黄金路径仍由 Task 6 单独验收。

## 阶段门禁

React 迁移 Tasks 1–6 已完成当前 Windows 技术交付与独立复审，2026-10-03 最终构建 / 测试 / 原生三入口结果见 `DESKTOP_MIGRATION_VALIDATION.md`。原 Tk 阶段记录保留为历史基线；当前 UX 差距、跨平台桌面与人工理解度验证明确列为后续，不随此次交付隐式宣称完成。

每阶段结束必须记录 changed files、实现摘要、适用的 lint / typecheck / test / build 结果、原有失败与新增失败、下一阶段。文档与实现冲突时同一阶段内修正文档或实现，并在 ADR / changelog 留痕。
