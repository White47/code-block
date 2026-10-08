# 通用 Python 分析实施计划

> **For agentic workers:** 使用 subagent-driven-development 分任务执行；测试先行，每阶段进行需求与代码质量独立审查。用户已授权实施至验收，非调研待确认稿。

**Goal:** 让普通脚本、函数库、面向对象和异步代码能被保守分析、逐层浏览并准确追溯源码，同时保留 ML 功能。

**Architecture:** 复用标准库 AST、独立 Resolver、Code Graph 和原生桌面桥接。FunctionFlow 与训练 Execution 分开；隐式词法作用域作为非定义节点建模。静态事实不等于运行轨迹，所有不完整路径保留诊断。

**Tech Stack:** Python 3.11+、unittest / Ruff / mypy、React / TypeScript / React Flow / Vitest、pywebview；首批不新增依赖。

基线：9c09fd9；开发分支 codex/general-python-analysis。原用户未跟踪文件不覆盖、不提交。后端基线 376 测试（49 skip），前端 41 测试；静态检查通过。

## Task 1 — 通用入口、函数流程与桌面可达性

文件：新增 analysis/entries.py、views/function_flow.py；修改 analysis/project.py、analysis/training.py、core/model.py、ui/presenter.py、controller.py、web_contract.py、web_bridge.py、persistence/codec.py、fingerprint.py、store.py；前端 types.ts、Overview.tsx、i18n.ts、视图操作与测试。

- [x] 自建 main guard、__main__.py、无入口函数库、export_csv、嵌套函数和缓存往返夹具，先观察断言失败。
- [x] 新增 GenericEntryCandidate（root_symbol_id、识别类别、confidence、evidence、SourceAnchor）；ProjectAnalysis.generic_entries。仅 main 名称为 Probable；人工选择函数不是自动入口。
- [x] 新增 ViewKind.FUNCTION_FLOW='FunctionFlow'，从已有 CFG 投影当前作用域，不内联嵌套定义或被调函数；预算、布局、Inspector / Source、详情与恢复可用。
- [x] Overview 暴露模块、类、具名函数 / 方法；非 ML 项目可选函数流程。训练名称规则需要框架证据，不把 export_csv 误标训练 Export。
- [x] 在已知 finally / match / async / generator 简化处报告 issue / Partial。分析和语义版本升级，旧缓存要求重新分析但 View State / Annotation 保留；缓存无 AST 仍可展示。
- [x] 后端、前端新增回归通过，原 ML 路径通过，独立需求审查后进行质量审查。

测试通过真实 analyze_project，不执行被分析代码。验收断言示例：

```python
overview = build_overview(analysis, root)
assert overview.functions
graph = build_visible_graph(analysis, ViewRequest(ViewKind.FUNCTION_FLOW, function_id))
assert graph.nodes
assert all(node.source_anchors for node in graph.nodes if not node.member_ids)
```

## Task 2 — 词法作用域与事实一致性

文件：core/identity.py、analysis/symbols.py、resolution.py、flow.py、data_flow.py，以及共享作用域定位模块（如需）；tests/test_general_python_bindings.py。

- [x] lambda 参数 / 默认值、推导式首 iterable / 嵌套 / class 遮蔽、match 捕获 / OR / mapping / star、自建 walrus 与调用归属回归先失败。
- [x] NodeKind.LEXICAL_SCOPE='LexicalScope'、definition=False，稳定结构 ID。lambda 默认值外围、body 独立；推导式首 iterable 外围，其余局部。
- [x] match capture 在当前作用域，不创建 case scope；忽略通配符。walrus 支持正确外围绑定或明确诊断，不输出错误 Exact。
- [x] Resolver / Flow / Data 使用一致 scope；lambda body 不归为外围即时执行，class namespace 不错误捕获。Exact 表示定义归属，不是值已绑定或调用必然执行。
- [x] Task2 阶段的全量回归、需求审查、质量审查；记录语言支持矩阵。此检查点不替代 Task5 最终代码快照的整合门禁。

绑定测试示例：

```python
references = analysis.symbol_index.find_references('row')
assert references
assert all(ref.resolved_definition_id is not None for ref in references)
```

## Task 3 — 对象与模块关系（独立增量）

主要文件：analysis/relations.py、value_candidates.py、resolution.py、flow.py；tests/test_relations.py、test_value_candidates.py、test_general_python_objects.py、test_analysis_work_budgets.py 及原解析 / 调用回归。

- [x] 导入别名、继承、构造实例调用、self / cls / super、函数别名与条件重绑定的失败测试先行。
- [x] 有证据的 IMPORTS / INHERITS；有限对象 / callable 候选传播，动态分派不假定唯一 Exact；循环与累计工作预算终止。继承 / 重导出及候选语句 / 表达式实际访问预算已复审，耗尽保留已知候选与 unknown / truncated / 源码 Partial。
- [x] 独立验证错误 Exact、同名消歧、跨文件源码锚点；不加载第三方运行时。需求 / 质量复审批准，包含推导式重绑定与权威 nonlocal owner。

## Task 4 — 控制流与普通数据关系（独立增量）

主要文件：analysis/control_flow.py、def_use.py、data_flow.py、views/code_context.py、navigation.py；tests/test_control_exit_semantics.py、test_generic_def_use.py、test_general_python_control.py、test_general_python_data.py 与通用 fixture。

- [x] match、try / finally 的 return / raise / break / continue、loop else、普通参数 / 赋值 / 返回值及条件来源集合的失败测试先行。
- [x] 保守恢复控制结构，区分退出类型；无法恢复的异常 / 并发 / generator 时序明确 Partial。
- [x] 通用有界 def-use 按需展示，不依赖 ML 名称；分支保留可能来源与 unknown，不声称运行时值等价。首批只做过程内分析；项目 callee 返回值被使用时给源码锚定的 Partial，未经证明不连实参→形参 / return→结果边。实际跨函数传播作为后续独立预算化增量，不暗称本轮已支持。
- [x] 原训练控制图、递归 / 调用环和大图预算回归；两阶段复审。finally 值来源保守合并为 unknown / Partial，不声称精确退出路径数据分析。

## Task 5 — 真实仓库交叉验证与交付

- [x] 固定普通 CLI、HTTP / Web、面向对象、async 与 ML 仓库提交、许可和分析范围；只读扫描，不安装或运行目标代码。
- [x] 抽样人工核对入口、定义 / 调用候选、错误 Exact、控制结构和 SourceAnchor；统计解析 / 未解析 / issue 和性能，不承诺无依据覆盖百分比。
- [x] 文档同步支持矩阵、限制、缓存升级和验收证据。可选 Jedi / LibCST 只有实测证明收益才接入，本计划不要求换引擎。
- [x] 全量检查与构建；实际窗口黄金路径验证；独立最终审查。2026-10-05 整体批准：后端 611 项（49 跳过）、前端 43 项零失败，固定语料与最终 exe / wheel 原生验证通过。批准限于支持矩阵，跨函数值传播 / 完整动态运行语义等后置项未隐式完成。

## 每阶段验证命令

```powershell
.desktop-venv/Scripts/python.exe -m ruff check src tests
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
.desktop-venv/Scripts/python.exe -m pip wheel . --no-deps -w dist
git diff --check
```

命令必须分别确认退出码；受影响 Windows 打包与真实窗口测试使用仓库已有脚本。提交 / 推送不作为实现前提，本轮未经另行要求不自动上传。
