# Phase 8 Desktop UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有静态分析与局部可见图之上交付只读、可交互的本地桌面 UI，打通 Overview→Architecture / Execution→Source。

**Architecture:** `ui/presenter.py` 将分析事实变成纯数据的 Overview / Inspector / Problems；`ui/controller.py` 管理已打开目录、一次性 Analyze、视图与安全源码读取；`ui/graph_canvas.py` 绘制不超过 View Builder 预算的图；`ui/tkapp.py` 只承载 Tk 事件、Tabs / Split 与后台分析通知。设计边界见 `PHASE8_DESIGN.md`。

**Tech Stack:** Python 3.11+ 标准库 Tkinter / ttk、现有 unittest / Ruff / mypy / setuptools，无新增运行时依赖。无显示器时 Tk 实例测试跳过；其余测试无需创建窗口。

---

## 文件职责

- `src/codeviz/ui/presenter.py`：不可变的 `OverviewData`、`InspectorData`、`ProblemData` 和数据转换函数；不读取文件。
- `src/codeviz/ui/controller.py`：`AppController`，管理项目 / 分析 / 视图 / 选中 / 展开 / 源码跳转，读取源码前做路径与大小检查；不依赖 Tk。
- `src/codeviz/ui/graph_canvas.py`：`GraphCanvas` 映射 VisibleNode / VisibleEdge 到 Canvas 图形、图例、点击 / 双击 / 拖动事件；不解析源码。
- `src/codeviz/ui/theme.py`：颜色、字体与 ttk 样式参数。
- `src/codeviz/ui/tkapp.py`：窗口、Activity Bar、Sidebar、双 Notebook、Inspector、状态栏与后台 Analyze；只调用 Controller。
- `tests/test_ui_presenter.py`、`tests/test_ui_controller.py`、`tests/test_ui_graph.py`：无显示器可运行的事实映射和安全测试。
- `pyproject.toml`：增加 `codeviz` 启动脚本，不新增 dependencies。

## Task 1 — Overview / Inspector / Problems 纯数据

**Files:** `src/codeviz/ui/presenter.py`、`src/codeviz/ui/__init__.py`、`tests/test_ui_presenter.py`。

- [ ] 写失败测试并运行：两个模型 / 多个入口独立列出，健康度中的 parsed / exact / probable / unresolved 来自真实分析；选中节点的真实名、置信度、证据、关系和锚点进入 Inspector；合成聚合没有伪造 Open Source。

```python
overview = build_overview(analysis, project_root)
assert {item.id for item in overview.models} == {
    definition.symbol_id for definition in analysis.model.definitions
}
inspector = build_inspector(view, selected_id, analysis)
assert inspector.source_anchors == selected_node.source_anchors
```

- [ ] 将 `OverviewData` 与 `InspectorData` 定为 frozen dataclass；先从 ProjectAnalysis / VisibleGraph 读取现有字段，不重新解析 AST，不从名称补造确定语义。
- [ ] 重跑 `python -B -m unittest tests.test_ui_presenter -q` 与现有全量测试；每项失败先定位原因。

## Task 2 — 控制器与安全源码导航

**Files:** `src/codeviz/ui/controller.py`、`tests/test_ui_controller.py`。

- [ ] 写失败测试并运行：`open_project()` 只保存目录、不立即解析；`analyze()` 后可查询 Architecture / Execution；展开节点不修改 `ProjectAnalysis`；源码锚点准确返回行号；绝对 / `..` 路径由 SourceAnchor 拒绝，符号链接越界由控制器拒绝；打开新项目清除旧结果。

```python
controller = AppController()
controller.open_project(project_root)
assert controller.analysis is None
controller.analyze()
assert controller.view(ViewKind.EXECUTION).view_kind == ViewKind.EXECUTION
```

