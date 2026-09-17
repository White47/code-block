# Phase 2 实施报告

日期：2026-09-17

## Goal 与范围

在 Phase 1 领域类型之上提供只读的一次性 Python 分析入口：文件发现 → AST → 词法符号索引 → 文件级问题汇总。不执行被分析项目，不做跨文件名称解析、语义推断或 UI。

## Changed files

- 实现：`src/codeviz/analysis/__init__.py`、`discovery.py`、`python.py`、`symbols.py`、`project.py`。
- 测试：`tests/test_discovery.py`、`test_parser.py`、`test_symbols.py`。
- 文档：`README.md`、`docs/IMPLEMENTATION_PLAN.md`、`PROJECT_STRUCTURE.md`、`DATA_MODEL.md`、`ARCHITECTURE.md`、`KNOWN_LIMITATIONS.md`、`TEST_STRATEGY.md` 与本报告。

## 实现摘要

- `discover_python_files` 返回排序后的项目相对 `.py` 路径；忽略常见依赖、缓存、构建、数据和权重目录，并允许按相对路径额外忽略。拒绝越界忽略路径和符号链接遍历。
- `parse_python_file` 仅解码、解析源码，支持 Python 编码声明；限制单文件大小，语法 / 解码 / 读取 / 过深 AST 失败产生带源码锚点的 issue。
- `index_parsed_file` 记录文件、模块、类、函数、方法、参数、变量、`self` 属性、导入别名和原始 Load 引用；定义拥有稳定 ID、scope、qualifiedName、parentSymbol 与 SourceAnchor。引用暂不解析。导入别名重新绑定不会保留过时的 `imported_from` 元数据。
- `analyze_project` 汇总各文件；解析或索引阶段单文件失败不会删除其他文件的符号与问题。

## 验证

- 进入阶段前 Git 工作树干净；Phase 1 基线为 13/13 测试通过。
- 测试先于实现运行并观察到预期失败；Phase 2 完成后全套 32 项测试通过，其中 1 项因当前 Windows 环境无法创建测试符号链接而跳过。
- Lint / typecheck：仓库仍未配置专用命令，不声称通过。
- 离线 wheel 构建：通过；wheel 内确认包含 `codeviz.analysis` 全部模块。`git diff --cached --check`：通过。
- 原有失败：无；新增失败：当前未发现。

## 限制与下一阶段

见 `KNOWN_LIMITATIONS.md`。指定连接器 `workspace_info` 仍返回内部错误，当前路径检查采用用户指定的本地目录。Phase 3 将建立独立 Symbol Resolver，按作用域和 import 解析 references，并显式表示 External / Unresolved / possible targets；此阶段不把不确定的引用伪装为 Exact。
