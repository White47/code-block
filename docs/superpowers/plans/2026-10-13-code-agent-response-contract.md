# Code Agent C6 Response Contract Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans, subject to repository routing. Main reads this complete contract before dispatching test_writer and core_implementer; reviewer independently reviews implementation. Existing product/design is APPROVED. No product interview or confirmation gate.
>
> **Process amendment (2026-10-15):** process is governed by the repository `AGENTS.md`. Do not load `subagent-driven-development` or `executing-plans`; do not run a per-task spec/quality review pair. `reviewer` runs once at the phase boundary, not per task. Technical requirements below are unchanged.

**Goal:** Deliver one small, independently testable stdlib `response.py` implementing approved C3 evidence authority and C6 answer validation.

**Architecture:** Parse untrusted bytes into frozen values, resolve only request-provided IDs through the actual B5 session and A registry, then project local cards and relation facts. C service owns freshness, disclosure, repair, publication and navigation jobs; validation performs no filesystem/network access.

**Tech Stack:** Python 3.11 stdlib, existing A/B3/B4/B5/C2 contracts, unittest. No SDK, Pydantic, general query engine, AI framework or new interface library.

## 1. Authority and actual ports

Read the approved design `docs/superpowers/specs/2026-10-11-code-understanding-agent-design.md` and runtime plan `docs/superpowers/plans/2026-10-12-code-agent-runtime.md`, especially C3/C6. This refinement does not replace their lifecycle/security contracts. Architect disposition: approved for the narrow vertical slice below; full runtime acceptance is separate.

Actual implementation inspected:

| Module | Existing port / constraint |
| --- | --- |
| `assistant/contracts.py` | `RequestScope(project_epoch, analysis_generation, request_id)`; A `AgentError` accepts only A codes. |
| `assistant/evidence.py` | `EvidenceRecord(scope, anchor, content_hash, confidence, symbol_id=None, code_edge_id=None, semantic_id=None, truncated=False)`; `EvidenceRegistry.lookup(id, *, scope, expected_file_hash)`. Registry is structural, not source authority. |
| `assistant/tool_contracts.py` | `EntityRef(kind: symbol/code/semantic, id)`; `RelationKey(collection_kind: code/semantic, original_index)`; actual `EvidenceBinding`. |
| `assistant/tools.py` | `create_tool_session(snapshot, *, scope, consent, ledger, registry, current_state)`; `session.dispatch(name, payload)`; `bind_evidence(id, target)`; `lookup_binding(id, target)`. Lookup checks owned receipt, consent/state/deadline, authorization and returned-range support. |
| `assistant/snapshot.py` | `snapshot.binding`, `matches_scope`, `get_file/get_symbol/get_code_node/get_component`, `code_edge_inventory/relation_inventory`. Edge identity is original ordinal plus collection, never a core edge ID. No snapshot metadata dictionary exists. |
| `assistant/source_excerpts.py` | `ExcerptRequest(anchor)` and real `SourceExcerpt` with requested/returned anchors, raw content hash and UTF-16 navigation range. Reader/native verification already owns UTF-8 boundary proof. |
| `assistant/config.py`, `provider.py` | `ProviderError` is defined in config and re-exported by provider; unsupported codes silently become `AI_INVALID_INPUT`. Current fixed table lacks `AI_EVIDENCE_INVALID`. |

Future implementation ownership: create only `src/codeviz/assistant/response.py` and `tests/test_assistant_response.py`. Reuse `tests/assistant_tool_fixtures.py` without modifying it. Main coordinates the single C2 error-table addition in `config.py` with its owner before integration; this architect task changes only this new plan, with no Git operations. No UI component, billing, service backend or navigation delivery is included.

## 2. Exact frozen values and signatures

All following new DTOs use `@dataclass(frozen=True, slots=True)`. Tuple collections are recursively checked at construction and again at public verifier entry; reject bool-as-int, subclasses of input DTOs, invalid enums, invalid UTF-8/surrogates and mutable nested substitutes. Freeze copied dictionaries with `MappingProxyType`; never retain caller-owned mappings. JSON projection explicitly maps snake_case to the approved camelCase fields, converts enums to their values and tuples to arrays; never serialize resolver/snapshot/proof via `asdict`.

