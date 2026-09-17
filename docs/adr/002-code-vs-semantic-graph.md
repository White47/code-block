# ADR-002：Code Graph 与 Semantic Graph 分离

- Status：Accepted
- Date：2026-09-17

## Context

源码事实、框架语义和面向人的论文式组件抽象粒度不同；强行合并会造成解析与展示耦合，也会掩盖不确定性。

## Decision

Code Graph 保存真实符号和关系；Semantic Graph 保存规则解释、组件组合、evidence 与 confidence；Visible Graph 仅是查询结果。三者通过 stable IDs 和 traceability 关联。

## Consequences

自定义代码即使不能命名，也能展示结构；语义规则可以迭代而不重写 parser；缓存和 UI 状态必须分层保存。
