# Phase 9 Implementation Plan

Goal: 打通 Search → Reveal in Graph → Inspector → Source，遵守 `PHASE9_DESIGN.md`。

| Task | Deliverables / likely files | Dependencies | Tests / acceptance | Out of scope |
| --- | --- | --- | --- | --- |
| 1 搜索契约 | `src/codeviz/search.py`，定义/语义查询结果、排序、上限；`tests/test_search.py` | Phase 3/6 的 Symbol Index 与 Semantic Graph | 同名 scope、中文/英文别名、空查询、无匹配与截断；每项有真实锚点 | 模糊 AI 语义 |
| 2 定位规划 | `src/codeviz/navigation.py`，结果→视图/根/选中 ID；`tests/test_navigation.py` | Task 1，Phase 7 | 模型、训练、普通符号、关键数据各走正确视图；不可定位有明确原因 | Runtime 目标猜测 |
| 3 局部代码图 | `src/codeviz/views/code_context.py` 与 View Builder 路由；`tests/test_views.py` | Task 2，Phase 4/7 | 默认 1-hop、上下游最多 5-hop、预算、锚点/边证据；无 Full Graph 直渲染 | 全量 Calls/Gradient 专业视图 |
| 4 UI 闭环 | `src/codeviz/ui/tkapp.py`、控制器、图例；`tests/test_ui_tkapp.py` | Tasks 1–3，Phase 8 | Search/Symbols 查询，Reveal 保留选择并更新 Inspector，Open Source/Side 到准确行；切项目清旧结果 | 持久化与增量索引 |
| 5 文档及阶段验收 | `docs/PHASE9_REPORT.md`、限制/计划同步 | Tasks 1–4 | unittest、Ruff、mypy、wheel、Windows UI 烟测；区分既有失败 | Phase 10 缓存 |

每项按红→绿→重构执行，阶段末独立审查。沿用指定的 `<workspace>` 工作区及 in-place 功能分支，不创建额外 worktree，避免改变用户已指定的工作目录。产品设计已有明确批准；本文件只记录工程细化，不声称用户审阅了新文档。

状态：Task 1–5 已完成；验证、审查结论与剩余限制见 `PHASE9_REPORT.md`。
