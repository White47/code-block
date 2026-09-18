# Phase 6 实施报告

日期：2026-09-18

## Goal 与范围

基于 Phase 4–5 的静态 Flow / Model 事实，生成多入口训练事件和 Semantic Component Graph，保留源码、证据与置信度；不执行 PyTorch、用户项目或真实训练。可见图、图形 UI、缓存和真实 autograd 均留后续阶段。

## Changed files

- 实现：`src/codeviz/core/model.py`、`core/__init__.py`、`analysis/training.py`、`analysis/project.py`、`semantic/training.py`、`semantic/components.py`、`semantic/__init__.py`。
- 测试：`tests/test_semantic_graph.py`、`test_training_rules.py`、`test_training.py`、`test_semantic_components.py`。
- 文档：`README.md`、`docs/PHASE6_PLAN.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`SEMANTIC_RULES.md`、`TEST_STRATEGY.md`、`PROJECT_STRUCTURE.md`、`ERROR_MODEL.md`、`KNOWN_LIMITATIONS.md`、`IMPLEMENTATION_PLAN.md` 与本报告。

## 实现摘要

- `ProjectAnalysis.training` 按 Train / Evaluate / Inference / Export 名称生成独立 EntryPoint 与训练事件；同一模型定义可被多个入口引用。事件带稳定 ID、CallSite / ControlBlock 来源、循环父节点、源码锚点、confidence 和 evidence。
- PyTorch 训练规则独立于 Parser。显式 torch 导入和已证实接收者可产生 Exact；参数名及重绑定线索只给 Probable。`model.eval()` 与 `torch.no_grad()` 不混淆；仅 `with torch.no_grad()` 生成禁用梯度事件。`torch.save/load` 只生成 Probable checkpoint 候选。
- `semantic.graph` 将模型定义、子模块实例、训练入口 / 事件组成独立组件，验证所有关系端点和子组件引用。关系保留证据与锚点；循环回边、词法控制顺序和 Backward→Optimizer 梯度模式只给 Probable，不宣称实际运行路径或 autograd 图。
- Attention-like 仅在同一 forward 内 Q/K/V 命名赋值、Q/K 矩阵得分与该得分进入 softmax 的结构证据同时存在时，生成保留真实类名的 Probable 候选。未知结构不强行命名。
- 单文件训练规则错误产生 `TRAINING_ERROR`，语义候选规则错误产生 `SEMANTIC_RULE_ERROR`；其他文件和入口继续可用。无新增运行时依赖。

## 验证与审查

- Phase 5 基线：86 项测试，其中 1 项 Windows 符号链接环境性跳过。本阶段新增 32 项测试；当前 118 项测试，117 通过、1 项同样跳过。TDD 红绿覆盖循环归属、图构建、误报及同名重复 attention、checkpoint 置信度、try 分支合流、循环 / with / NamedExpr / del 遮蔽、语句与表达式互斥分支、简单、嵌套及连续独立分支汇合、循环体真实出口、80 个可跳过分支的关系数量、事件 ID 稳定、索引遍历次数及单文件规则异常隔离。
- Ruff `src tests` 通过；mypy 严格模式通过（24 个源码文件）；全量 unittest 通过；离线 wheel 构建通过；`git diff --check` 通过。原有失败：无；新增失败：最终未发现。LF→CRLF 提示是现有 Windows Git 配置提示，不影响检查结果。
- 独立只读审查本轮指出 4 项 Important：NamedExpr / del 旧 Exact 事实泄漏、互斥 `if/else` 假关系、前序事件导致 ID 变化、入口和语义图的平方级扫描。均已先复现再修正，另由领域契约禁止 Exact 梯度边。复审继续指出条件表达式假梯度边、分支汇合漏边、连续独立分支旧出口泄漏、连续可选分支平方级关系数及循环回边选错出口；现由 AST 遍历维护路径前沿、插入可追溯 ControlJoin 压缩，并记录循环体实际出口。最终独立静态复审未发现 Important 阻碍；完整测试由主任务运行通过。未使用用户重置卡。

## 限制与下一阶段

详见 `KNOWN_LIMITATIONS.md`。Phase 7 将在事实图和语义图之上构建受控局部 Visible Graph，做聚合、按需展开、关系过滤和布局提示；不把 Full Graph 全量渲染到 UI。远端 CI 以推送后的工作流结果为准。
