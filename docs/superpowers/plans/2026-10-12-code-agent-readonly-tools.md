# B5 Read-only Index Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow AGENTS.md routing and the main agent's assigned write scopes.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. Technical requirements below are unchanged.

**Goal:** 提供基于不可变分析索引、严格授权过滤、有界结果与后端权威证据绑定的三个只读工具。

**Architecture:** search/relations 仅查询 B3 新值对象；显式片段请求通过 B4 安全 reader，权威绑定仅接受本 ToolSession 实际取得的 B4 receipt。ToolSession.dispatch 是唯一工具计次入口，C 是实际 wire 源码/token/model request 预算入口；索引查询不读取磁盘、不调用 DesktopBridge、不生成核心边 ID。

**Tech Stack:** Python 3.11+ 标准库、frozen/slots dataclass、unittest/subTest、现有 BudgetLedger/EvidenceRegistry 和 B3/B4 合同；无新依赖、SDK、网络或执行工具。

---

## 1. 依据、当前状态与实施门禁

必读正式批准规格 `docs/superpowers/specs/2026-10-11-code-understanding-agent-design.md` §5（工具/预算）、§6（真实引用）、§8（授权/无执行）；版本保护同时遵循 §7。B3 精确合同为 `docs/superpowers/plans/2026-10-12-code-agent-analysis-snapshot.md`；B4 为 `docs/superpowers/plans/2026-10-12-code-agent-source-excerpts.md`。写作时 B3 implementation running，B4 review in progress，不能把计划签名称为已交付实现。

**最新协调指令优先：** 主代理将修订 B4，使 reader 只调用 `ledger.reserve(BudgetReservation())` 检查取消/deadline，不预留 tool_calls、files 或 source_characters。B5 dispatch 唯一预留 tool_calls；C 每次真正 wire 发送前预留文件、源码、输入 token、模型请求与输出 envelope。本文件不保留旧 B4 “读取计一次工具/第一次潜在暴露扣源码”的合同。若 B4 当前文件/实现仍保留旧合同，必须由主代理协调修改并独立复审后才接入 B5，不能同时启用两套收费。一次 read、两次 wire = 1 tool call、2 份 source；纯本地 read = 1 tool call、0 wire source。

B5 生产实现前门禁：B3 实现和独立规格/质量 review 通过；B4 最终接口（含上述计量修订）和独立 review 通过；主代理确认 additive B3 accessor 的文件写入串行安排。test_writer 可以先编写 typed fixture/测试合同，不能用未审查 B4 假端口宣布真实引用通过。C 新端口适配本 B5 合同，不反过来要求宽松/不安全读取。C 原有计划不是 B5 权限扩张依据。

本 architect 任务仅创建本文件，不写代码、测试、进度文件，不运行测试或 Git。主代理拥有全项目进度、授权协调和写范围；B5 实施为 cross-module/source mapping 工作，交 core_implementer；测试交 test_writer；实现后 reviewer 独立批判审查，复杂失败交 debugger。无架构难题/连续失败时不使用 final_auditor。用户已批准连续完成，不增加产品方向确认或自动提交步骤。

### 实际代码核对

- `src/codeviz/search.py` 的 `search_project` 使用可变 ProjectAnalysis；definition-only，casefold exact/prefix/substring 三级 rank，空查询仅浏览 symbol，非空支持七组固定中英文 role aliases。B5 复用这些纯匹配语义，不能调用其可变分析入口。
- `src/codeviz/navigation.py` 的 `plan_reveal` 返回 RevealTarget，但依赖 model/training/current VisibleGraph 及 component/node.metadata。B3 不复制这些字段，B5 不能据名称/role猜 view/root。B5 不调用 reveal/build_visible_graph；D 以后在 fresh/授权门禁后适配已有导航。
- `src/codeviz/views/query.py` 的 GraphQuery 内部字典可变且 descendants/path_to_root 不受本批预算限制；不能将其作为模型邻域工具。
- `src/codeviz/core/model.py` 的 CodeEdge/SemanticRelation 没有 ID；B3 original_index 是原 collection 的零起始 ordinal。CodeEdge possible_targets 仅 uncertain CALLS 候选，不能升级为确定邻接终点；semantic child/code IDs 不等于显式关系。
- A EvidenceRegistry 验证结构/归属/hash 匹配，不证明源字节真实；EvidenceRecord.confidence 的 B4 Exact 只证明源码位置。静态事实 confidence 必须从 B3 另外保留。
- B3 六类 records 总计默认不超过 200,000、text 16 MiB、anchors 200,000，构建超限整体失败；公共限量查询最大 1,000，不能枚举完整搜索集合或完整 adjacency。B4 另需要完整 file_inventory。

## 2. 文件结构及写范围

| 文件 | 动作与单一职责 |
| --- | --- |
| `src/codeviz/assistant/tool_contracts.py` | Create；严格工具请求/结果和本地 EvidenceBinding 值对象 |
| `src/codeviz/assistant/index_tools.py` | Create；纯索引 search 与 BFS，授权投影、稳定排序，零源文件 I/O |
| `src/codeviz/assistant/tools.py` | Create；ToolSession、dispatch 计次、B4 adapter、request-local receipts/绑定验证 |
| `src/codeviz/assistant/snapshot.py` | Modify；additive 完整 inventories 与不可变 adjacency，保留 B3 原查询语义 |
| `tests/assistant_tool_fixtures.py` | Create；typed synthetic 索引/假 state 和真实临时源码 fixture builders，无测试自动导入执行 |
| `tests/test_assistant_index_tools.py` | Create；search/relations/权限/完整性与无 I/O |
| `tests/test_assistant_tools.py` | Create；dispatch/B4 adapter/计量/状态/不执行 |
| `tests/test_assistant_evidence_binding.py` | Create；真实 returned_anchor、原始 hash、ordinal/provenance 和私有映射 |
| `tests/test_assistant_snapshot.py` | Modify；additive accessor 完整性、结构上限、不可变和旧 API 回归 |

