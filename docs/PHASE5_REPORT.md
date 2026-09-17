# Phase 5 实施报告

日期：2026-09-17

## Goal 与范围

在 Phase 4 的静态代码图之上，建立独立于 Python Parser 的 Generic PyTorch 模型规则，识别模型定义、子模块实例、容器、共享别名和 `forward` 调用。结果仍是源码事实，不执行项目、PyTorch 或训练循环；训练语义图、Visible Graph 与 UI 留给后续阶段。

## Changed files

- 实现：`src/codeviz/semantic/__init__.py`、`rules.py`、`pytorch.py`；`src/codeviz/analysis/model.py`、`project.py`。
- 测试：`tests/test_model.py`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`DATA_MODEL.md`、`SEMANTIC_RULES.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md`、`ERROR_MODEL.md`、`KNOWN_LIMITATIONS.md`、`IMPLEMENTATION_PLAN.md` 与本报告。

## 实现摘要

- `ProjectAnalysis.model` 返回 `ModelAnalysis`，其 Definition 关联类符号 ID，Instance 具有稳定的结构 ID，Invocation 关联 Phase 4 的 CallSite ID；每项保留源码锚点、confidence 与证据。未知工厂若在 `forward` 中被调用，保留 Unresolved 候选而不冒充已知 PyTorch 层。
- `ModelRule` 协议隔离框架规则。PyTorch 规则只读取 AST 中可追溯的显式 `torch` / `torch.nn` 导入，匹配常见内建层、Module 基类、Sequential、ModuleList、ModuleDict；项目内同名 `torch` 路径与局部别名遮蔽均不当作真实框架证据。用户自定义 Module 通过 Resolver 的类继承关系识别。
- `self` 子模块构造及链式赋值、共享别名分别建模。ModuleList 仅在同构且对象来源不同的静态成员上记录折叠倍数；共享成员不计作重复实例，动态次数为 `None`。容器动态 / 部分成员产生局部 issue，条件或循环中构造标 Probable。
- `forward` 的直接子模块调用、静态 / 动态容器索引及简单容器循环可关联 CallSite；动态选择保留 `possible_instance_ids`，不伪造唯一目标。模型阶段异常产生 `MODEL_ERROR`，保留其他文件或已取得的事实。
- 无新增运行时依赖；不导入或执行被分析项目和 PyTorch。

## 验证与审查

- 阶段开始前工作树干净；Phase 4 基线为 63 项测试、1 项环境性跳过。
- 采用测试先行：新增 23 项模型测试，覆盖导入遮蔽、常见层与跨文件自定义子类、三类容器、同构与共享、零次 / 动态重复、条件构造、forward CallSite、未知工厂、规则异常、未知容器成员的位置保持和不执行源码。
- 本地最终质量检查：Ruff 通过、mypy 严格模式通过（21 个源码文件）、unittest 86 项中 85 项通过 / 1 项 Windows 符号链接环境性跳过、`git diff --check` 通过。
- 本地虚拟环境缺少 `setuptools`，使用该环境执行 `pip wheel --no-build-isolation` 失败；改用本机现有 `setuptools` 离线构建 wheel 成功，并确认 wheel 包含 Phase 5 新模块。这是打包环境差异，不是运行时依赖变更；GitHub CI 的构建结果以推送后实际检查为准。
- 两轮独立只读代码审查指出本地同名 torch、局部 / 顶层遮蔽、条件构造、共享容器成员、零次重复、动态调用、未知工厂、局部异常隔离、未知成员索引错位、失败时的孤立子节点与容器扫描性能等风险；已由新增测试驱动修正。原有失败：无；新增失败：最终本地质量门禁未发现。

## 限制与下一阶段

详见 `KNOWN_LIMITATIONS.md`。Phase 6 将消费模型事实与现有 Flow Graph，增加训练语义规则和 Semantic Component Graph；不把本阶段尚未证实的运行时行为升级为 Exact。
