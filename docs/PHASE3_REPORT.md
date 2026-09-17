# Phase 3 实施报告

日期：2026-09-17

## Goal 与范围

在 Phase 2 的只读 AST 与原始引用之上，加入独立的 Python 符号解析器：项目内常见导入、词法作用域、显式成员，以及确定 / 可能 / 未解析的目标。仍不导入或执行被分析项目，不生成调用、控制或数据流图。

## Changed files

- 实现：`src/codeviz/analysis/imports.py`、`resolution.py`、`symbols.py`、`project.py`。
- 测试：`tests/test_resolution.py`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`DATA_MODEL.md`、`IMPLEMENTATION_PLAN.md`、`KNOWN_LIMITATIONS.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md` 与本报告。

## 实现摘要

- `ModuleCatalog` 从已扫描文件构造项目模块目录，处理包、相对导入及常见 `src/` 布局；不调用 Python 的导入机制。未使用导入也检查，缺失本地成员 / 子模块产生 issue，第三方导入保留 ExternalSymbol。
- `SymbolResolver` 在独立模块里根据作用域所有者、`global` / `nonlocal`、项目导入链与显式成员解析引用。唯一有证据的目标标为 Exact；多目标保留 possible targets；动态或未知目标保留 UnresolvedSymbol 与原因。纯导入别名环、星号导入等产生可查看的问题。
- 嵌套符号的稳定 ID 引入父符号身份，避免重复同名函数体里的局部符号碰撞。`analyze_project` 对外返回解析后的 SymbolIndex 与独立 `ResolutionResult`；原始索引不被原地修改。
- 无新增运行时依赖；所有分析仍是静态、只读操作。

## 验证

- 进入阶段前：`main` 与远端一致、工作树干净；既有 32 项测试中 1 项因本机不支持测试符号链接而跳过，Ruff 与 mypy 基线通过。
- 先写失败用例再修复：未使用导入、缺失子模块等边界都已观察到预期失败后转绿。
- 全套检查最终结果：Ruff 通过；mypy 严格模式通过（12 个源码文件）；unittest 45 项中 44 项通过、1 项环境性跳过；`git diff --check` 通过。
- 离线 wheel 构建通过，wheel 内包含 `analysis/imports.py` 与 `analysis/resolution.py`。仓库 `.venv` 未安装 `setuptools`，用它离线构建会因缺少后端失败；改用已安装 `setuptools` 的系统 Python 完成同一构建。该失败归类为本地打包环境问题，不是源码错误。
- 新增回归失败：无。GitHub Actions 需在推送后另行确认，本报告不预称远端通过。

## 限制与下一阶段

详见 `KNOWN_LIMITATIONS.md`。Phase 4 将基于解析后的引用构建调用关系、基础控制流和关键数据流，并保持不确定目标与不同关系类型的区别；本阶段不提前进入 Phase 4。