不修改 core IR、resolver/parser、既有 search/navigation/view builder、A ledger 计量语义、UI/bridge、provider/service、网络/历史或持久化。B4 的计量修订及其测试属于主代理安排的 B4 范围，不由 B5 worker 顺手改。C wire 测试属于 `tests/test_assistant_service.py`，此处明确交接验收而不取得其写权限。所有文件由主代理先分配 owner，尤其 snapshot.py 不与 B3/B4 并行编辑。

## 3. B3 additive 完整访问器：不是 metadata 后门

以下 API 由 core_implementer 按 Task 1 在 B3 原投影基础上添加，不引用 ProjectAnalysis，不复制 metadata，不重扫文件，不放宽原 `limit=0..1000`。

| AnalysisSnapshot API | 精确返回/合同 |
| --- | --- |
| `file_inventory` | `tuple[FileRecord, ...]`；沿用 B4，同一已复制排序 tuple |
| `symbol_inventory` | `tuple[SymbolRecord, ...]`；全部 ID 排序 symbol，包括非 definition |
| `code_node_inventory` | `tuple[CodeNodeRecord, ...]`；全部 ID 排序 code nodes |
| `component_inventory` | `tuple[ComponentRecord, ...]`；全部 ID 排序 components |
| `code_edge_inventory` | `tuple[CodeEdgeRecord, ...]`；原 collection 顺序，原 ordinal 不变 |
| `relation_inventory` | `tuple[RelationRecord, ...]`；原 collection 顺序，原 ordinal 不变 |
| `incident_code_edges(node_id: str)` | `tuple[CodeEdgeRecord, ...]`；全部直接 source/target incident 边，按 original_index；self-loop 一次 |
| `incident_relations(component_id: str)` | `tuple[RelationRecord, ...]`；同上语义关系 |

合法 unknown ID 返回空 tuple；非法 ID 固定 AgentError(invalid_input)，无 I/O。内部 adjacency 在 builder 中由已经验证的完整 edge tuples 建立，每条最多两个引用，self-loop 一个；冻结 values 为 tuple，索引映射只私有不可变保存（MappingProxyType，底层 dict 不保留外部引用），不公开 dict/list。不新增 record、anchor 或任意 metadata。结构总空间 O(V+E)，adjacency references <=2E，受 B3 max_records 约束；不构造节点对矩阵。B3 builder的现有三参数合同保持不变，构建属于有界分析发布而非在模型请求中临时构建；B5查询扫描每64条和结束时执行其明确签名中的可信checkpoint，不能用1,000项API伪装全量。B3已批准的固定200000字符串tuple出现次数上限与精确字节账本保持有效，不因增加访问器放宽。

这里是有界完整 inventory，不是任意分页协议：最大工作由 B3 SnapshotLimits 限制。单次 search 最多扫描全部 symbols/components；2-hop 最多展开两层，incident tuple 总访问次数不超过两倍完整边数（visited 节点每层去重，每节点最多展开一次）。超时/取消必须失败，不能返回未标明的半扫描。无需引入 SQL、eval、DSL、regex、用户谓词或通用 graph query engine。

## 4. 公共合同（trusted Python），模型只进入 dispatch

DTO 均 frozen=True、slots=True；strict int 拒绝 bool/float，strict str 可 UTF-8 编码、无控制字符，tuple 严格 tuple。ID 非空最多 512 字符；query 可空、最多 2,000 Unicode codepoints；枚举采用 Literal 并在运行时严格验证。结果只输出白名单字段，不通用 serialize snapshot/receipt/binding/analysis。

架构续审澄清：上述无控制字符约束仅用于标识/元数据，而不用于实际源码正文。ExcerptToolResult.text采用独立strict str/UTF-8校验，≤4000 codepoints及≤200覆盖行，逐字符保留B4实际返回值，包括LF/TAB/formfeed/U+0085等，不增设控制字符白名单或删改/解释源码转义。B4仍先规范CRLF/CR为LF；普通JSON转义属于传输编码。正文DTO接受不证明来源权威，必须由ToolSession真实B4 receipt确认。

```python
from dataclasses import dataclass
from typing import Literal
from codeviz.core import Confidence, EdgeType, NodeKind, NodeOrigin
from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.snapshot import (
    AnchorRecord, SnapshotBinding, SymbolRecord, CodeNodeRecord,
    ComponentRecord, CodeEdgeRecord, RelationRecord,
)

@dataclass(frozen=True, slots=True)
class EntityRef:
    kind: Literal["symbol", "code", "semantic"]
    id: str

@dataclass(frozen=True, slots=True)
class RelationKey:
    collection_kind: Literal["code", "semantic"]
    original_index: int

@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    limit: int = 20

@dataclass(frozen=True, slots=True)
class RelationsRequest:
    seed: EntityRef
    hops: int = 2
    max_nodes: int = 50
    max_edges: int = 100

@dataclass(frozen=True, slots=True)
class SymbolHit:
    ref: EntityRef
    name: str
    qualified_name: str
    kind: str
    scope: str
    anchor: AnchorRecord
    confidence: Confidence
    rank: int

@dataclass(frozen=True, slots=True)
class SymbolSearchResult:
    results: tuple[SymbolHit, ...]
    total_count: int
    truncated: bool

@dataclass(frozen=True, slots=True)
class LocalNode:
    ref: EntityRef
    name: str
    kind: str
    origin: NodeOrigin | None
    confidence: Confidence
    anchors: tuple[AnchorRecord, ...]
    distance: int

@dataclass(frozen=True, slots=True)
class LocalRelation:
    key: RelationKey
    source: EntityRef
    target: EntityRef
    type: EdgeType
    confidence: Confidence
    anchors: tuple[AnchorRecord, ...]

@dataclass(frozen=True, slots=True)
class LocalRelationsResult:
    status: Literal["ok", "unavailable"]
    nodes: tuple[LocalNode, ...]
    relations: tuple[LocalRelation, ...]
    truncated: bool

@dataclass(frozen=True, slots=True)
class ExcerptToolResult:
    evidence_id: str
    relative_path: str
    text: str
    returned_anchor: AnchorRecord
    truncated: bool

@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    evidence_id: str
    scope: RequestScope
    snapshot_binding: SnapshotBinding
    target: EntityRef | RelationKey | None
    provenance: SymbolRecord | CodeNodeRecord | ComponentRecord | CodeEdgeRecord | RelationRecord | None
    returned_anchor: AnchorRecord
    content_hash: str
    confidence: Confidence
    truncated: bool
    status: Literal["bound", "source_only"]
```

