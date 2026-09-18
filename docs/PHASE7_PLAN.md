# Phase 7 Visible Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从现有 Code Graph 与 Semantic Graph 生成有预算、可展开、可回溯的局部 Architecture / Execution 图。

**Architecture:** `views/query.py` 只做事实索引和邻接查询；`views/builder.py` 选择当前视图、根、展开节点、关系并执行可见预算；`views/layout.py` 产出确定性的层级 / 流程布局提示。核心 `VisibleGraph` 是不可变查询结果，不修改分析事实。`ViewState` 只序列化用户位置与展开状态，磁盘缓存留 Phase 10。

**Tech Stack:** Python 3.11+ 标准库、现有 unittest / Ruff / mypy；不新增运行时依赖。

---

## 文件职责

- `src/codeviz/core/model.py`：补充 VisibleGraph 的聚合、布局、图例、隐藏数量与查询上下文字段及校验。
- `src/codeviz/views/query.py`：只读索引 `SemanticGraph`，查询子节点与特定关系的邻居，不扫描整图多次。
- `src/codeviz/views/data.py`：以入口函数或模型 `forward` 的符号 ID 为边界投影关键 Code DataNode 与已有 DATA_FLOW，不推断新运行时边。
- `src/codeviz/views/builder.py`：`build_visible_graph(analysis, request)`；区分 Architecture 与 Execution，控制节点 / 边预算，支持展开与 1-hop。
- `src/codeviz/views/layout.py`：确定性的布局提示和手动位置覆盖；不依赖 UI 图形库。
- `src/codeviz/views/state.py`：可序列化视图状态；Phase 10 再接入应用缓存目录。
- `tests/test_views.py`：端到端视图契约、预算、关系过滤、溯源和稳定性。

## Task 1 — 可见图契约与状态

**Files:** `src/codeviz/core/model.py`、`src/codeviz/core/__init__.py`、`src/codeviz/views/state.py`、`tests/test_views.py`。

- [x] 写失败测试：`VisibleGraph` 超过预算拒绝；聚合成员必须非空且能追溯；`ViewState` 的展开集合和 `positions` 经 JSON 数据往返后相等。
- [x] 运行 `.\.venv\Scripts\python.exe -B -m unittest tests.test_views -q`，确认因新字段 / 接口缺失而失败。
- [x] 添加 `VisibleAggregation(id, member_ids)`、`VisibleGraph.aggregations/layout_hints/legend/hidden_counts/query_context`；`ViewState` 用明确的 `to_dict/from_dict` 边界检查位置值。
- [x] 重跑新增及既有 `tests.test_graph`，并运行 Ruff/mypy。

## Task 2 — 只读 Graph Query 与视图过滤

**Files:** `src/codeviz/views/query.py`、`src/codeviz/views/data.py`、`src/codeviz/views/builder.py`、`src/codeviz/views/__init__.py`、`tests/test_views.py`。

- [x] 写失败测试：模型与多个入口分别选择；Architecture 显示结构 / 已知关键数据关系，Execution 显示控制 / 数据 / 回环，不把 `GRADIENT_FLOW` 混成默认主箭头；每个可见节点与边保留源码或语义组件引用。关键数据来自入口 / `forward` 符号作用域，不能凭名称跨入口拼接。
- [x] 运行新增测试确认失败。
- [x] 建立单次邻接索引；`ViewRequest(view_kind, root_id, expanded_ids, selected_id, hops, max_nodes, max_edges)` 验证参数。根视图只取相应类型根；执行视图以所选 EntryPoint 为根；组件展开只取直接孩子。关系使用明确白名单，未知视图抛清晰异常而不返回误导性空图。
- [x] 重跑新增测试、全量 unittest、Ruff/mypy。

## Task 3 — 预算、聚合与局部上下文

**Files:** `src/codeviz/views/builder.py`、`tests/test_views.py`。

- [x] 写失败测试：大于 100 的同级组件只产生预算内可见节点和一个可追溯聚合节点；点选后只增加默认 1-hop，展开后局部可见；节点 / 边均不超过请求阈值；两次相同请求结果相等。
- [x] 运行失败测试。
- [x] 按输入顺序与稳定 ID 限额选择，预留聚合位；聚合 `member_ids` 为被隐藏的真实 ID；将被截断的数量与可继续展开状态返回，而不是静默丢失。只查询相邻边，不把 Full Graph 交给 UI。
- [x] 重跑新增和全量测试、Ruff/mypy。

## Task 4 — 布局提示、图例与手动位置

**Files:** `src/codeviz/views/layout.py`、`src/codeviz/views/builder.py`、`src/codeviz/views/state.py`、`tests/test_views.py`。

- [x] 写失败测试：Architecture 层级、Execution 流程布局确定；不同关系在 legend 中有不同样式；手动位置覆盖默认位置且序列化后可恢复。
- [x] 运行失败测试。
- [x] 以稳定拓扑层和固定间距给 `layout_hints`，`LOOP_BACK` 不参与执行图拓扑层；对循环不无限展开。应用 `ViewState.positions` 时只覆盖当前可见节点。图例按实际可见关系生成。
- [x] 重跑新增及全量测试、Ruff/mypy、离线 wheel 构建、`git diff --check`。

## Task 5 — 文档与审查

**Files:** `docs/DATA_MODEL.md`、`ARCHITECTURE.md`、`PERFORMANCE.md`、`KNOWN_LIMITATIONS.md`、`IMPLEMENTATION_PLAN.md`、`PHASE7_REPORT.md`。

- [x] 对照已批准的 Phase 7 Goal / Deliverables / Tests / Acceptance Criteria 核实真实实现，记录 Phase 8 UI 与 Phase 10 持久化边界。
- [x] 独立只读审查完整差异，先复现再修复 Important 问题，复审通过。
- [x] 记录 changed files、验证结果与下一阶段；仅在完整验收通过后准备提交并推送，避免未完成阶段混入 `main`。

## Out of Scope

VSCode 风格 UI、磁盘分析缓存、项目源码执行、完整 runtime tensor / autograd 图、任意语言适配器、无限层级通用框架。