- [ ] `read_source(anchor)` 先 `resolve(strict=True)` 并确认目标仍位于解析后的项目根内，再限额读取 UTF-8 文本；不执行、不导入、不写源码。`open_project` 接受已存在目录，清空旧 view state；`analyze` 显式调用现有 `analyze_project`。
- [ ] 重跑新增、全量 unittest、Ruff 和 mypy。

## Task 3 — 图形映射与交互

**Files:** `src/codeviz/ui/graph_canvas.py`、`src/codeviz/ui/theme.py`、`tests/test_ui_graph.py`。

- [ ] 写失败测试并运行：图形映射使用 `VisibleGraph.layout_hints`，不同关系线型不同，聚合节点展示隐藏数量，节点标签只含易读名 / 真实名；选择只高亮一跳，双击产生 drill-down 请求，拖动更新会话 `ViewState.positions`。

```python
scene = graph_scene(visible_graph)
assert len(scene.nodes) == len(visible_graph.nodes)
assert {edge.style for edge in scene.edges} == {
    item.style for item in visible_graph.legend
}
```

- [ ] 先实现纯数据 `graph_scene()`；`GraphCanvas` 再把该数据绘制为带滚动区域的 Tk Canvas。只绘制 View Builder 返回的局部图，不遍历 Full Graph。边型采用 `PHASE8_DESIGN.md` 颜色与线型，键盘 Enter / 鼠标双击共用 drill-down 动作。
- [ ] 重跑新增、全量 unittest、Ruff 和 mypy；无显示器 CI 不创建 Tk 根窗口。

## Task 4 — VSCode 风格桌面壳与后台分析

**Files:** `src/codeviz/ui/tkapp.py`、`src/codeviz/ui/theme.py`、`pyproject.toml`、`tests/test_ui_controller.py`。

- [ ] 写失败测试并运行：Tk 应用模块可导入而不创建窗口；后台分析消息可表达 Analyzing / Ready / Partial / Failed；重复 Analyze 不并发启动；Source / Open to Side 动作与 drill-down 分离。

```python
from codeviz.ui.tkapp import main
assert callable(main)
```

- [ ] 绘制 Activity Bar（Explorer、Search、Architecture、Execution、Symbols、Settings）、Sidebar、Overview Tab、Graph Tab、Source Tab、右侧 Split Notebook、Inspector、Problems 和状态栏；Search / Symbols 完整功能明确标 Phase 9。按 `filedialog.askdirectory` 选择本地项目，点击 Analyze 才启动后台线程；工作线程只写 `queue.Queue`，主线程以 `after` 轮询并更新 Tk。
- [ ] Graph Canvas 单击 Select + Inspector + 邻居高亮，双击 / Enter 展开；Open Source 与 Open to Side 仅通过安全导航接口。Source Text 只读，定位到 anchor 行；Problems 展示 issue，Partial 不丢可用结果。
- [ ] 运行全量 unittest、Ruff、mypy、离线 wheel；在当前 Windows 桌面做可见烟测，确认 Overview、Architecture、Execution、Split、Inspector、源码定位。

## Task 5 — 文档与独立审查

**Files:** `README.md`、`docs/ARCHITECTURE.md`、`PROJECT_STRUCTURE.md`、`KNOWN_LIMITATIONS.md`、`TEST_STRATEGY.md`、`IMPLEMENTATION_PLAN.md`、`PHASE8_REPORT.md`。

- [ ] 更新真实能力与 Phase 9 / 10 边界，不把占位 Search / Symbols 说成可用功能。
- [ ] 独立只读审查 Phase 8 实现，任何 Important 先用失败测试复现再修复；复审通过。
- [ ] 记录 changed files、验证结果和下一阶段，仅在完整验收后合入 / 推送 `main`。

## Out of Scope

真实搜索与 Symbol Reveal（Phase 9）、磁盘缓存 / Recent Projects 持久化（Phase 10）、编辑源码、运行用户代码、远程仓库打开、完整 tensor / autograd 观察。