NodeKind import above is the existing enum used when producing kind.value, not a new node vocabulary. EvidenceBinding 是本地权威 DTO，不可由模型构造，不导出 provenance/root/ignore/hash。bound 的 target/provenance 必须非 None 且来自当前 snapshot；source_only 两者 None，confidence=Exact 仅源码身份。不将 LocalRelation.key 命名 code_edge_id，不创建 `edge_123`。symbol namespace 与 code namespace 即使共享 ID 也需显式 EntityRef。

ToolSession 初始化参数：`snapshot: AnalysisSnapshot, *, scope: RequestScope, consent: SourceConsent, ledger: BudgetLedger, registry: EvidenceRegistry, current_state: Callable[[], SourceReadState], reader: SourceExcerptReader`。reader 必须由可信组合层用同一 snapshot/scope/consent/ledger/registry/state 构建；不允许模型替换 callback/reader。生产入口 `create_tool_session(snapshot: AnalysisSnapshot, *, scope: RequestScope, consent: SourceConsent, ledger: BudgetLedger, registry: EvidenceRegistry, current_state: Callable[[], SourceReadState]) -> ToolSession` 在 tools.py 内先以同参数构建 B4 SourceExcerptReader，再构建 ToolSession。测试可注入严格 typed reader fake；不依赖读取 B4 私有属性推断共享身份。scope 完整含 request_id，consent.binding 精确等于 snapshot.binding，current_state 按 B4 合同检查。所用 B4 类型从 source_excerpts/source_policy 模块导入，Callable 从 collections.abc 导入。

| API | 精确合同 |
| --- | --- |
| `search_symbols(snapshot, consent, request: SearchRequest, *, checkpoint: Callable[[], None]) -> SymbolSearchResult` | index_tools.py；纯索引，无账本计次 |
| `get_local_relations(snapshot, consent, request: RelationsRequest, *, checkpoint: Callable[[], None]) -> LocalRelationsResult` | index_tools.py；纯索引，无账本计次 |
| `ToolSession.search_symbols(request: SearchRequest) -> SymbolSearchResult` | 可信本地包装，检查状态/空 reservation，调用纯查询，不增加 tool_calls |
| `ToolSession.get_local_relations(request: RelationsRequest) -> LocalRelationsResult` | 同上 |
| `ToolSession.read_source_excerpt(request: ExcerptRequest) -> SourceExcerpt` | B4 adapter；验证真实返回与 registry，保存无文本 receipt，无 tool/source 计量；模型输出另作白名单投影 |
| `ToolSession.dispatch(name: str, payload: object) -> SymbolSearchResult | LocalRelationsResult | ExcerptToolResult` | 唯一计次入口；固定三个名字，严格 dict schema，片段转换为白名单DTO，不接受任意函数 |
| `ToolSession.bind_evidence(evidence_id: str, target: EntityRef | RelationKey | None) -> EvidenceBinding` | 本地 verifier；不能发起读取；仅当前 session issued receipt 可绑定 |
| `ToolSession.lookup_binding(evidence_id: str, target: EntityRef | RelationKey | None) -> EvidenceBinding` | 仅既有成功绑定；重新状态/范围检查，未知返回固定 evidence_unavailable |
| `ToolSession.close() -> None` | 幂等关闭；清私有 receipts/bindings，registry.clear，不重建 registry |

dispatch schemas：search=`{"query": str, "limit": int?}`；relations=`{"seed": {"kind": "code"|"semantic", "id": str}, "hops": int?, "max_nodes": int?, "max_edges": int?}`；excerpt=`{"file_path": str, "start_line": int, "start_column": int|None?, "end_line": int, "end_column": int|None?}`。问号仅表示键可省略，默认值沿用 DTO/B4（columns None）；unknown keys 拒绝，不能输入 scope/root/consent/hash/evidence/confidence/limits/callback。relations 不接受 symbol seed；请求方需用授权 code ID，symbol 有源码无 code node 时明确 unavailable。dispatch 非 dict/非法 name/unknown tool 均在计次后 fixed invalid_input；不能生成动态错误字符串。

架构复审明确原始路径入口顺序：excerpt payload的file_path先经source_policy.validate_source_path，再构造B3 AnchorRecord和B4 ExcerptRequest。B3规范化合同不改；不能在构造后才检验已经消失的`./`、重复/尾分隔符或反斜线。拒绝此类原始拼写时工具attempt计一次，reader、root I/O及证据注册均为零；C接受原始上下文路径时同样先验原值。

模型可见 excerpt 仅 `evidence_id, relative_path, text, returned_anchor, truncated`，无 requested_anchor、scope/hash/root、receipt、Monaco action 或任意 URI。search/relations 仅授权元数据，不含 evidence 文本、code_symbol_ids、children、possible_targets、metadata。结果的 anchor 是索引位置，不是可点击 citation；事实要启用引用必须显式 read 后 bind。search rank 表示检索相关性，不是 confidence。

## 5. 授权过滤和纯索引算法

### 5.1 统一可披露判定

每次 query 验证 consent.binding，allowed_files 使用 B4 canonical/deny policy。路径可披露 iff 在完整 file_inventory、was_parsed=True、content_hash 非 None、精确在 allowed_files、未 ignore 字面前缀且未 protected。不做 stat/read/freshness，不补 hash；磁盘 fresh 属 B4/C 后续门禁。

纯索引输出只是当前内存中的候选元数据，不是已获准发送的结果。C在每次wire之前必须对这些候选的完整文件依赖经B4安全原始hash及整文件秘密筛查；疑似凭据/读取失败/版本变化使对应文件候选不可外传，不能借search或semantic说明绕过片段阻断。内部安全验证不能把源码另行塞进模型免费上下文，也不能公开被阻断文件名或计数。该发送门禁由C真实B4集成验收；纯index测试仍零I/O，不谎称其自己已筛查磁盘秘密。

