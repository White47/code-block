# 系统架构

## 1. 当前约束

Phase 0 初次审计未发现现有代码或技术栈；用户确认从空目录新建。Phase 1 的 `src/codeviz/core` 使用 Python 3.11+ 标准库，具体决定见 ADR-005。以下是目标边界与接口契约；桌面壳、图形库和状态库尚未选择。

## 2. 分层

```text
Project Source / Config
  → File Discovery / Language Detection
  → Python Parser (AST)
  → Symbol Index / Symbol Resolver
  → Code Graph IR
  → Call / Control / Data Flow
  → Framework Semantic Rules
  → Semantic Component Graph
  → View Builder / Graph Query
  → Visible Graph / Layout
  → Search / Navigation / Inspector / Source
```

## 3. 模块责任

- File Discovery：扫描 `.py`，忽略 `.git`、虚拟环境、`node_modules`、缓存、构建目录、权重、数据集、二进制；支持用户 Ignore Paths。
- Parser：将 Python AST 转为带 SourceAnchor 的解析结果；部分语法失败不能阻断其他文件。
- Symbol Index：登记 definition / reference、scope、qualifiedName、parentSymbol 和稳定 ID。
- Symbol Resolver：独立处理 import、局部作用域、属性和 unresolved / possible targets；不得散落在 UI 或 parser 中。
- Code Graph IR：保存 Project、Module、File、Class、Function、Method、Variable、Parameter、Attribute、ExternalSymbol、UnresolvedSymbol 与关系边。
- Flow Analysis：基础调用、顺序、if、loop、recursion，以及关键 DataNode；不执行代码。
- Semantic Rules：Generic Python / Generic PyTorch / Transformer / Training 插件，输出依据与 confidence。
- Semantic Graph：把类、函数或内部逻辑段组合成可读组件，可与代码符号多对多关联。
- View Builder：按视图和选中节点查询局部图、聚合重复结构、控制可见节点数。
- UI：VSCode 风格 Activity Bar、Sidebar、Editor Tabs / Split、Inspector、Problems。
- Cache：Analysis Cache、View State、Annotations 三者分离。

## 4. 关键边界

Parser 只负责语法事实；Resolver 负责名称；Semantic Rules 负责解释；View Builder 负责展示取舍。任何层都必须保留回溯链：`Visual Node → Semantic Component → Code Symbol → Source Anchor`。

## 5. 运行状态

项目分析状态为 Analyzing、Ready、Partial、Outdated、Failed。文件级失败只产生 Problem，不抹掉其他文件的可用结果。

## 6. 待真实仓库确认的技术决策

桌面壳、图渲染库、状态管理、持久化格式和源码查看组件仍暂缓。Phase 1 已选择 `unittest` 和 setuptools；后续技术决策应在相应阶段记录 ADR，并优先复用已建立的领域核心。
