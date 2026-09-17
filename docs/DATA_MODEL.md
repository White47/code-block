# 数据模型

## 1. 通用约定

所有模型均为可序列化领域对象；字段可扩展但不得破坏稳定 ID。`confidence` 统一为 `Exact | Probable | Unresolved`。

## 2. SourceAnchor

```text
SourceAnchor {
  filePath: relative path
  startLine: number
  startColumn?: number
  endLine: number
  endColumn?: number
}
```

来自用户源码的 CodeNode、CodeEdge、SemanticComponent、DataNode、EntryPoint 必须保留一个或多个 SourceAnchor 或可追溯到带锚点的来源对象。Project、聚合节点、ExternalSymbol 等合成或外部实体可以没有自身的源码锚点，但必须保留创建依据（例如项目中的 import / 调用位置）；确实没有可用位置时，UI 应说明无法打开源码的原因，不得提供失效的 Open Source 动作。

## 3. CodeNode

```text
CodeNode {
  id, kind, name, qualifiedName?, scope?, filePath?, range?, language,
  metadata, confidence, sourceAnchors[]
}
```

`kind` 至少支持 Project、Module、File、Class、Function、Method、Variable、Parameter、Attribute、ExternalSymbol、UnresolvedSymbol。Definition、Instance、Invocation 必须区分。

Definition 的 Stable ID 优先由 `relativePath + qualifiedName + kind + scope` 生成；同一作用域内确有重复定义时可额外加入结构发生点标识。Instance / Invocation 可以在同一作用域出现多次，必须加入可复算的发生点标识（例如词法父节点、结构位置 / 表达式指纹和同级序号），保证重复调用不碰撞。ID 不能随机生成，也不能只依赖行号。源码重排可能改变发生点 ID，缓存与 Annotation 迁移应尽力匹配并报告无法匹配的项。

## 4. CodeEdge

```text
CodeEdge {
  source, target, type, confidence, sourceRange?, metadata
}
```

边类型至少包含 CONTAINS、IMPORTS、EXPORTS、CALLS、INHERITS、IMPLEMENTS、CONTROL_FLOW、BRANCH_TRUE、BRANCH_FALSE、LOOP_BACK、READS、WRITES、PASSES_TO、RETURNS_TO、DATA_FLOW、GRADIENT_FLOW。动态调用可带 possibleTargets，但不得伪造单一确定目标。

## 5. SemanticComponent

```text
SemanticComponent {
  id, displayName, codeName?, role?, confidence, evidence[],
  childComponents[], codeSymbolIds[], sourceAnchors[], metadata
}
```

一个组件可对应一个或多个类、函数或 `forward()` 内部逻辑段；不要假设一一对应。

## 6. DataNode / EntryPoint

DataNode 角色：INPUT、TARGET、MASK、FEATURE、HIDDEN_STATE、PREDICTION、LOSS、PARAMETER、GRADIENT、OTHER。`shape`、`dtype`、`device` 仅保存静态可确认值与 confidence，不通过执行代码获取。

EntryPoint 至少包含 id、name、kind（Train / Evaluate / Inference / Export）、sourceAnchor、flowNodeIds、modelIds。

## 7. SymbolIndexEntry

包含 name、qualifiedName、kind、scope、filePath、range、parentSymbol、definition、references[]。同名符号必须按作用域消歧。

## 8. VisibleGraph

VisibleGraph 是查询结果，不是事实存储：包含 viewKind、rootIds、visibleNodes、visibleEdges、aggregations、layoutHints、legend、hiddenCounts、queryContext。每个可见节点需携带到 SemanticComponent / CodeNode / SourceAnchor 的来源引用；聚合节点携带成员 ID 集合。用户布局和展开状态不得写回分析事实。