symbol 必须 definition 且自身 file/anchor 可披露。code node 所有 source_anchors/file_path 可披露；semantic component 的全部自身 anchors 以及全部 code_symbol_ids 对应的 symbol/code record provenance 都必须可披露；缺失 code ID 或递归/无法确定 provenance 则不可披露。对于 code_symbol_ids，优先 symbol record，若同 ID 的 code record 也存在必须一并通过；只存在其中一个可用其已验证 anchors。只靠 child_component_ids 的 component 不展示名称/role/ID，也不递归借孩子制造父来源。无自身 anchors 的 component 即使其 code IDs 有源也保守 unavailable，不发明 anchor。

relation 的所有 anchors 可披露且两个 endpoint 可披露，才可加入模型邻域。code edge.source_anchor=None 不可披露其端点关系；semantic relation 全部 provenance anchors 均需通过；原 evidence 文本留在私有 snapshot 不输出。possible_targets 不输出、不参与 traversal；不根据 CONTAINS 之外的 children 推造 edge。权限过滤在 rank、计数、traversal 和 cap 之前，不能先走 forbidden 中间节点再返回 allowed 后继。

External/Synthetic 无源节点不可从 incident/source node 继承授权，不能输出名称、qualified_name、raw ID、猜测路径或隐藏 relation 类型；unknown、denied、无 anchor seed 对模型统一 `unavailable, (), (), False`。有真实获准 anchors 的 External/Synthetic 可作为源内使用位置元数据，保留原 origin/confidence，证据仅支持该 anchor，不声称库实现源码。严格排除无源节点是首版安全选择，不增加 aliases/外部 allowlist 协议。

total_count 只计授权匹配，truncated 只表示授权结果超过 cap，不能由隐藏文件/边数量决定。不要返回 hidden_count、denied ID、未授权路径或“这个 ID 存在但 forbidden”区别。未检索到关系只表示本索引/权限范围未确认，不等于运行时不存在。

### 5.2 search_symbols

纯 rank 函数（index_tools.py 内私有），完整实现如下：

```python
def rank_terms(query: str, terms: tuple[str, ...]) -> int | None:
    values = tuple(term.casefold() for term in terms)
    if any(value == query for value in values):
        return 0
    if any(value.startswith(query) for value in values):
        return 1
    if any(query in value for value in values):
        return 2
    return None
```

query strip/casefold；空 query 的 symbol rank=0，不列 semantic。非空 symbol terms=name/qualified_name/file_path；semantic terms=display_name/code_name/role 及现有 search.py 七组固定 alias 值，按已有词表复制（BackwardPass、ParameterUpdate、ForwardPass、LossCalculation、ClearGradients、EpochLoop、BatchLoop），不调用模型、正则或扩展同义词网络。symbol confidence=Exact 表示索引 definition，component 原 confidence。

完整扫描 symbol_inventory/component_inventory（周期 checkpoint），仅保留授权匹配。以 `(rank, ref.kind != "symbol", qualified_name.casefold(), anchor.file_path, anchor.start_line, ref.id)` 排序；多 anchor component 选择按 `(path,start_line,start_column or 0,end_line,end_column or 0)` 最小的已授权自身 anchor。同 rank 总序追加原大小写 qualified_name、anchor 完整坐标作 tie-break，避免输入 collection 排列影响结果。

limit strict 1..20；用至多 20 项的小 top-k 有序列表及 total_count，扫描期间不物化 200,000 个 SymbolHit。仅在 rank 有命中且通过权限后投影，临时 terms 逐 record 释放。truncated=`total_count > len(results)`，没有未授权候选 count。极大字符串来自已受 B3 text budget 约束的 immutable records，查询扫描受 deadline checkpoint，不宣称硬 real-time。

### 5.3 get_local_relations

hops strict 0..2，max_nodes strict 1..50，max_edges strict 1..100。seed 是 code 或 semantic，按所属 graph 处理，绝不跨命名空间联图。hops=0 仅合法授权 seed、无边、truncated=False。

使用 incident 完整 accessor；双向 traversal 保留 edge 的原 source/target direction。BFS 按 frontier ID 排序，直接流式扫描快照自有incident tuple的原ordinal顺序，不按degree复制或整批排序；层内候选节点及最终边分别通过下述有界top-k保持既定公开总序，结果和authorized-only truncated不依赖内部扫描次序。cycles/self-loop 保留真实边并去重 key，平行重复边以 original_index 区分。深度 2 只表示最多两个显式邻接步，不能将 uncertain possible_targets/semantic children/code IDs 当作步。

先构建有界候选节点集：seed distance=0，每层遍历已选 frontier 的完整 incident tuples，在授权过滤后保留距离/ID 最小的剩余 node slots；发现其他获准候选则 truncated=True。每层最多保存 50 个选中节点、50 个 frontier/候选 top-k；第 2 层不可经过第 1 层已因 node cap 被省略的节点，已省略候选已使 truncated=True。hops 预算不把合法第 3 层标为截断。

节点集确定后，遍历选中且 distance<hops 的节点完整 incident tuples，只保留两个端点均在返回节点集内的真实授权关系，以 key 去重并保持前 100 小项；总序 `(max(endpoint distances), source.id, target.id, type.value, original_index)`。额外获准关系则 truncated=True。禁止输出悬空 endpoint，禁止先截边导致节点排序受原 collection 任意顺序支配。self-loop 在 hops>0 可返回，hops=0 不返回。节点顺序 `(distance, ref.id)`，seed 必在第一项；未到达 cap 的查询必须完整，不得因 accessor 1,000 上限漏边/漏二跳。

这是输出有界的 BFS，不承诺列出超过 node cap 后省略节点的所有二跳；truncated 明确表示输出缺失。无额外可披露候选时 False，即使原始 graph 有成千上万 forbidden 边。预算耗尽抛 AgentError，不伪造 `ok, truncated=False`。

## 6. B4 adapter 与权威 EvidenceBinding

