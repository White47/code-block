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

Phase 4 增加 `CallSite`、`FlowStep`、`ControlBlock`、`Data`。CallSite 使用 Invocation role 和独立稳定 ID；调用位置与函数定义不是同一个节点。控制块记录条件摘要和 SourceAnchor，关键数据节点保留对应符号 ID 与名称规则依据。

Definition 的 Stable ID 优先由 `relativePath + qualifiedName + kind + scope` 生成；同一作用域内确有重复定义时可额外加入结构发生点标识。Instance / Invocation 可以在同一作用域出现多次，必须加入可复算的发生点标识（例如词法父节点、结构位置 / 表达式指纹和同级序号），保证重复调用不碰撞。ID 不能随机生成，也不能只依赖行号。源码重排可能改变发生点 ID，缓存与 Annotation 迁移应尽力匹配并报告无法匹配的项。

## 4. CodeEdge

```text
CodeEdge {
  source, target, type, confidence, sourceRange?, metadata
}
```

边类型至少包含 CONTAINS、IMPORTS、EXPORTS、CALLS、INHERITS、IMPLEMENTS、CONTROL_FLOW、BRANCH_TRUE、BRANCH_FALSE、LOOP_BACK、READS、WRITES、PASSES_TO、RETURNS_TO、DATA_FLOW、GRADIENT_FLOW。动态调用可带 possibleTargets，但不得伪造单一确定目标。

Phase 4 的 `CALLS` 从 CallSite 指向已证实的函数 / 方法，或指向 Probable 候选 / UnresolvedSymbol；解析到参数或变量只证明绑定身份，不证明实际可调用目标，`self.method()` 因可能被子类覆写也不标 Exact。CallSite 通过 CONTAINS 关联到所在流程步骤 / 控制块；`scope_symbol_id` 保留词法调用者以计算调用环。`CONTROL_FLOW`、`BRANCH_TRUE/FALSE`、`LOOP_BACK` 表示静态结构，不代表实际运行路径。`READS` / `WRITES` 从流程步骤指向关键数据；`DATA_FLOW` 是赋值表达式的静态依赖，置信度为 Probable，不等同于 Gradient Flow。

## 5. SemanticComponent

```text
SemanticComponent {
  id, displayName, codeName?, role?, confidence, evidence[],
  childComponents[], codeSymbolIds[], sourceAnchors[], metadata
}
```

一个组件可对应一个或多个类、函数或 `forward()` 内部逻辑段；不要假设一一对应。

Phase 6 的 `ProjectAnalysis.semantic.graph` 使用 `SemanticGraph(components, relations, roots)`。每个组件保留源码锚点、代码符号 ID 或子组件；每条 `SemanticRelation` 保留源、目标、EdgeType、confidence、evidence 与 sourceAnchors，端点和 child ID 必须在图内。模型 Definition 与 Module Instance 分别有组件；入口、训练事件、循环和合成 `ControlJoin` 分别建模，循环子组件通过 `child_component_ids` / `CONTAINS` 关联。`CONTROL_FLOW` 来自 AST 分支前沿及有源码锚点的汇合组件，不从平铺事件序列猜测；`GRADIENT_FLOW` 只在同一静态分支路径上连接 Backward→Optimizer，领域契约禁止其为 Exact；它不是 autograd 观察。Attention-like 是 Probable 候选，保留真实类名与 forward 锚点。

## 6. DataNode / EntryPoint

DataNode 角色：INPUT、TARGET、MASK、FEATURE、HIDDEN_STATE、PREDICTION、LOSS、PARAMETER、GRADIENT、OTHER。`shape`、`dtype`、`device` 仅保存静态可确认值与 confidence，不通过执行代码获取。

Phase 4 仅按有限变量 / 参数 / `self` 属性名称表创建关键 DataNode，角色标记为 Probable；导入别名和普通临时变量不进入关键数据集合。简单直线赋值可通过隐藏临时变量传递关键数据来源，覆盖赋值会清除旧来源；分支和循环合流保持保守。`FlowAnalysis.data_nodes` 与 `FlowAnalysis.graph` 同时返回，后续 View Builder 再决定可见性。