```python
from collections.abc import Mapping
from typing import Literal
from codeviz.core import Confidence, EdgeType
from codeviz.assistant.contracts import RequestScope
from codeviz.assistant.evidence import EvidenceRecord, EvidenceRegistry
from codeviz.assistant.snapshot import AnalysisSnapshot
from codeviz.assistant.tool_contracts import (
    EntityRef, RelationKey, EvidenceBinding as B5EvidenceBinding,
)
from codeviz.assistant.tools import ToolSession

SectionKind = Literal['summary', 'entry', 'steps', 'branches',
                      'relations', 'evidence', 'unknowns']
Basis = Literal['static', 'inference', 'unknown']
Category = Literal['call', 'control', 'data', 'structure']
ChosenTarget = EntityRef | RelationKey | None

# Each declaration below is frozen/slots, in this exact field order.
class ModelClaim:
    text: str
    basis: Basis
    evidence_ids: tuple[str, ...]
    relation_evidence_id: str | None
class Section:
    kind: SectionKind
    claims: tuple[ModelClaim, ...]
class ModelAnswer:
    sections: tuple[Section, ...]
class ResolvedEvidence:
    record: EvidenceRecord
    proof: B5EvidenceBinding
class EvidenceCard:
    evidence_id: str
    label: str
    confidence: Confidence
    truncated: bool
    source_available: bool
    graph_available: bool
    unavailable_reason: Literal['navigation_check_required', 'source_only']
class RelationFact:
    evidence_id: str
    source_id: str
    target_id: str
    type: EdgeType
    confidence: Confidence
    candidate_targets: tuple[str, ...]
    category: Category
class Answer:
    sections: tuple[Section, ...]
    evidence: tuple[EvidenceCard, ...]
    relation_facts: tuple[RelationFact, ...]
    incomplete: bool
    coverage_gaps: tuple[str, ...]
    checked_at: str
class ValidationIssue:
    code: Literal['invalid_schema', 'unprovided_id', 'invalid_evidence',
                  'relation_required', 'invalid_relation', 'static_requires_ids']
    section_index: int | None
    claim_index: int | None
class ValidationResult:
    answer: Answer | None
    issues: tuple[ValidationIssue, ...]

def parse_model_answer(payload: bytes) -> ModelAnswer: ...

class ActualEvidenceResolver:
    def __init__(self, session: ToolSession, registry: EvidenceRegistry,
                 snapshot: AnalysisSnapshot, scope: RequestScope,
                 provided: Mapping[str, ChosenTarget]) -> None: ...
    def resolve_evidence(self, evidence_id: str) -> ResolvedEvidence: ...

def validate_model_answer(
    model: ModelAnswer, *, resolver: ActualEvidenceResolver,
    checked_at: str, incomplete: bool = False,
    coverage_gaps: tuple[str, ...] = (),
) -> ValidationResult: ...
```

These are proposed C signatures, not claimed existing B APIs. Production validator accepts the concrete resolver, not an arbitrary callback returning an `EvidenceBinding`. It checks exact concrete type; composition requires an actual exact `ToolSession`, registry, snapshot and scope. Trusted Python hosts are the boundary (no claim of security against monkeypatching Python internals). Real factory receipts, not typed mock DTOs, establish production authority. No alternate resolver protocol is required.

`checked_at` is supplied by service's successful freshness check as an RFC3339 UTC string, maximum 64 chars; parser/verifier does not read a clock. Coverage gaps are local fixed reason strings, at most 20, each at most 200 chars; incomplete is a real bool. Answer/card flags describe evidence eligibility, not issued navigation actions: source_available=True for validated source; graph_available=True only for supported bound target. Both still require separate C6 navigation checks, and no buttons/actions activate before service publishes Completed. Source-only uses reason `source_only`; bound uses `navigation_check_required`. Local label is `file_path:start_line-end_line` from A record. Card confidence comes from proof for bound facts and A for source-only.

## 3. Strict model protocol (self-contained)

