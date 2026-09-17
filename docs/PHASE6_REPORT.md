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

- Phase 5 基线：86 项测试，其中 1 项 Windows 符号链接环境性跳过。本阶段新增 18 项测试；最终 104 项测试，103 通过、1 项同样跳过。TDD 红绿覆盖循环归属、图构建、误报及同名重复 attention、checkpoint 置信度、try 分支合流、循环 / with 遮蔽及单文件规则异常隔离。
- Ruff `src tests` 通过；mypy 严格模式通过（24 个源码文件）；全量 unittest 通过；离线 wheel 构建通过；`git diff --check` 通过。原有失败：无；新增失败：最终未发现。LF→CRLF 提示是现有 Windows Git 配置提示，不影响检查结果。
- 独立只读代码审查已发起，但审查助手因使用额度限制未返回反馈；尚不能称其通过。自行复查已修复 attention 同名重复定义导致的图 ID 冲突，以及循环变量 / `with ... as` 遮蔽旧 optimizer 证据的问题。额度重置后须补做独立审查并修正重要问题，再推送与进入 Phase 7；无需用户使用重置卡。

## 限制与下一阶段

详见 `KNOWN_LIMITATIONS.md`。Phase 7 将在事实图和语义图之上构建受控局部 Visible Graph，做聚合、按需展开、关系过滤和布局提示；不把 Full Graph 全量渲染到 UI。本阶段当前只准备本地提交，独立审查和远端 CI 尚待完成。
