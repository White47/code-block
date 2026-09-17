# Phase 1 实施报告

日期：2026-09-17

## Goal 与范围

用户确认从 `<workspace>` 空目录新建项目。本阶段建立与 UI 解耦的 Python 领域核心：SourceAnchor、Stable ID、Code Graph IR、基础语义 / 数据 / 可视对象。未进入文件扫描、AST、PyTorch 规则或 UI。

## Changed files

- 项目入口：`.gitignore`、`pyproject.toml`、`README.md`。
- 核心实现：`src/codeviz/__init__.py`、`src/codeviz/core/__init__.py`、`identity.py`、`model.py`、`graph.py`。
- 测试：`tests/test_identity.py`、`tests/test_graph.py`。
- 文档：`docs/PHASE0_AUDIT.md`、`PRD.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`SEMANTIC_RULES.md`、`UX_SPEC.md`、`MVP_ACCEPTANCE.md`、`ROADMAP.md`、`IMPLEMENTATION_PLAN.md`、`TEST_STRATEGY.md`、`PROJECT_STRUCTURE.md`、`ERROR_MODEL.md`、`CACHE_SPEC.md`、`PERFORMANCE.md`、`KNOWN_LIMITATIONS.md`；新增 `docs/adr/005-stdlib-core-first.md` 与本报告。

## 实现摘要

- SourceAnchor 使用项目相对路径，校验范围并拒绝越界路径。
- Stable ID 使用结构身份的确定性摘要；Instance / Invocation 必须提供发生点键，重复 Definition 可选择提供。
- CodeNode 区分用户源码、合成和外部来源；用户源码节点必须有锚点。
- CodeGraph 拒绝重复节点与悬空关系，保留 uncertain call 的 possible targets，支持 JSON 往返。
- SemanticComponent、VisibleNode 和 VisibleEdge 要求来源依据；VisibleGraph 校验节点和关系引用。
- Python 3.11+ 标准库运行时，setuptools 打包，无运行时第三方依赖。

## 验证

- `unittest`：13 项通过。测试先于相应功能实现运行，确认缺失行为后再实现。
- 离线 wheel 构建：通过（`--no-deps --no-build-isolation --no-index`）。
- Lint / typecheck：项目尚未配置命令，未声称通过。
- 原有失败：新建项目无旧测试基线；新增失败：当前未发现。

## 限制与下一阶段

指定连接器 `workspace_info` 仍返回内部错误，不能据此验证绑定身份；直接检查的目标路径来自用户明确指定。Phase 2 将实现 Python 文件发现、AST Parser 和 Symbol Index，并建立第一个可分析 fixture；进入前再次核对 Git 状态、忽略规则及测试入口。