Top-level shape is exactly `{"sections":[...]}`. Seven sections exactly once, in this order: summary (功能摘要), entry (入口与前提), steps (执行步骤), branches (条件/异常/返回路径), relations (调用与数据关系), evidence (证据), unknowns (未确认事项). Each section has exactly `kind` and `claims`. Each claim has exactly `text`, `basis`, `evidenceIds`, `relationEvidenceId`; all four are required. No defaults, extras or repairs in parser. Empty claims arrays are permitted; missing sections are not.

Example full valid envelope without invented evidence:

```json
{"sections":[{"kind":"summary","claims":[{"text":"Unable to confirm the function without evidence.","basis":"unknown","evidenceIds":[],"relationEvidenceId":null}]},{"kind":"entry","claims":[]},{"kind":"steps","claims":[]},{"kind":"branches","claims":[]},{"kind":"relations","claims":[]},{"kind":"evidence","claims":[]},{"kind":"unknowns","claims":[]}]}
```

Parser algorithm: require exact bytes and 1..65536 bytes before decode; strict UTF-8, no BOM; reject trailing content, Markdown fences, comments and JSON extensions. Use `json.loads` with `object_pairs_hook` rejecting duplicate keys at every object and `parse_constant` rejecting NaN/Infinity. Convert decode/JSON/type/recursion failures to fixed response error without raw payload or exception chain. Schema's fixed nesting bounds accepted structure; no permissive arbitrary JSON retained. Enforce <=20 claims per section and <=60 total; text 1..2000 Unicode characters, non-whitespace, valid UTF-8; permit ordinary newline/tab prose but reject other controls. Basis exactly static/inference/unknown. evidenceIds is an array of <=8 unique nonempty strings each <=512 printable characters; relationEvidenceId is null or the same bounded string type. If non-null it must also occur in evidenceIds (one clear source/card association). Reusing a valid ID across claims is allowed. DTO entry checks repeat these limits to prevent a hand-constructed typed bypass.

No root URI, providedIds, target, confidence, links, filename, coordinates, endpoints or navigation fields are accepted at any schema level. Literal prose remains inert text; it cannot become a link/action or local identifier. Unknown basis is always visibly labeled unable to confirm; preserve text as untrusted explanation, never interpret its wording as authorization. Model prompt explicitly instructs unknown claims to state inability to confirm. No natural-language classifier claims to prove compliance/truth.

All parser failures raise local `ResponseError(ValueError)` with `code='AI_EVIDENCE_INVALID'` and fixed message `Assistant evidence validation failed.`. Do not call current `ProviderError('AI_EVIDENCE_INVALID')`: it would silently change the code. Main's coordinated table addition permits service to translate ResponseError to that same ProviderError code later. Verifier issues contain only the six literals above and indexes, never source, IDs, exception text, provider details or model text. At most one issue per claim plus one envelope issue, capped at 61, deterministic section order. Any issue => answer=None (no partial actionable success). Service may perform one budgeted repair; retry/failure/evidence retention is outside response.py.

## 4. Authority verification and semantic limits

Service constructs the request's immutable ID -> chosenTarget table only from authorized local retrieval before any ID is provided to the model. The response module neither scans the registry to populate it nor binds targets during validation. Cap table at 1000 entries and canonical logical JSON representation at 2 MiB; main service additionally charges it with receipts/provenance to the existing global budget. Never copy source text into it. Duplicate proposed associations for an ID must be rejected before a dictionary overwrites them; same target reuse is idempotent, different target is forbidden. For another fact issue another actual read/receipt through dispatch, consuming existing eight tool-attempt budget; no proof expansion loop.

For every distinct referenced ID, in first-occurrence order (<=480 references before deduplication):

