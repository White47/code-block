# Phase 9 实施报告

日期：2026-09-18。范围：搜索定义与静态语义、按真实 ID 路由到局部图、Inspector 与只读源码；缓存 / 增量分析属于 Phase 10。

## 实现摘要

- `search.py` 基于既有 Symbol Index 和 Semantic Graph 提供有上限的搜索；同名定义显示限定名、类型、文件行与 confidence。中文“反向传播 / 参数更新”及英文对应词指向真实训练组件，不把未解析引用伪装成定义。空查询用于 Symbols 浏览，结果截断有提示。
- `navigation.py` 将模型、训练事件、普通符号和关键变量分别路由到 Architecture、Execution、Symbol Context 与 Data Flow Context；已在正确视图中的目标可原位定位。无法构建图时返回显式原因，结果的真实 SourceAnchor 仍可直接打开。
- `views/code_context.py` 从现有 Full Code Graph 按方向取 1–5 hop 局部邻域，默认 1-hop，最多 100 节点 / 300 边。READS 在 Data Flow 视图按 data→consumer 显示，保留底层原始事实与锚点；超预算保留 hidden count / 聚合成员。
- Tk Search / Symbols 侧栏提供查询、Reveal、直接 Open Source；图中选中后沿用 Inspector 与 Open to Side。局部图可选 Both / Upstream / Downstream / + hop；新搜索目标恢复默认 1-hop/Both。切项目和重新分析清除旧结果。没有新增运行时依赖、源码执行或写回。

## 变更文件

- 产品代码：`src/codeviz/search.py`、`navigation.py`、`views/code_context.py`、`views/builder.py`、`ui/controller.py`、`ui/graph_canvas.py`、`ui/tkapp.py`。
- 测试：`tests/test_search.py`、`test_navigation.py`、`test_views.py`、`test_ui_tkapp.py`。
- 文档：`README.md`、`docs/PHASE9_DESIGN.md`、`PHASE9_PLAN.md`、`PHASE9_REPORT.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`IMPLEMENTATION_PLAN.md`、`PROJECT_STRUCTURE.md`、`TEST_STRATEGY.md`、`KNOWN_LIMITATIONS.md`。

## 验证与审查

- 先有失败测试再实现。全量 unittest：184 项，0 失败，2 项跳过（原有 Windows 符号链接创建权限不足）；Tk 实例测试在 Windows 图形环境中实际运行。Ruff `src tests`、mypy 严格模式、`git diff --check` 通过；隔离构建 wheel 成功。
- 独立只读审查先发现 Data Flow 点击流程步骤报错、READS 上下游反向、新搜索结果继承旧 hop / 方向三项。逐项补红绿测试后，复审进一步指出“第二个结果已在同一局部图”时仍会继承旧设置；用相连数据节点补测试并修复。最终复核结论 Ready to merge: Yes，无剩余 Critical / Important。复核者未独立运行测试，因此以本机上述验证结果为准。

## 留待后续

- Phase 10：Analysis Cache、Outdated / Re-analyze、Recent Projects、ViewState 与 Annotations 独立持久化。
- Source → Graph 反查、完整调用 / 梯度专业视图、全文搜索、复杂语义别名、动态目标证明仍未实现；具体限制见 `KNOWN_LIMITATIONS.md`。GitHub CI 结果未在本阶段本地验证，不以本报告代替远端检查。
