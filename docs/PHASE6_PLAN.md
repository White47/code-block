# Phase 6 — Training Rules and Semantic Component Graph

> 本计划细化已批准的 `IMPLEMENTATION_PLAN.md` Phase 6；只实现静态事实，不执行用户项目。每项先写失败测试，确认失败，再实现和复测。

状态：实现与本地质量门禁完成；独立只读审查因额度限制待补，尚未推送。验证与已知边界见 `PHASE6_REPORT.md`。

**Goal:** 从现有 Flow / Model 事实构建多入口训练语义与可追溯组件图。

**Architecture:** `semantic/training.py` 只定义 PyTorch 训练语义匹配规则；`analysis/training.py` 负责 AST、Resolver 与 CallSite 证据，产生入口和事件；`semantic/components.py` 将模型事实和训练事件组合为 Semantic Component Graph。Python Parser 与 UI 不承载框架规则。

**Tech Stack:** Python 3.11+ 标准库、现有 `unittest` / Ruff / mypy；不新增运行时依赖。

## Task 1 — 语义图契约

**Files:** `src/codeviz/core/model.py`、`src/codeviz/core/__init__.py`、`tests/test_semantic_graph.py`。

- [x] 先测 `SemanticRelation` 和 `SemanticGraph`：ID 唯一、关系端点存在、每条语义关系带 evidence 和 SourceAnchor。
- [x] 将关系类型限定到现有 `EdgeType`；梯度关系必须保留 `confidence=Probable` 或 `Unresolved`，不得宣称真实 autograd 图。
- [x] 运行新增测试与核心测试，并检查 mypy。

## Task 2 — 训练规则与接收者证据

**Files:** `src/codeviz/semantic/training.py`、`tests/test_training_rules.py`。

- [x] 先测显式 torch import、别名、项目内同名 torch、文件 / 函数局部遮蔽；`model.train/eval` 与 `torch.no_grad` 不混为一谈。
- [x] 规则输出角色、置信度、证据；只有能证实模型 / optimizer / scheduler / loss 来源时才允许 Exact。名称线索只能 Probable，任意 `.step()` / `.backward()` 不伪造确定训练角色。
- [x] 覆盖常见损失构造器、`torch.optim`、`lr_scheduler`、`torch.save/load`；不导入 PyTorch。

## Task 3 — 多入口与执行事件

**Files:** `src/codeviz/analysis/training.py`、`src/codeviz/analysis/project.py`、`tests/test_training.py`。

- [x] 先测独立 Train / Evaluate / Inference / Export 入口，同一模型被多入口引用但定义不复制。
- [x] 事件保留稳定 ID、SourceAnchor、CallSite / ControlBlock 来源、confidence 与 evidence；识别 epoch / batch 循环、mode、clear gradients、forward、loss、backward、optimizer / scheduler、no_grad、简化 checkpoint。
- [x] 以 Resolver 与模型定义验证局部构造来源；动态重绑定、参数接收者和未知方法调用保持 Probable / Unresolved。
- [x] 单文件规则异常产生 issue，保留其余文件与入口；不执行被分析代码。

## Task 4 — 组件图与关系

**Files:** `src/codeviz/semantic/components.py`、`tests/test_semantic_components.py`、`src/codeviz/analysis/project.py`。

- [x] 先测模型、实例、训练事件可追溯到代码符号或源码锚点，Model Definition 与 Instance 不混同。
- [x] 建立 `CONTAINS`、静态 `CONTROL_FLOW`、循环关系与保守 `GRADIENT_FLOW`；条件路径不画成无条件运行事实。
- [x] Attention-like 仅在 Q/K/V 与 score/softmax 等结构证据同时出现时给 Probable 候选，保留真实名称；未知模型保持通用结构。

## Task 5 — 阶段门禁与文档

**Files:** `README.md`、`docs/ARCHITECTURE.md`、`DATA_MODEL.md`、`SEMANTIC_RULES.md`、`KNOWN_LIMITATIONS.md`、`TEST_STRATEGY.md`、`IMPLEMENTATION_PLAN.md`、`PHASE6_REPORT.md`。

- [x] 逐项核对 Phase 6 的 Goal / Deliverables / Tests / Acceptance Criteria；明确静态推断与真实梯度观察的边界。
- [x] 运行 `python -m ruff check src tests`、`python -m mypy`、`python -B -m unittest discover -s tests -q`、离线 wheel 构建；区分基线失败与新增失败。
- [ ] 独立只读审查因额度限制未取得反馈，待额度重置后补做；本轮已自行复查并修复误报、分支合流、遮蔽和重复 ID。审查完成后再决定推送及进入 Phase 7。

## Out of Scope

完整 autograd graph、真实 tensor shape、分布式训练、动态跟踪、运行用户源码、通用无限层级与 UI。