1. Require membership in this request's copied provided table. Do not fall back to registry or old history IDs.
2. Invoke actual `session.lookup_binding(id, chosenTarget)`. This is authoritative for issued receipt, lexical authorization, requested/returned range containment, native UTF-8 proof already established by B4, state/deadline and retained binding; no private `_receipts` inspection or new read.
3. Invoke `registry.lookup(id, scope=scope, expected_file_hash=proof.content_hash)`. Require exact proof type and rerun structural validation; check proof ID/scope, snapshot binding, snapshot.matches_scope(scope), chosen target, and A scope/hash/truncated agreement. Compare A SourceAnchor and proof AnchorRecord by all five coordinate fields, not cross-class equality. snapshot.get_file(path) must exist, parsed, same raw hash, and contain returned line range. A confidence is Exact and symbol/code_edge/semantic IDs all None, as actual B5 receipt requires.
4. Source-only: target/provenance both None, status source_only, proof confidence Exact; no relation/graph eligibility. Bound: non-null target/provenance, status bound, exact namespace/type/membership in current snapshot. Entity symbol uses get_symbol plus definition=True; code/semantic use their own lookups. Relation code/semantic uses corresponding inventory and matching original_index, not endpoint guessing. Check complete immutable provenance equality and original confidence (symbol proofs are Exact). B5 lookup remains the returned-range/consent authority; merely comparing anchors cannot substitute for its receipt check.
5. Cache verified values only within this validation invocation; do not retain across requests or freshness boundaries. Lower A/B errors become fixed invalid_evidence/invalid_relation issues; service independently preserves cancellation/stale/deadline terminal policy and must not repair after those conditions.

Relation reference must be bound RelationKey; EntityRef/source-only fail even when source overlaps. Build endpoints/type/confidence solely from actual CodeEdgeRecord/RelationRecord. Require non-null relationEvidenceId for any static claim in relations; free inference/unknown without it is explicitly unconfirmed and emits no RelationFact. Claims with any supplied relationEvidenceId always validate it regardless of basis. All static claims require at least one ID, including summary/entry/evidence; key facts cannot bypass through section choice.

Free prose cannot prove author intent, business purpose or runtime order. Conservatively downgrade *all* model static prose to inference after valid citation checks; retain unknown/inference unchanged. This avoids pretending a keyword filter proves truth and is allowed by approved C6 downgrade policy. Locally derived RelationFact is a separately labeled static-analysis fact with its original confidence, never confirmation of model wording. No structural text parser, execution-order inference or business-intent truth checker is introduced. Even Exact source never raises Probable/Unresolved relation or component confidence.

Exhaustive category map from actual EdgeType: CALLS -> call; CONTROL_FLOW/BRANCH_TRUE/BRANCH_FALSE/LOOP_BACK -> control; READS/WRITES/PASSES_TO/RETURNS_TO/DATA_FLOW/GRADIENT_FLOW -> data; CONTAINS/IMPORTS/EXPORTS/INHERITS/IMPLEMENTS -> structure. Reject an unmapped future enum rather than guessing. GRADIENT_FLOW retains Probable/Unresolved and represents semantic evidence, not observed runtime tensor/gradient flow.

Uncertain code CALLS: candidates originate only in verified CodeEdgeRecord.possible_targets, preserve snapshot order and original confidence. Actual B5 `_target_proof` refuses binding unless *all* candidate nodes are authorized; pure `get_local_relations` exposes/traverses actual source/target only and has no candidate field. Response never adds candidates to model tool payload or chooses a candidate. Local RelationFact includes only those verified authorized candidates, with exact code namespace/current snapshot membership; any missing/mismatched candidate fails the relation. Never salvage a denied binding by filtering its raw snapshot candidates. Deduplicate projected facts/cards by ID; distinct relations sharing a range still need distinct receipt IDs. Bound local output to 480 cards/facts, 1000 candidate IDs per fact and 2 MiB canonical UTF-8 JSON overall; check before accumulating, fail with invalid_evidence rather than silently truncating candidate facts. Global service retention limits still apply and bridge polling must respect its own bounded envelopes.

## 5. Service entry and publication boundary

Approved request context remains the runtime discriminated union: question `{kind:'question'}`; node `{kind:'node',viewKind,visibleNodeId,rootId,detailSourceId}`; source `{kind:'source',filePath,anchor,mode:'function'|'file'}`. Exact epoch/generation/request scope and backend context mapping apply. These are request fields, never model response keys. Grouped UI IDs are not symbol IDs.