read adapter 重建严格 B4 ExcerptRequest，调用同一 reader.read_excerpt。不得读原文件、复用全文 bridge、跳过原生句柄/hash/秘密门禁、自动裁剪未获准路径；B4 单次 <=5MB raw/200行/4,000字符保持不变。B4 无 tool/source 累计计量，仍有空 reservation 和 current_state 检查。

成功 SourceExcerpt 返回后，session 检查完整 scope、snapshot file/hash、returned_anchor path、registry.lookup(expected_file_hash=excerpt.content_hash) 的精确 anchor/hash/truncated 与 excerpt 一致；B4 receipt 是本 adapter 自己实际取得的对象，而不是模型提供的 ID 或任意构造 SourceExcerpt。只保存元数据 receipt（evidence_id, scope, requested_anchor, returned_anchor, hash, truncated），不保存 text/bytes；requested_anchor 仅作下述整行支持证明，绝不替代实际 returned_anchor。返回前再 checkpoint，失败 close 清本请求映射和 registry；不返回未经确认的 ID。dispatch 从成功 excerpt 构造 ExcerptToolResult，不能把整个 SourceExcerpt 序列化给模型。

bind_evidence 要求 evidence_id 来自私有 receipt，且仍在同 scope registry；仅 A registry.register 的任意合法结构不够。target=None 返回 source_only。EntityRef 查对应 snapshot record，RelationKey 在完整原序 tuple 以 original_index 查原 record 并核对其 original_index，不接受模型改 endpoint/type/confidence/provenance。目标 unknown/无 anchor/不获准/不能被实际返回片段支持时抛 fixed evidence_unavailable；源码 receipt 仍可按 None 查 source_only，不能把失败目标返回为 bound。

绑定范围采用**完整包含目标的一个真实 provenance anchor**，不以“同文件/有重叠”充当支持。symbols 用其 definition anchor；code node/component 用自身一个真实 source_anchor；code edge 用其 source_anchor；semantic relation 用其真实 source_anchors 中一个，并保留整个 relation provenance 在本地，且所有 anchors/endpoint provenance 先通过授权。一个文件片段不能声称展示多文件全部推导，binding 只表示此 anchor 支持该静态 record 的引用，不验证任意自然语言解释。不可用 endpoint definition 的范围给调用/数据边作证，不把 semantic evidence 字符串当源码。

returned_anchor 必为显式 UTF-8 columns。目标 anchors 列完整时按 `(line,column)` 比较；任一列 None 沿 B4 规则要求目标 start..end 整行覆盖：目标 start=end 所在整行，或连续多行。工具 receipt 请求必须本来覆盖完整目标 anchor，且 `truncated=False`，才允许 None-column 目标绑定；不凭不知道的行长猜 end byte。完整列目标即使 excerpt truncated=True 也仅当实际返回包含完整目标且非零宽才可 bound，binding.truncated 仍保留 True。源零宽 receipt 永远只支持 source_only；无法证明 None-column 整行覆盖时保守 unavailable，可显式重读精确目标范围，不自动重试。

为证明上述 whole-line 条件，私有 receipt 额外保存 B4 requested_anchor（不导出、不保存源码）。当任一目标列 None：adapter 必须验证 requested_anchor 也含缺列、覆盖目标整行且 B4 正常非截断返回；否则 unavailable。这是 B4 真实 reader 选择合同的证明，不允许用户直接填 receipt。完整列关系则仅比较 returned_anchor，不用 requested end 补齐被裁剪区域。

EvidenceBinding.provenance 是原 B3 frozen 白名单 record（允许引用其 immutable 值），静态 confidence 原样；source receipt 的 Exact 不把 Probable/Unresolved CALLS/GRADIENT_FLOW 升级。snapshot_binding 含 root/ignore/epoch/generation，只在本地；关系索引键为 `(collection_kind, original_index)`，完整身份还由该 binding+scope 确定，跨 generation/request 相同 ordinal 不复用。EvidenceRecord.code_edge_id 保持 None，不修改现有 registry record 添假 ID。

私有 mapping 键 `(evidence_id, target)`，模型只能引用它收到的 evidence_id；模型 target/reference 只做查找，不作为 authoritative claim。绑定重复同一 key 幂等，不能重定向旧 key。lookup 检查 current_state/consent/epoch/generation/request，target 权限和 registry receipt 均仍有效；lookup 不执行磁盘 freshness，因此 C 完成/点击仍要 B4 freshness+目标再读验证。D 不得把 bound 当作导航动作直接执行。

容量：receipts/bindings 加 registry.entry_count 合计最多 1,000 项；registry.byte_count 与私有元数据的规范 UTF-8 JSON 计长总计 <=2 MiB；私有 provenance 引用需要将其全部白名单字段/anchors/evidence text 计入逻辑预算，即使物理共享 tuple，不能以“引用不占空间”规避。计长仅逐明确字段白名单，不通用遍历任意对象；计长超限 fixed registry_full，无新增映射。不存在文本缓冲/无限 memo。C 后续在多对话上统一 2 MiB/1,000，全局门禁必须再计 B5 保留量。

## 7. 唯一计次与 C wire 交接

dispatch 顺序：session closed/current_state/ledger 空 reservation 检查 → reserve(tool_calls=1) → 校验 name/payload/严格 schema → 重建 typed request → trusted helper → 终点 checkpoint → 白名单结果。取消/超时或 closed 在开始前拒绝，不计新 attempt；已经接受的非法 name/payload/unknown/denied工具 attempt 全部计一次，无退款。第九次 attempt 因 reserve 失败，不开始查询/reader。response repair/retry/selected-node/source seeds 每次实际工具执行均经 dispatch，不能调用 direct helper 为模型偷偷取免费上下文。

trusted direct purequery/B4 adapter 不计工具；只用于本地组合/测试/验证，不能注册成另一个模型入口。bind/lookup 不重新检索/读取，所以不计工具。周期 checkpoint 空 reservation 只检查取消/deadline，不改 counts。B4 reader 不再预留工具/source，B5 不加第二个账本，不修改 BudgetLedger 源码。

