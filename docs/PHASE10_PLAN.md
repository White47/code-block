# Phase 10 Implementation Plan

Goal: 交付可安全恢复的本地缓存、Outdated 反馈和独立用户状态，遵守 `PHASE10_DESIGN.md`。

| Task | Goal / deliverables / likely files | Dependencies | Tests / acceptance | Out of scope |
| --- | --- | --- | --- | --- |
| 1 可缓存领域快照 | 显式 JSON codec、`ParsedFile.parsed_success`、缓存 schema；`src/codeviz/persistence/codec.py`、`analysis/python.py`、Presenter | Phase 1–9 类型 | 图/索引/语义往返后能查询；缓存没有 AST 或源码；恶意/损坏 payload 拒绝 | pickle / 完整源码副本 |
| 2 指纹与存储 | 应用数据目录、源文件/配置/版本指纹、原子 Analysis Cache；`persistence/fingerprint.py`、`store.py`、ADR-007 | Task 1、discovery | 无变化命中，改/增/删、Ignore Paths、版本均过期；坏缓存不污染源码或 annotations | watcher / 增量解析 |
| 3 会话状态 | Controller 自动读取缓存、Outdated 原因、显式重分析；ViewState 与 Annotations 分离、Recent Projects；`ui/controller.py`、`persistence/store.py` | Tasks 1–2 | 旧图仍可查、重分析清过期、状态独立、失败保留旧缓存 | 语义编辑器 |
| 4 UI | Overview / 图 / Source 的过期提示、Recent Projects、Re-analyze；`ui/tkapp.py`、Presenter | Task 3 | 有显示器 Tk 闭环；无显示器 Controller 测试 | 通用设置面板 |
| 5 收尾 | 文档、阶段报告、独立复核、质量门禁 | Tasks 1–4 | unittest、Ruff、mypy、wheel，区分既有跳过与新增失败 | Phase 11 大项目性能验收 |

每项先写失败测试再实现。保留 Phase 9 已合入 `main` 的代码，当前阶段在独立功能分支进行；无需新增运行时依赖或迁移 GUI 技术栈。
