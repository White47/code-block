# 目标项目结构

以下是当前与目标职责分区。Phase 1 已建立 `src/codeviz/core/`；Phase 2–6 已建立 `src/codeviz/analysis/`，集中放置扫描、解析、Resolver、Full Graph / flow、模型和训练事实与项目级汇总；Phase 5–6 已建立 `src/codeviz/semantic/`；Phase 7–9 已建立 `src/codeviz/views/`；Phase 8–9 已建立 `src/codeviz/ui/`，Phase 9 增加顶层 `search.py` 和 `navigation.py`。其余目录按真实需要创建，不提前铺空目录：

```text
src/codeviz/
  search.py       # Phase 9：定义与语义组件的有限结果查询
  navigation.py   # Phase 9：结果到现有视图根 / 选中 ID 的路由
  core/          # 已建立：ID、SourceAnchor、confidence、图与视图基础类型
  analysis/       # 已建立：discovery、python、symbols、imports、resolution、graph_build、control_flow、data_flow、flow、call_cycles、model、training、project
  graph/          # 后续：flow、query；Code Graph IR 已在 core/
  semantic/      # 已建立：ModelRule、PyTorch 模型/训练规则、组件图
  views/         # 已建立：语义查询、关键数据投影、局部代码图、View Builder、layout hints、ViewState
  persistence/   # Analysis Cache、View State、Annotations
  ui/             # 已建立：presenter、controller、graph_canvas、theme、tkapp；Search / Symbols 复用同一侧栏
tests/
  fixtures/
docs/
```

规则：事实层不得依赖 UI；UI 不直接解析 AST；Resolver 只有一个权威入口；缓存 schema 与 UI 状态分离；所有跨层对象通过明确 DTO / domain type 传递。