| 场景 | B5/B4 local usage | C 真实 wire 预留 |
| --- | --- | --- |
| 1 次 search dispatch | tool_calls +1；source +0 | 每次发出该 metadata 都计 input tokens |
| 1 次 excerpt dispatch，尚未外传 | tool_calls +1；files/source +0 | 无 wire 则不计源码/模型请求 |
| 同一 excerpt 外传两次（包括重试/历史） | tool_calls 仍 1 | source_characters +=2*len(text)，files 同一路径只计一个；两次 model_requests/input tokens/output envelope |
| 第二次实际 excerpt dispatch | tool_calls +1 | 仅其实际 wire 时再计源码，不因相同内容免计 |
| 非法/unknown dispatch | tool_calls +1；无读源码 | 错误 DTO 后续发送也计 tokens |

C 对每个实际 wire body 重建 source_fragments：所有本次 body 中原始源码出现次数均计，包括 tool results、回答引文、历史/seed 和重试；文件去重但字符不去重。必须从受控 payload provenance 跟踪，不能只计“新的 tool response”。相同 excerpt 在一个 body 出现两次计两份；不可靠判断哪些历史包含源码时用保守受控来源账本上界，不得未知当 0。序列化转义开销进入 UTF-8/token 上界，源码字符用原 Unicode text 字符数；未知 tokenizer 用整个 wire UTF-8 字节+协议开销的保守 token 上界，无法界定拒绝。

C 发送前在自己的授权/任务门禁下重验状态和 freshness，使用**同一 ledger**一次 reserve 包含本 wire 的 source_fragments/input_tokens/model_requests/output_tokens envelope，再开始网络；输出 envelope 不可重复预留同一发送，追加修复/重试是新发送。超 8 files/12k source/20k input/6 model requests/2k output 拒绝发送，不能以 B5 本地无 source charge 解释为免费外传。SDK 重试关闭，ProviderAdapter 不能直接执行 tools。B5 不实现网络/history/SDK/tokenizer，C 的新 ports 必须保留以上事实和 no-text 私有证据界限。

## 8. TDD 风险组与精确 fixtures

约 13 个风险组，用 unittest/subTest 合并等价类；不逐非法字段重复整项目解析。`tests/assistant_tool_fixtures.py` 定义三个 typed builders：`make_index_case(*, symbol_count: int=3, degree: int=3) -> IndexCase`、`make_source_case(root: Path, raw: bytes) -> SourceCase`、`make_session(case: IndexCase | SourceCase) -> ToolSession`。IndexCase 字段 snapshot/consent/scope/ledger/registry/state；SourceCase 另有 reader/anchor。builder 采用 B3 ProjectAnalysis 合成白名单 records，固定合法 binding/64位hash，B4真实 SourceConsent；每例独立 mutable state holder，无全局 session。

IndexCase 固定 ID：code `seed`, `neighbor`, `tail`；source anchors `app.py:1:0..1:5`，每个成功 inventory file 的 line_count 足够且分析 hash 为合成合法 hash；symbol `s0000..` qualified_name `app.s0000..`，definition=True；code edge ordinal0 `seed->neighbor CALLS Probable`、ordinal1 `neighbor->tail DATA_FLOW Unresolved`、ordinal2 self-loop seed CONTROL_FLOW；semantic components `component-a/component-b` anchors 同获准范围、code IDs 对应已有 symbol，relation ordinal0 `GRADIENT_FLOW Probable`。source-less/denied fixtures 在 subTest 中用 builder 参数生成独立 analysis，不改 frozen snapshot。

large-index fixture：1,101 symbol，只有排序靠后的 `s1100` 匹配 `late-match`；degree=1101 时替换默认代码边集合为1,101条seed incident edges，第1,101条（original_index=1100）指向neighbor且允许，前1,100端点在denied.py；另加original_index=1101的neighbor→tail，且此large fixture不加默认self-loop。因此不能漏掉neighbor/tail或泄漏forbidden count。degree=3保持上一段默认三条边。另造全部允许degree=101，验证node cap；两节点101个平行edges测edge cap，不被node cap遮蔽。

真实 SourceCase 用临时 root 写 `b"x = 1\r\n"`（由未来测试的 pathlib 写 fixture，非本计划写代码），通过 parse_python_file/build_snapshot 获取实际 raw hash，B4 native reader 与同 ledger/registry/state。列 fixture 使用 `a😀中z`：byte1..8 对应 😀中，实际 returned_anchor/Monaco由 B4生成。裁剪 fixture 用 ExcerptLimits(max_characters=2) 读取 `abcde`；目标 byte0..5 不能绑定，byte0..2 可以；None-column whole-line 请求被裁剪不可绑定。fixture 项目加入 `raise RuntimeError('must never execute')` 注释/字符串安全样本，分析/读取不 import 或 exec。

| 风险组 | 文件/具体断言 |
| --- | --- |
| R1 严格类型/schema | tools/index tests；bool/float/非法 enum/unknown key/query >2000/limit21/hops3 拒绝；错误不含原输入 |
| R2 完整 inventory/adjacency | snapshot tests；1,101记录/边末项可达，self-loop 一次、tuple不可变、旧limit1000不变、引用<=2E |
| R3 搜索/总序 | index tests；exact/prefix/substring、空浏览、definition filter、七组中英alias、20项/count/truncated、乱序输入同输出 |
| R4 metadata consent | index tests；denied/ignored/protected/hash缺失/failed/case alias、component多文件/缺code IDs均不漏名称/路径/ID/count |
| R5 非源与未知 | index tests；External/Synthetic无anchor种子unknown等价；获准真实anchors仍保持origin，无库实现推断 |
| R6 BFS真实关系 | index tests；0/1/2hop、cycles/self-loop/parallel、incoming、caps、无悬空、uncertain候选/children不成边 |
| R7 纯查询隔离 | index tests；open/stat/scandir/native reader/bridge/view builder/socket/subprocess/metadata访问哨兵不触发 |
| R8 唯一attempt计数 | tools tests；有效/非法/unknown各+1，direct+0，B4+0，seed dispatch+1，第9次不查询，取消/超时不开始 |
| R9 B4真实adapter | tools tests；实际rawhash/returned_anchor与registry一致，秘密/源变化拒绝，无普通读回退，无text保留 |
| R10 证据支持范围 | binding tests；同文件不同范围/仅重叠/零宽/truncated越界/None整行失败；Unicode真实anchor可绑，无sourceID伪造 |
| R11 ordinal/provenance | binding tests；code与semantic ordinal0区分、重复边区分、端点/type/confidence原样、不能endpoint anchor替代edgeanchor |
| R12 私有scope/容量 | binding tests；其他registry注册ID/另session receipt不可绑、epoch/gen/request/撤销失效、满表拒绝、close幂等 |
| R13 C计量交接 | tools tests本地source/files=0；C service未来1read/2wire=1tool/2source、历史/重试/8files/12k边界门禁 |