Phase 5 的 `ProjectAnalysis.model` 返回独立 `ModelAnalysis`：`ModelDefinition` 以 Symbol Index 的 class ID 为 `symbol_id`，含 base、forward method ID、evidence 与 SourceAnchor；`ModuleInstance` 有稳定的 Instance ID、所属定义 ID、构造层名或项目类 ID、容器种类、按原位置保留的 `member_instance_ids`（未知槽位为 `None`）、`repeat_count: int | None` 与 `shared_with_id`；`ModuleInvocation` 使用已有 CallSite ID 关联被调用 Instance，保留调用锚点和动态选择时的 `possible_instance_ids`。`repeat_count=None` 表示容器重复次数静态未知；`repeat_count > 1` 仅表达来源不同的同构结构候选，不等同共享权重。`shared_with_id` 才表示静态可见的同一对象别名。直接 `forward` 调用仍是 Probable，因为运行时可重绑定。模型事实与 Phase 4 Full Code Graph 分开保存，Phase 6 组成语义图，Phase 7 再生成局部可见图。

EntryPoint 至少包含 id、name、kind（Train / Evaluate / Inference / Export）、sourceAnchor、flowNodeIds、modelIds。

`ProjectAnalysis.training` 返回独立 `TrainingAnalysis(entry_points, events, issues, joins)`。`TrainingEvent` 保存稳定 ID、role、sourceAnchor、confidence、evidence、entryPointId、原始 CallSite / ControlBlock ID、真实调用名、`parent_loop_id`、静态 `branch_path` 与 `control_predecessor_ids`；后者由 AST 遍历时各可能路径的前沿事件产生。循环事件另存 `loop_back_predecessor_ids`，指向循环体结束时的控制前沿，而非词法子节点列表最后一项。`TrainingControlJoin` 保存分支汇合来源、前驱 ID、入口与循环归属；当不同路径汇合时先汇入该合成节点，以免大量可跳过分支使后续事件前驱成平方增长。`flowNodeIds` 仍按静态源码访问顺序引用训练事件，不把控制汇合节点冒充训练步骤。保存和加载调用只生成 Probable 的 Checkpoint 候选，因为 `torch.save/load` 不一定用于训练断点。

## 7. SymbolIndexEntry

包含 name、qualifiedName、kind、scope、filePath、range、parentSymbol、definition；逻辑上关联 references[]。Python 实现把 `SymbolIndexEntry` 与 `SymbolReference` 分开保存于 `SymbolIndex.entries` / `SymbolIndex.references`。原始引用保持未解析；独立 Resolver 返回索引副本，按证据填 `resolved_definition_id`（Exact）、`possible_target_ids`（Probable）或 `unresolved_symbol_id`（Unresolved），并保留 `confidence`、`reason`、`scope_symbol_id`。`ResolutionResult` 另存 ExternalSymbol、UnresolvedSymbol 与 issues，`ProjectAnalysis` 同时提供解析后的索引与该结果。同名符号按作用域所有者 ID 消歧，重复定义允许相同 qualifiedName 但不同 ID；嵌套符号 ID 包含父符号结构身份，避免重复函数体的局部符号碰撞。

## 8. VisibleGraph

VisibleGraph 是查询结果，不是事实存储：包含 viewKind、rootIds、visibleNodes、visibleEdges、aggregations、layoutKind、layoutHints、legend、hiddenCounts、queryContext 与本次 maxNodes / maxEdges。Phase 7 的 `build_visible_graph(ProjectAnalysis, ViewRequest, ViewState)` 支持 Architecture / Execution；Phase 9 增加 Symbol / Data Flow 局部代码图。默认最多 100 个节点、300 条边，选中节点可取 1-hop，上限 5-hop；代码图支持 both / upstream / downstream。语义可见节点携带 SemanticComponent ID、可用的代码符号 ID 和 SourceAnchor；关键数据可见节点携带真实 CodeNode ID 和 SourceAnchor，DATA_FLOW 边取自代码图并保留赋值锚点与 Probable，不从相近位置猜造新数据边。Data Flow 视图按信息方向将 READS 呈现为 data→step，底层 CodeEdge 仍保存 step→data 的事实语义。溢出聚合节点携带真实成员 ID 集合，不把它们伪装成新的代码事实。hiddenCounts 包含被折叠或预算截断的可达节点 / 关系；legend 按实际可见边给出不同线型。`ViewState` 的 expandedIds / 手动 positions 可转成 JSON 数据并恢复，但 Phase 10 前不做磁盘持久化；任何布局状态都不写回分析事实。

## 9. SearchResult / RevealTarget

Phase 9 的 `SearchResult` 只代表索引中的定义或带源码锚点的语义组件，包含 `id`、`source_kind`、`name`、`qualified_name`、`kind`、`scope`、`source_anchor` 与 `confidence`；`SearchResponse` 另给 `total_count` 和 `truncated`。未解析引用不被伪装成确定定义。`RevealTarget` 记录目标视图、根 ID、选中 ID 或无法定位的原因。关键变量搜索使用原 Symbol ID 查询，再显式关联对应 DataNode ID；语义组件使用真实组件 ID。搜索结果本身不创造新图事实。
