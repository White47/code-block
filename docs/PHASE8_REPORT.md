# Phase 8 实施报告

日期：2026-09-18。范围：本地只读桌面 UI；Phase 9 Search / Symbol Reveal 与 Phase 10 缓存不在本阶段。

## 实现摘要

- 新增 `src/codeviz/ui/`：Presenter 将已有分析事实映射成 Overview、Inspector、Problems；Controller 管理显式 Analyze、ViewState 与项目内只读源码访问；GraphCanvas 只绘制 Phase 7 的有限 VisibleGraph；Tk 壳提供 Activity Bar、Sidebar、Overview、Architecture / Training Flow、Editor Tabs / Split、Inspector、独立 Problems 与状态栏。
- Overview 的模型和入口可直接打开相应视图；图节点单击选择并高亮邻居，双击 / Enter 下钻；Open Source 与 Open to Side 单独操作。聚合节点没有虚假的源码按钮。
- Analyze 在后台线程运行，主线程接收状态；分析中禁止切换项目。Partial 结果保留可用事实并列出 Analysis Issues；失败不遗留旧分析。
- `pyproject.toml` 增加 `codeviz` 桌面启动入口；没有新增运行时依赖。Tkinter 的选型与边界见 ADR-006。

## 变更文件

- 产品代码：`src/codeviz/ui/__init__.py`、`presenter.py`、`controller.py`、`graph_canvas.py`、`theme.py`、`tkapp.py`、`pyproject.toml`。
- 测试：`tests/test_ui_presenter.py`、`test_ui_controller.py`、`test_ui_graph.py`、`test_ui_tkapp.py`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`IMPLEMENTATION_PLAN.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md`、`KNOWN_LIMITATIONS.md`、`PHASE8_DESIGN.md`、`PHASE8_PLAN.md`、`PHASE8_REPORT.md`、`docs/adr/006-tkinter-first-desktop-ui.md`。

## 验证

- 全量 unittest：155 项运行，0 失败、2 项跳过（合入前仍会复验）。Windows 上 Tk 实例测试运行；符号链接创建权限不足导致相关测试跳过。
- Ruff `src tests`、mypy 严格模式：通过。
- 离线 `pip wheel --no-build-isolation` 因当前虚拟环境未安装构建依赖 `setuptools` 而失败；随后按 GitHub 工作流采用隔离构建，wheel 成功，并确认包含 `codeviz` 启动入口及 6 个 UI 文件。这是本机环境差异，不是新增产品依赖。
- Windows Tk 图形烟测：训练局部图可实际绘制，窗口可映射；图节点→Inspector→侧边源码和 Problems 标签页另有自动化测试。

## 留待后续

- Phase 9：真实 Symbol / Semantic Search、局部上下文与 Reveal in Graph；当前 Search / Symbols 入口明确写明占位。
- Phase 10：磁盘 Analysis Cache、Recent Projects、Outdated 检测、ViewState / Annotations 持久化；本阶段位置仅会话内保留。
- Phase 11：更丰富的 fixture、跨平台视觉验收与大项目性能基线。

## 审查状态

待独立只读代码审查与最终复验后填写结论。