If service names a new internal `RootSourceFragment`, its exact frozen fields are `evidence_id: str`, `relative_path: str`, `text: str`, `returned_anchor: AnchorRecord`, `truncated: bool`: project the existing ExcerptToolResult from an actual `read_source_excerpt` dispatch, not frontend drafts. It is source-bearing retrieval material, not an authority object or additional response DTO. No `providedIds` field or automatic registry-table extraction. Service explicitly verifies/binds the single chosenTarget before adding the ID to its provided table and emitting the fragment; repeated source occurrences count toward wire budgets. Response.py need not define/import this service value to ship independently.

2026-10-18 internal service admission decision: add frozen `RootRetrieval(anchor: AnchorRecord, target: EntityRef|RelationKey|None)` and optional `JobRequest.root_retrieval=None`. Include its canonical anchor/typed-target representation in the replay digest. Only the trusted saved-context adapter selects this value; neither a model nor the public start DTO supplies a chosen target. Service owns each job's real `ToolSession`, registry and explicitly built provided-ID map, using the admitted snapshot, explicit `SourceConsent` and existing budget ledger. A source-free job still needs a real empty session/map and may publish only an answer whose citations pass that resolver. This is an internal integration seam, not a new model tool, provider protocol or source authorization path.

Evidence lifetime is distinct from provider/worker cleanup. Completed navigation ultimately requires a `RetainedEvidenceContext` holding the original session, registry and immutable provided map without excerpts, transferred to the conversation owner with all logical evidence/binding/provenance metadata charged to the 2 MiB/1,000-ID cap. Clear/revoke/eviction must deny new borrowers, cancel existing navigation jobs, and close the last reference outside locks. Re-reading under a new receipt ID does not preserve the original request's authority. A service-response-only slice may close its job session after validation, but its navigation entries must remain disabled until that ownership/retention path is implemented; it must not claim C6 click acceptance.

Service checks current publication/consent/config/cancel/freshness before send and final publication, supplies checked_at/coverage, owns one repair and acquired-evidence retention. Parser/resolver/verifier never open files, perform freshness scans, dispatch tools, bind new evidence, invoke provider or navigate. Existing B5 lookup checkpoints are in-memory callbacks/ledger checks; production supplies those existing bounded state callbacks. Separate C6 click job freshly verifies and builds actual source/graph navigation with frontend guards. No answer modifies graph/layout/cache or emits model-derived coordinates/links.

## 6. Compact failure-first execution

Use three parameterized risk groups in one unittest module, not a hundred individual methods. Main can run this slice alongside independent C backend jobs once the C2 table coordination is assigned; no SDK/service implementation dependency for parser or real B5 fixture validation.

- [ ] test_writer creates schema risk group: encode the complete seven-section example with json.dumps(...).encode(); parameterize missing/reordered/duplicate sections, absent/extra keys at each level, duplicate JSON keys, invalid UTF-8/BOM/constants/deep JSON, 65536/65537 byte boundary, text 2000/2001, 20/21 section and 60/61 aggregate claims, 8/9/duplicate IDs, relation ID not in evidenceIds, forbidden target/link/confidence keys, hand-built mutable/subclass DTOs. Assert fixed errors, never raw model payload. Run `python -m unittest discover -s tests -p test_assistant_response.py -v`; expected executed failure for missing response APIs, not collection errors.
- [ ] core_implementer implements frozen DTOs/parser only; rerun that group until green. No implementation of tests that merely mirror helpers; boundaries must exercise public entry points.
- [ ] test_writer creates receipt/authority risk group with the actual fixture path below. Parameterize valid source-only, EntityRef namespaces, code RelationKey and semantic gradient; unprovided valid-registry ID, foreign request, registry-only forged record, wrong session, closed registry/session, chosenTarget replacement, wrong collection/ordinal, missing node, hash/scope/binding/provenance/status/returned-range/truncated corruption on each side. Controlled negative fixture corruption may mutate internals only after factory-issued positive baseline; never use forged fixtures as evidence of successful authority. Every invalid result has answer=None and no actions.
- [ ] core_implementer adds concrete resolver and validator; B5 receipt/range checks remain the authoritative helper. Rerun focused tests.
- [ ] test_writer adds semantics/capacity risk group: one ID/two real relations refuses reassignment; separately dispatched receipts work; entity/source-only cannot prove relation; Exact source with Probable CALLS/Unresolved data/Probable gradient stays unchanged; authorized uncertain candidates retained, denied candidates cause B5 binding failure and zero exposure; namespace collisions; malicious prose stays inert/inference; empty-ID static fails; provided table/global local cap and typed DTO bypass; budget exhausted ninth dispatch cannot obtain another receipt. Keep unrelated service freshness/native navigation tests out of this slice.
- [ ] core_implementer finishes local projections; run focused suite plus existing `test_assistant_evidence_binding.py`, `test_assistant_tools.py`, `test_assistant_index_tools.py`, `test_assistant_provider.py` using unittest discovery per filename; run `python -m ruff check src/codeviz/assistant/response.py tests/test_assistant_response.py` and project `python -m mypy src/codeviz`. Main inspects diff; reviewer independently checks authority, confidence, namespace, candidate leakage, parser limits and prose downgrade, not just green tests. No Git action is required by this plan.

