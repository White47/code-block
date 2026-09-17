# Phase 4 实施报告

日期：2026-09-17

## Goal 与范围

在 Phase 3 的静态符号解析之上构建 Full Code Graph：调用位置与目标、基础顺序 / 条件 / 循环关系、关键数据的静态读写与依赖。仍不执行被分析代码，不实现 PyTorch 语义、完整 CFG 或 Visible Graph。

## Changed files

- 实现：`src/codeviz/analysis/graph_build.py`、`flow.py`、`control_flow.py`、`data_flow.py`、`call_cycles.py`，以及 `src/codeviz/analysis/project.py`、`symbols.py`、`src/codeviz/core/identity.py`。
- 测试：`tests/test_flow.py`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`DATA_MODEL.md`、`IMPLEMENTATION_PLAN.md`、`KNOWN_LIMITATIONS.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md`、`ERROR_MODEL.md` 与本报告。

## 实现摘要

- `ProjectAnalysis.flow` 返回 `FlowAnalysis`：经验证的 `CodeGraph`、关键 `DataNode`、确定调用环与局部 issues。Full Graph 装入项目、文件、模块、定义和 Phase 3 的 External / Unresolved 节点；新 CallSite / FlowStep / ControlBlock / Data 节点均保留源码锚点。
- 每个调用位置独立成节点，并挂在所属流程步骤 / 控制块下；`CALLS` 使用 Resolver 证据。参数、变量、动态表达式及 `self` 多态派发不伪造唯一 Exact 目标。装饰器、默认参数和类基类表达式中的调用归属定义时所在作用域。递归 / 互调环通过非递归图算法汇总，避免长调用链耗尽 Python 调用栈。
- 基础控制边区分顺序、条件 True / False、循环回边；进入 `with` 与 `try` 的内部步骤，但异常 / finally 语义仍按 `KNOWN_LIMITATIONS.md` 标记为部分建模。return / raise 为当前顺序路径终点。
- 关键数据角色仅由有限名称规则给出 Probable；READS / WRITES 与 DATA_FLOW 基于源码赋值。普通临时变量不抬升为 DataNode，但直线赋值链可传递关键来源，覆盖赋值会清除旧来源；不生成真实 Gradient Flow。
- 文件级控制 / 数据 / 调用分析失败产生 issue，保留其他文件的图。无新增运行时依赖；分析仍只读且不执行项目代码。

## 验证与审查

- 进入阶段前 Git 工作树干净；Phase 3 基线：Ruff、mypy 通过，45 项测试中 1 项因本机无法创建测试符号链接而跳过。
- 按测试先行验证关键新增行为和修复；完整本地检查：Ruff 通过、mypy 严格模式通过（17 个源码文件）、unittest 63 项中 62 项通过 / 1 项环境性跳过、离线 wheel 构建通过。wheel 内确认包含五个新分析模块；`git diff --check` 与暂存检查在提交前再运行。
- 交付前只读代码审查指出多态调用误标 Exact、长链递归溢出，以及调用位置归属、`with` / `try` 内部步骤、定义时调用、`self` 属性数据、隐藏临时变量来源等缺口；均以回归测试驱动修正。
- 原有失败：无。新增失败：最终检查未发现。远端 GitHub Actions 需推送后另行确认，不在本报告中预称通过。

## 限制与下一阶段

具体静态推断限制见 `KNOWN_LIMITATIONS.md`。Phase 5 将在现有 Code Graph 上建立可插拔的 Generic PyTorch 模型规则，识别 `nn.Module`、子模块与容器，并区分 Definition / Instance / Invocation 与共享实例；本阶段不提前实现。