## 9. 分步执行任务

### Task 1：完整 B3 accessor，先测试后实现

Files: Modify `src/codeviz/assistant/snapshot.py`, `tests/test_assistant_snapshot.py`; Create `tests/assistant_tool_fixtures.py`。

- [ ] test_writer 构造 R2/large fixture（以实际 B3 accepted public builder 生成，不私改 snapshot），添加如下独立断言；缺 API 首先用 hasattr 断言报明确 RED，避免 collection import error。

```python
case = make_index_case(symbol_count=1101, degree=1101)
self.assertTrue(hasattr(case.snapshot, "symbol_inventory"))
self.assertEqual(len(case.snapshot.symbol_inventory), 1101)
self.assertEqual(len(case.snapshot.incident_code_edges("seed")), 1101)
self.assertEqual(case.snapshot.incident_code_edges("seed")[-1].original_index, 1100)
self.assertEqual(len(case.snapshot.symbols(limit=1000)), 1000)
self.assertIsInstance(case.snapshot.symbol_inventory, tuple)
```

- [ ] 主代理运行 `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_snapshot.py -v`；预期新 accessor 明确 FAIL，旧投影测试保持有效。
- [ ] core_implementer 按 §3 添加 getter 与 builder 内不可变 adjacency；现有 tuple 直接返回，不用 limited query拼装；完整 incident 按原 ordinal，不重编号。
- [ ] 同命令 GREEN；补修改原 analysis 不影响 accessor、MappingProxyType底层不可逃逸、预算构建失败无partial publication、unknown空tuple/非法固定错误的 subTests。

### Task 2：严格 DTO 与纯搜索

Files: Create `src/codeviz/assistant/tool_contracts.py`, `src/codeviz/assistant/index_tools.py`, `tests/test_assistant_index_tools.py`；fixtures 仅 test_writer 继续持有。

- [ ] test_writer 添加 R1/R3/R4/R7 测试：固定 fixture query `late-match` 只命中 s1100，乱序原字典 builder 输出相同；21个允许命中取20且count21，新增100个forbidden匹配不改count/truncated；中文alias与英文命中同semantic ID。

```python
case = make_index_case(symbol_count=1101)
result = search_symbols(case.snapshot, case.consent,
                        SearchRequest("late-match"), checkpoint=lambda: None)
self.assertEqual(tuple(hit.ref.id for hit in result.results), ("s1100",))
self.assertEqual(result.total_count, 1)
self.assertFalse(result.truncated)
```

- [ ] 运行 index 定向命令 RED，明确缺 search contract；core 实现 §4 DTO validation、§5.1权限白名单、§5.2完整扫描/top-k/固定rank，不能调用 search_project。
- [ ] 相同命令 GREEN；R4 对多文件component仅授权其中一个仍完全不导出，R7 patches原analysis.metadata/AST访问及I/O为raise，确保 search真纯索引。

### Task 3：两层邻域与非源过滤

Files: Modify `src/codeviz/assistant/index_tools.py`, `tests/test_assistant_index_tools.py`, `tests/assistant_tool_fixtures.py`。

- [ ] test_writer 添加 R5/R6 和 large adjacency：前1100 forbidden，最后允许 seed→neighbor→tail；再全允许cap与101平行edge独立例；未知/无源seed输出相同安全值。

```python
case = make_index_case(degree=1101)
result = get_local_relations(case.snapshot, case.consent,
                            RelationsRequest(EntityRef("code", "seed")),
                            checkpoint=lambda: None)
self.assertEqual(tuple(node.ref.id for node in result.nodes),
                 ("seed", "neighbor", "tail"))
self.assertFalse(result.truncated)
self.assertTrue(all(edge.source in {node.ref for node in result.nodes}
                    and edge.target in {node.ref for node in result.nodes}
                    for edge in result.relations))
```

- [ ] 定向 index 命令 RED；core 实现 §5.3 分层top-k BFS、完整incident、真实endpoint闭包与稳定关系总序；不得以 `code_edges(limit=1000)` 代替。
- [ ] 同命令 GREEN；subTest hops0/1/2、cycles/selfloop、mixednamespace/possible_targets、node/edgecap/truncated，不把隐藏边算缺失；deadline在中途固定失败不返回虚假完整结果。

### Task 4：ToolSession dispatch 与 B4 adapter

Files: Create `src/codeviz/assistant/tools.py`, `tests/test_assistant_tools.py`; Modify `tests/assistant_tool_fixtures.py`。

- [ ] test_writer 真实 B4 source fixture验证 R8/R9，dispatch唯一计次，typed direct zero、unknown/非法 attempt占用一次、取消/state变化/registry关闭无结果；B4修订尚未通过时此集成门禁必须阻止生产接入。

```python
case = make_index_case()
session = make_session(case)
session.search_symbols(SearchRequest(""))
self.assertEqual(case.ledger.usage.tool_calls, 0)
session.dispatch("search_symbols", {"query": ""})
self.assertEqual(case.ledger.usage.tool_calls, 1)
with self.assertRaises(AgentError):
    session.dispatch("execute", {"command": "private-sentinel"})
self.assertEqual(case.ledger.usage.tool_calls, 2)
self.assertEqual(case.ledger.usage.source_characters, 0)
self.assertEqual(case.ledger.usage.files, ())
```