Concrete positive fixture setup to embed in the receipt group (all receipt creation precedes validation):

```python
import tempfile
from pathlib import Path
from assistant_tool_fixtures import make_source_case
from codeviz.core import Confidence
from codeviz.assistant.response import ActualEvidenceResolver
from codeviz.assistant.tools import create_tool_session
from codeviz.assistant.tool_contracts import RelationKey

with tempfile.TemporaryDirectory() as directory:
    case = make_source_case(Path(directory), b'value = 1\n')
    session = create_tool_session(
        case.snapshot, scope=case.scope, consent=case.consent,
        ledger=case.ledger, registry=case.registry, current_state=case.state,
    )
    excerpt = session.dispatch('read_source_excerpt', {
        'file_path': 'app.py', 'start_line': 1, 'end_line': 1,
    })
    target = RelationKey('code', 0)
    proof = session.bind_evidence(excerpt.evidence_id, target)
    resolver = ActualEvidenceResolver(session, case.registry, case.snapshot,
                                     case.scope, {excerpt.evidence_id: target})
    resolved = resolver.resolve_evidence(excerpt.evidence_id)
    assert resolved.proof == proof
    assert resolved.record.confidence is Confidence.EXACT
    assert resolved.proof.confidence is Confidence.PROBABLE
```

The snake_case dispatch keys above are the actual B5 schema, unlike camelCase bridge DTOs; source range covers synthetic fixture anchor bytes 0..5. For source-only/entity/semantic use another dispatch receipt and `None` / `EntityRef('code','seed')` / `RelationKey('semantic',0)`. Configure uncertain candidates via `source_case_with(..., configure=...)` before build_snapshot; do not invent published snapshot fields. Fixture native receipt success is mandatory on supported Windows/Linux; report unsupported platform explicitly, never replace it with an accepted forged proof.

## 7. Architect self-check and integration conditions

- [x] Approved seven sections, exact model top shape, bounds, all required keys, strict duplicate rejection and fixed failures are explicit.
- [x] ARecord + actual B5 EvidenceBinding alias; actual session lookup and registry lookup; one immutable chosen target; real receipt fixture; no invented edge IDs or snapshot metadata.
- [x] Source-only cannot prove relation/graph; source Exact cannot lift business/static confidence; original EdgeType/category/candidates derive only from authorized immutable fact.
- [x] Intent/order prose is explicitly inference; no claim that citation proves language truth. No model candidate selection, links or coordinates.
- [x] No filesystem/network in validator; freshness/send/publication/click checks remain service contracts; B3 provenance/B5 range and authorization remain mandatory.
- [x] Only this new plan is authored now; implementation/tests/other docs/Git are untouched. Future test_writer/core_implementer/reviewer ownership is explicit.

Integration condition, not product blocker: main coordinates adding the already approved fixed `AI_EVIDENCE_INVALID` message to C2 config's error table; local ResponseError preserves the code meanwhile. No remaining architecture blocker for this independent response slice.
