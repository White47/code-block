# 目标项目结构

以下是目标职责分区。Phase 1 已建立 `src/codeviz/core/`；Phase 2–3 已建立 `src/codeviz/analysis/`，集中放置文件扫描、AST 解析、词法索引、模块目录、独立解析器和项目级汇总。其余目录应在相应阶段按真实需要创建，不提前铺空目录：

```text
src/codeviz/
  core/          # 已建立：ID、SourceAnchor、confidence、图与视图基础类型
  analysis/       # 已建立：discovery、python、symbols、imports、resolution、project
  graph/          # 后续：flow、query；Code Graph IR 已在 core/
  semantic/      # 规则插件、PyTorch、Training、组件图
  views/         # View Builder、Visible Graph、layout hints
  persistence/   # Analysis Cache、View State、Annotations
  ui/             # Overview、views、Inspector、Search、Source
tests/
  fixtures/
docs/
```

规则：事实层不得依赖 UI；UI 不直接解析 AST；Resolver 只有一个权威入口；缓存 schema 与 UI 状态分离；所有跨层对象通过明确 DTO / domain type 传递。
