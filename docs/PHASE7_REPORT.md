# Phase 7 实施报告

日期：2026-09-18

## Goal 与实现

在 Phase 6 的代码图与语义组件图之上，生成受控的 Architecture / Execution 局部可见图。`src/codeviz/views/query.py` 对语义图建邻接索引；`data.py` 按模型 `forward` 或训练入口的精确符号 ID 投影关键 DataNode 和已有 DATA_FLOW；`builder.py` 负责根选择、展开、选中邻域、关系白名单和输出预算；`layout.py` 产生确定性层级 / 流程位置；`state.py` 独立序列化展开状态与手动位置。

默认不超过 100 节点 / 300 边，超限返回真实成员 ID 的聚合节点、隐藏数量和可继续下钻标志。选中节点与请求的一跳邻居先于无关同级组件占用预算；仅有一个节点名额时保留选中项。Execution 优先保留控制主流程，再保留关键数据流与结构关系。可见边与普通节点带真实源码锚点或原始组件 / 代码节点 ID；不会把静态 DATA_FLOW 当成运行时张量观察。多模型、多入口保持独立。

## Changed files

- 实现：`src/codeviz/core/model.py`、`core/__init__.py`、`views/__init__.py`、`views/query.py`、`views/data.py`、`views/builder.py`、`views/layout.py`、`views/state.py`。
- 测试：`tests/test_views.py`。
- 文档：`README.md`、`docs/PHASE7_PLAN.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`IMPLEMENTATION_PLAN.md`、`KNOWN_LIMITATIONS.md`、`PERFORMANCE.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md` 与本报告。

## 验证与审查

- Phase 6 基线 118 项测试（1 项 Windows 符号链接环境性跳过）；本阶段新增 17 项，当前 135 项测试、134 通过、1 项同样跳过。
- Ruff `src tests`、mypy 严格模式（30 个源码文件）、全量 unittest、离线 wheel 构建和 `git diff --check` 均通过；无已知原有或新增失败。Windows Git 的 LF→CRLF 提示不影响检查结果。
- 独立审查发现的三项 Important 已先写失败测试再修复：代码级 DATA_FLOW 未进入视图、单节点预算丢选中项、紧边预算挤掉执行主控制边。复审发现的一跳数据邻居优先级和局部关系边预算问题也均红绿修复；最终独立只读复审未发现 Critical / Important 阻碍。独立审查环境无可用 Python，测试与构建由主工作区运行。

## 边界与下一阶段

当前仅提供 Python API，没有 VSCode 风格 UI；Calls / Data Flow / Gradient / Symbol 独立视图、磁盘持久化、分析取消与 Partial 查询状态尚未实现。关键数据流只投影原有代码边，不推断组件间张量流。Phase 8 将接入可交互的 Overview、Architecture / Execution 图、Tabs / Split 与 Inspector；图形库与桌面壳须在该阶段基于本仓库实情最小选择。