- [ ] tools定向命令 RED；core 实现 §4签名与§7顺序，严格 schema、dispatch不动态getattr、不二次调用dispatch；state/checkpoint沿B4而非读取desktop状态。
- [ ] 真实source dispatch一次断言tool_calls=1、files=()、source_characters=0；B4 read两次安全读取不计两次工具。adapter receipt校验失败close，不记录源/原始异常；哨兵确认无socket/exec/subprocess/history/persistence。
- [ ] 相同定向命令 GREEN；第九attempt无需昂贵I/O，maxfiles/source由Cwire负责的交接断言写入主代理审查清单，不在B5模拟网络称其已通过。

### Task 5：权威绑定、返回范围与私有容量

Files: Modify `src/codeviz/assistant/tool_contracts.py`, `src/codeviz/assistant/tools.py`; Create `tests/test_assistant_evidence_binding.py`; Modify fixture builders。

- [ ] test_writer 添加 R10/R11/R12：真实 reader receipt、完整代码边anchor、semantic原confidence、重复ordinal、不同snapshot/request、另registry结构合法伪证据；每类失败固定evidence_unavailable不echo target。

```python
session = make_session(case)
excerpt = session.read_source_excerpt(ExcerptRequest(case.anchor))
binding = session.bind_evidence(excerpt.evidence_id, RelationKey("code", 0))
self.assertEqual(binding.returned_anchor, excerpt.returned_anchor)
self.assertEqual(binding.content_hash, excerpt.content_hash)
self.assertEqual(binding.provenance, case.snapshot.code_edge_inventory[0])
self.assertEqual(binding.confidence, Confidence.PROBABLE)
self.assertIsNone(case.registry.lookup(excerpt.evidence_id, scope=case.scope,
                   expected_file_hash=excerpt.content_hash).code_edge_id)
```

这里 case 是 source builder 增加 §8 同范围 Probable CALLS 的 SourceCase；所有code endpoints获准且源真实，不复用合成hash与磁盘不一致的IndexCase。

- [ ] binding定向命令 RED；core 按 §6 实现实际receipt-only、完整returned包含、None整行保守证据、key+scope原ordinal映射，绝不改EvidenceRegistry事实语义。
- [ ] 测试 `abcde`裁剪到`ab`：整目标0..5失败，0..2 bound且truncated=True，None整行失败；字节中间边界由B4拒绝，零宽仅source_only；同文件调用位置不同于endpoint定义不可绑定。
- [ ] close幂等/撤销/epoch/gen/request变化lookup拒绝、满容量不增加private mapping、重复key幂等、本地映射不含text/bytes；相同定向命令GREEN。

### Task 6：独立 review、回归与 C 交接

Files: 仅本批已分配文件；主代理另拥有进度更新，reviewer读而不写其他子项目。

- [ ] reviewer独立核对规格§5/6/8、完整大索引、权限先过滤、无源端点、returned_anchor支持、私有scope与计量修订。tests passing不替代授权/来源审查。发现实际缺陷先test_writer RED再core修复/复审，复杂失败交debugger。
- [ ] 主代理检查实际diff和写范围（实施阶段才做），不把B3/B4并行修改算本批成果；本architect任务不运行Git。
- [ ] 主代理在实施阶段运行以下门禁，预期exit0；报告实际counts/skips，未跑平台明确标未验收，不复制历史938等计数。

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_snapshot.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_index_tools.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_tools.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_evidence_binding.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_assistant*.py' -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_search.py -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_navigation.py -v
.desktop-venv/Scripts/python.exe -B -m ruff check src tests
.desktop-venv/Scripts/python.exe -B -m mypy src/codeviz
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

- [ ] Windows native B4 reader集成真实运行；Linux按现有环境跑适用门禁，缺环境或nativeskip不称通过。未改前端不重复其每子任务完整门禁，阶段B/最终交付仍由主代理安排UI/包门禁。
- [ ] C接续端口只适配ToolSession.dispatch/受控excerpt投影和private lookup_binding；C tests/test_assistant_service.py证明1read/2wire计量、真正发送前状态/freshness、seed计次与所有history source上界。C response.py的关系验证从EvidenceBinding.provenance取真实端点/type/confidence；D在新鲜导航门禁后适配RevealTarget，无法图定位则sourceOnly，不猜metadata/root。

## 10. 架构自审与结论

| 要求 | 本计划落点/检查结果 |
| --- | --- |
| §5 三工具/20/2hop50/100/预算 | §4/5/7、Tasks2–4；单一dispatch attempt计次，seeds同路径，C唯一wire累计source |
| §6 权威证据/节点关系/真实范围 | §6、Task5；B4实际receipt/returnedanchor/rawhash，原ordinal与immutable provenance，未造coreedgeID |
| §8 元数据同意/保护/不执行 | §5.1、R4/5/7、Tasks2–4；forbidden计数不泄漏，无源节点不继承授权，无SQL/eval/DSL或网络 |
| 完整索引和有界工作 | §3/5、R2/6、Tasks1/3；>1000测试，tuple+immutable adjacency，O(V+E)空间、扫描deadline |
| 未知/无anchor/无法图定位诚实 | §4/5/6；unavailable或source_only，binding不是导航或freshness证明 |
| 写范围/前置reviews/C适配 | §1/2/9；B3+B4批准后实现，主代理协调B4新计量，不把C port当宽松读取授权 |

自审已检查DTO/方法命名一致、原confidence不被B4Exact覆盖、隐藏metadata不进入结果、非法attempt在schema前计次、directqueries不计次、无旧reader tool/source扣量、tuple大索引不静默截断、privateproof按scope及capacity管理。计划不使用占位产品决定，不宣称本文件即已实施或测试通过。

架构结论：可按此合同推进，无需再确认产品方向；生产接入必须等待B3/B4独立review以及主代理B4计量修订落地。后续实施的审查重点是授权过滤与实际returned范围，而不是仅通过工具结果schema。此次只交付本B5计划，进度和后续执行仍由主代理连续推进。
