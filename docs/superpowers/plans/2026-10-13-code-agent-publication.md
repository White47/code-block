# Code Agent C3 Publication Execution Plan

> **For agentic workers:** Read the technical content of this document and approved runtime C3/C7 before execution.
>
> **Process amendment (2026-10-15):** Process is governed by the repository `AGENTS.md`. Where this plan and `AGENTS.md` disagree about process, `AGENTS.md` wins. Do **not** load `subagent-driven-development` or `executing-plans` for this plan, and do not run a per-task spec/quality review pair. Routing for this slice: `core_implementer` owns implementation, `test_writer` may author bulk fixture tests, and `reviewer` runs **once, at Task 6**. Do not dispatch `final_auditor`.
>
> Every technical requirement below — file ownership, invariants, acceptance criteria, and the fail-closed mirror ordering — is unchanged and must still be met.

**Goal:** Publish one immutable AssistantContext and invalidate it before existing analysis, project, policy, or source mutations, without importing or executing an assistant service or SDK.

**Architecture:** AppController owns the publication object and a dedicated short lock. Existing desktop operations remain serialized by DesktopBridge; snapshot construction uses that trusted operation boundary outside the publication lock. Internal pre-write hooks close the gap inside SourceEditingService and ProjectExplorerService; a bounded trusted mirror port reserves the approved publication-to-service transaction for later integration.

**Tech Stack:** Existing Python 3.11+, stdlib frozen dataclasses, MappingProxyType, threading locks, unittest, and delivered B3 AnalysisSnapshot. No dependency or native integration changes.

---

## Authority and phase boundary

This refines `docs/superpowers/plans/2026-10-12-code-agent-runtime.md`, COMPLETE C3 and C7, without superseding them. C4's lock, cancellation and bounded-job rules constrain the future mirror port. B3/B4/B5 are accepted. C2 SPEC/QUALITY is complete; C6 SPEC passed and QUALITY is ongoing. Response and isolated SDK/native work have separate owners. These facts authorize offline publication work, not production assistant activation.

The deliverable for this slice is controller-owned immutable publication plus hooks and tests. There is no service/jobs module, provider factory, model request, SDK import, consent admission, tool adapter, new assistant bridge endpoint, API whitelist change, frontend change, background thread, executor, async job or native lifecycle integration. Runtime Task 3's B retrieval/evidence adapters and later C4/C5/C6/C7 work remain separate. A published snapshot is local accepted analysis, not permission to disclose metadata/source or proof of current disk freshness.

Do not change the approved runtime/spec, C2/C6/SDK files, dependencies, progress claims or unrelated dirty files. Execution does not require Git, network, secrets or `.codex` inspection. Parent review must read the complete plan; this document is not implementation acceptance.

## Current implementation facts and exact ownership

Read before executing:

- `src/codeviz/ui/controller.py`: `__init__`, `open_project`, `analyze`, `save_source_document`, `sanitize_view_state`, `view`, `detail_view`, `structure_namespace`.
- `src/codeviz/ui/web_bridge.py`: `__init__`, `_open_project`, `set_analysis_settings`, `_publish_session`, `_call`, `_write_call`, `_session_snapshot`, `analyze`, `get_view`, `get_detail_view`, `_file_mutation`, `_project_files_changed`, explorer methods and `save_source_document`.
- `src/codeviz/ui/source_editing.py`: project binding, `_project`, `_relative`, `_read`, `_verify`, COMPLETE `save` including native handle validation, temporary-file creation and replacement.
- `src/codeviz/ui/project_explorer.py`: project binding, COMPLETE `create_entry`, `prepare_operation`, `apply_operation`, destination checks and uncertain-mutation error handling.
- `src/codeviz/assistant/snapshot.py`: `SnapshotBinding`, `build_snapshot`, inventories and typed immutable records; `tool_contracts.EntityRef` supplies namespace-qualified entity identity.
- Minimal view/navigation facts: `core/model.py` VisibleNode, `views/builder.py`, `views/project_structure.py`, `views/architecture_structure.py`, `views/structure_identity.py`, `navigation.py`.

Actual gaps: controller open binds SourceEditingService with its own generated epoch; bridge `_open_project` subsequently generates another epoch and rebinds both services. `_publish_session` publishes a six-element display tuple, not assistant identity. Controller save and bridge `_project_files_changed` mark Outdated only after mutation. Explorer create/apply perform preflight and native mutation inside the same method; prepare creates a token but does not write project files. Source save performs disk/native checks before `mkstemp`, then more checks after writing the temporary file. `plan_reveal` and StructureResolver use mutable collections/metadata and cannot be called at assistant start.

Future implementation ownership:

| File | Change |
| --- | --- |
| Create `src/codeviz/ui/assistant_context.py` | Frozen publication/projection/identity types and trusted mirror protocol only; no controller/service import at runtime. |
| Modify `src/codeviz/ui/controller.py` | Own publication, nonblocking capture, invalidation/CAS commit, authoritative epoch, analysis/cache acceptance and view projections. |
| Modify `src/codeviz/ui/web_bridge.py` | Bind explorer to controller epoch, pass project ignore policy into controller open, policy/file hooks, capture identity pair in existing session snapshot. |
| Modify `src/codeviz/ui/source_editing.py` | Optional internal per-save pre-write hook at the validated write boundary. |
| Modify `src/codeviz/ui/project_explorer.py` | Optional internal per-create/per-apply pre-write hook; prepare unchanged. |
| Create `tests/test_assistant_publication.py` | Frozen values, capture, CAS, mirror and publication/view barriers. |
| Extend `tests/test_ui_controller.py`, `tests/test_ui_web_bridge.py`, `tests/test_source_editing.py`, `tests/test_project_explorer.py` | Tests at each real operation boundary. |

The small UI-owned value module avoids a controller-to-service-to-controller cycle and keeps offline UI imports independent of provider code. Use TYPE_CHECKING for AnalysisStatus and ProjectAnalysis annotations. Do not move AnalysisStatus or existing core/view types, re-export through assistant package initialization, or create a generic publication framework.

## Exact proposed internal types and ports

These are NEW C3 implementation contracts, not APIs claimed to exist today. All dataclasses below are frozen and slotted. Collections are tuples or fresh privately owned dictionaries wrapped in MappingProxyType; never wrap a caller-owned dictionary. Mapping keys/values and every nested record must be immutable. No AST, ParsedFile, ProjectAnalysis, mutable graph, VisibleGraph, ViewState, metadata dictionary, document text, callback or service reference belongs inside AssistantContext.

```python
# assistant_context.py; AnalysisStatus imported only under TYPE_CHECKING
@dataclass(frozen=True, slots=True)
class ViewNodeKey:
    view_kind: ViewKind
    root_id: str | None
    detail_source_id: str | None
    visible_node_id: str

@dataclass(frozen=True, slots=True)
class NodeContext:
    targets: tuple[EntityRef, ...]
    source_anchors: tuple[AnchorRecord, ...]
    source_only: bool
    truncated: bool
    reason: str | None  # fixed internal code, never node metadata/display prose

@dataclass(frozen=True, slots=True)
class NavigationFact:
    source_anchors: tuple[AnchorRecord, ...]
    visible_locations: tuple[ViewNodeKey, ...]

@dataclass(frozen=True, slots=True)
class AssistantContext:
    publication_revision: int
    project_epoch: str | None
    analysis_generation: int | None
    project_key: str | None
    root: Path | None
    ignore_paths: tuple[str, ...]
    status: AnalysisStatus
    eligible: bool
    snapshot: AnalysisSnapshot | None
    navigation_facts: Mapping[EntityRef, NavigationFact]
    context_map: Mapping[ViewNodeKey, NodeContext]

@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    publication_revision: int
    project_epoch: str | None
    analysis_generation: int | None
    project_key: str | None
    root: Path | None
    ignore_paths: tuple[str, ...]
    status: AnalysisStatus
    eligible: bool

class PublicationMirror(Protocol):
    def replace_current(self, identity: PublicationIdentity) -> None: ...
```

Controller fields are exactly `_assistant_context`, `_assistant_publication_lock`, `_assistant_publication_mirror` (None by default). Existing `analysis`, `project_root`, `ignore_paths`, `status` remain ordinary operation state, not alternate assistant identity stores. Do not add a writable `_project_epoch`, independent assistant generation, or mutable assistant maps. The candidate/accepted analysis object is a local commit argument, never a retained worker-facing field.

Add only `assistant_publication_mirror: PublicationMirror | None = None` as a keyword-only AppController constructor argument after the existing cache/ignore arguments. There is no mirror registry, setter, runtime discovery or DesktopBridge service construction. Define the local `PublicationError(ValueError)` with fixed `code`/message pairs for `AI_ANALYSIS_BUSY` and `AI_ANALYSIS_REQUIRED`; use the latter for failed internal publication construction, without treating it as an ordinary analysis failure or exposing raw exceptions.

Controller ports:

```python
def capture_assistant_context(self) -> AssistantContext: ...
@property
def project_epoch(self) -> str | None: ...
@property
def analysis_generation(self) -> int | None: ...
def _assistant_invalidate(self, *, status: AnalysisStatus,
                          root: Path | None, ignore_paths: tuple[str, ...],
                          rotate_epoch: bool = False) -> AssistantContext: ...
def _assistant_commit(self, expected: AssistantContext,
                      candidate: AssistantContext,
                      accepted_analysis: ProjectAnalysis) -> bool: ...
def _assistant_publish_accepted(self, expected: AssistantContext,
                                accepted_analysis: ProjectAnalysis) -> bool: ...
def _assistant_publish_view(self, expected: AssistantContext,
                            accepted_analysis: ProjectAnalysis,
                            graph: VisibleGraph, *, root_id: str | None,
                            detail_source_id: str | None) -> bool: ...
```

`capture_assistant_context` tries the dedicated lock with `blocking=False`, returns one object and releases immediately. Contention raises a local fixed-code publication error `AI_ANALYSIS_BUSY`; do not use A AgentError for an unsupported AI code. The two read-only properties derive from capture, with no setters. Consumers needing BOTH fields must capture once, not read the properties separately. Capture itself returns ineligible contexts; future admission decides required/busy/stale from that context. It never takes bridge/source/service locks, builds facts, reads disk, calls a clock, imports SDKs or waits for analysis.

Initialize revision=0, epoch/generation/key/root=None, ignores=(), NotAnalyzed, eligible=False, snapshot=None, empty frozen maps. First valid project open produces generation=1; every identity-changing invalidation increments prior generation (None treated as zero). Revisions increment on EVERY replacement, including ineligible status completion and eligible view-map refresh. Analysis completion does not increment generation again. Same-root valid reopen rotates epoch. View refresh changes revision only. Use UUID hex for epoch and the canonical absolute root string `os.path.normcase(os.path.normpath(str(resolved_root)))` for project_key; compute key/UUID before taking the publication lock. These are identity values, not disk freshness checks.

This canonical-root choice replaces the proposed SHA256 partition key and is the minimal match to actual C2 `consent._project`: ConsentStore requires an absolute, normcase/normpath-canonical root string, not a digest. Context root remains the already resolved Path; project_key is its canonical comparison/consent identity. Future C2 composition passes `ctx.project_key` unchanged to actual `ConsentStore.grant/get/revoke(project_key=...)`, subject to C2's existing validation and explicit consent; this slice invokes none of those methods and creates no grants. Do not change C2 validation or B contracts. Actual B3 SnapshotBinding has `project_root`, NOT `project_key`: construct it with `project_root=str(ctx.root)`, `ignore_paths=ctx.ignore_paths`, `project_epoch=ctx.project_epoch`, `analysis_generation=ctx.analysis_generation`, and compare its project_root to `str(ctx.root)` on commit. Do not substitute the canonical consent spelling for the resolved snapshot root or invent a SnapshotBinding.project_key field. Both absolute root representations and the complete context/binding remain private backend values, excluded from model-wire payloads, tool disclosures and newly added public session identity fields; public epoch/generation are opaque identity, not root disclosure.

`_assistant_invalidate` clears snapshot and BOTH projections, installs an ineligible replacement and mirrors that same identity within one transaction. Ordinary save/file mutation uses same root/ignore/epoch and Outdated if analysis exists, otherwise NotAnalyzed. Do not increment again from the existing after-success notification. Completion/failure may replace status/actual root/ignore with another ineligible object at the SAME generation and epoch; never restore eligibility by copying an old object.

## Lock order and the reserved future service seam

Approved order remains existing trusted operation serialization -> publication -> future service state. Source save also holds its existing source RLock before its trusted pre-write invalidator enters publication. No publication section may acquire bridge/source locks; no service state section may acquire publication/bridge/source locks. Candidate construction, dictionary copying/freezing, hashing, disk checks, native operations, clocks, arbitrary callbacks, logging and error formatting all happen outside publication/service locks.

The only under-publication-lock call is the explicitly trusted `PublicationMirror.replace_current`, if attached by application composition. This is not an extension/event-listener API. Its contract is: acquire only service state lock, replace one immutable identity slot, revoke permits and set cancelRequested for affected admitted jobs (global admitted cap 2), and record one coalescing pending loop-closure revision slot. Do not scan conversations/evidence/history, invoke external callbacks, get clocks, perform I/O, schedule/await asyncio, load modules, wait for teardown or report cancellation confirmed. Older active jobs fail the revision guard even when a later eligible publication overwrites the slot. Completed-result stale visibility is derived from identity mismatch, with bulk cleanup outside the transaction.

The pending closure slot is the bounded in-memory enqueue, not resource-release confirmation. Future C4 composition drains it and wakes the worker loop OUTSIDE publication/service locks; all dispatch guards already reject replaced identity synchronously. That future wiring must prove gate closure/release before cancellation confirmation. This slice adds no loop, scheduler or real service implementation. Default None has no callback. Tests may attach only a trusted bounded fake via constructor injection; attachment is fixed before operations, not hot-swappable user input.

The trusted mirror must not raise in production. Following independent Task 2 review, the safe ordering is: while holding the publication lock, call the trusted mirror with the candidate identity FIRST; only after it returns successfully install the controller candidate. If it raises, propagate a fixed internal error and abort the pending ordinary mutation before any source/root/analysis change. The old controller identity can remain eligible because its underlying ordinary data has not changed. A mirror still holding the old identity matches only that unchanged data; a partially installed new identity mismatches the old controller revision and must deny admission. This is a narrow correction to the earlier fallback that merely disabled the controller after a mirror error: that fallback could leave an old eligible mirror beside newly changed data. Future composition cannot be accepted unless mirrored admission is demonstrably fail-closed. A stale capture is not a permit: the future service must capture the controller identity outside its own lock, reject publication-lock contention, and compare ALL PublicationIdentity fields/revision under the service lock on admission, dispatch, completion and navigation. The fixed, trusted mirror must perform bounded in-memory replacement under its service lock; an arbitrary mirror that ignores identity comparison is outside this contract.

There is one later exception path: if the current generation's ineligible invalidation was already mirrored successfully and ordinary analysis/cache status has subsequently changed, an ineligible completion mirror error may be finalized locally under the publication lock. This local completion uses the same epoch/generation, a fresh revision, actual ordinary status, and empty snapshot/maps; it never enables admission and does not claim mirror synchronization. The prior successfully mirrored invalidation already revoked old eligibility, while any old or partially updated ineligible mirror identity mismatches the new revision. Do not use this fallback when the initial invalidation itself failed; in that case abort before changing ordinary data.

Independent Task 2 re-review identified the same requirement after an **eligible** commit mirror error: ordinary analysis or cache acceptance has already succeeded, so `_assistant_publish_accepted` must catch the fixed publication error, retain ordinary result/status/root, and complete the same epoch/generation as ineligible with empty snapshot/maps. Try the trusted ineligible mirror replacement; if that also fails, use the local-only ineligible completion above, then return `False` rather than failing the ordinary operation. This applies to both direct analyze and cached project open. A partially installed eligible mirror candidate differs from the newer local ineligible identity and must be rejected by the future service's full identity/revision/eligibility gate. Allowing the ordinary cached open to return also lets the current bridge finish Explorer/source binding; it does not claim that unrelated bridge post-open failures are solved.

## Construction and expected-revision commit

`_assistant_publish_accepted` captures an expected context AFTER invalidation and final ordinary acceptance decisions. It builds `SnapshotBinding(str(root), ignore_paths, epoch, generation)` and calls actual B3 `build_snapshot(accepted_analysis, binding=...)`. Ready/Partial with accepted analysis are the only eligible statuses. Analyzing/Outdated/Failed/NotAnalyzed never qualify. Provenance failure leaves assistant ineligible while ordinary analysis remains usable: parsed-success files require actual non-null parsed hashes; never fill hashes from a subsequent fingerprint. CacheStore already validates exact inventory/hash agreement under B2; valid cached analysis has `tree=None` and requires no AST reconstruction.

Build snapshot and source-only initial navigation facts from immutable snapshot inventories, outside publication/service locks. Facts use typed EntityRef namespaces, so identical raw symbol/code/semantic IDs do not collide. Omit IDs outside EntityRef's 512-character bound from action projections without relabelling them. Initial context_map is empty and initial visible_locations are empty. Snapshot inventories remain bounded by B3 limits. No initial StructureResolver construction, global plan_reveal scan or metadata lookup is needed; conservative source-only navigation facts do not promise an unbuilt graph route.

The trusted caller guarantees original analysis collections cannot mutate during construction: bridge `_call`/`_write_call` serialize existing operations, and direct AppController callers are single-owner callers just as existing synchronous controller methods require. This does NOT make controller operations generally thread safe. Workers receive only detached contexts. A test can interleave publication invalidation without mutating original graph collections to exercise CAS; do not test graph mutation during an unprotected build as supported behavior.

CAS checks under the dedicated lock: current expected object/revision and epoch/generation/key/root/ignore match; `self.analysis is accepted_analysis`; ordinary root/ignore/status still equal the candidate's accepted binding/status; candidate snapshot binding matches those exact values; Ready/Partial and valid snapshot/projections. Prepare/freeze and validate candidate outside lock. Install candidate at current revision+1 and mirror identity atomically, else return False with no replacement. A superseding invalidation OR same-generation map refresh rejects the candidate. Never rebind stale snapshot evidence to new identity. Rebuild only from currently accepted data at a later trusted operation boundary; no background retry. Projection/build exceptions disable assistant with a fixed local issue, without failing a successfully completed ordinary analysis or changing its diagnostic behavior.

## Hook placement: validation versus actual mutation

| Boundary | Validate BEFORE invalidation | Invalidate immediately before | Post-boundary failure |
| --- | --- | --- | --- |
| Controller open / bridge `_open_project` | Existing analyzing guard, path resolution and directory validation; select already validated ignore tuple. | Controller changes root/rules, binds source service, clears analysis or loads cache. | Same rotated epoch/generation stays ineligible; old eligible context never restored. |
| Controller analyze | Existing project-present/already-analyzing guards. | Set Analyzing and begin fingerprint/analyze work. | Retained old ordinary analysis may remain; assistant Failed/ineligible at new generation. |
| Changed ignore settings | Existing strict payload/path/cap/serialized size/outside-project checks. | First settings filesystem mutation (`data_dir.mkdir`) and later activation. | Persist/activation failure leaves assistant ineligible; old explanations inert. |
| Source save | Existing project/path/revision/baseline/readonly/encoding/size checks, first `_verify`, native nontruncating `r+b` handle identity/path validation. | `tempfile.mkstemp` creates a project-local temporary file. | Even temporary creation, second `_verify`, permission/security, replace or cleanup failure remains ineligible. |
| Explorer create | Existing policy/name/kind/project/parent revision/writable/case collision checks and final project/directory stamp checks. | `attempted=True` and native `open('xb')` or `mkdir`. | FileExistsError/permission failure after hook still remains ineligible even without affected_paths. |
| Explorer apply | Existing project/token/prune/pop and BOTH preflight passes including subtree/destination checks. | Native rename/move/recycle call. | Consumed token and existing uncertain-path handling preserved; ineligible regardless of affected_paths. |
| Explorer prepare/list/open source | All existing read/token validation. | No project mutation boundary. | No assistant revision/generation change merely for request rejection/token preparation. |

Implement exact internal per-call keywords, preserving public/native bridge arities:

```python
SourceEditingService.save(..., *, before_write: Callable[[], None] | None = None)
ProjectExplorerService.create_entry(..., *, before_mutation: Callable[[], None] | None = None)
ProjectExplorerService.apply_operation(..., *, before_mutation: Callable[[], None] | None = None)
```

Source hook is after the `with path.open('r+b')` block closes and before `mkstemp`, INSIDE the existing error-handling try and source lock. That opening does not truncate/write source; native access/identity rejection there remains pre-boundary. The final `_verify` after temporary-file write cannot be moved ahead of the hook or removed. Explorer create hook follows `target = root / relative`, BEFORE `attempted=True`. Apply hook follows the validated operation dispatch choice and immediately precedes its native call. Unreachable/unsupported prepared operation rejects before hook. Hooks take no source/affected-path payload and are exactly controller-owned bounded invalidators, not arbitrary product callbacks. If invalidation cannot complete, no native mutation begins.

Pass source hook from controller `save_source_document`. Pass explorer hooks from bridge create/apply; keep `_file_mutation`'s result validation, affected-document invalidation and existing errors. `_project_files_changed` is still after-result document/UI maintenance, not the first assistant invalidation. It must not add a second generation for a mutation whose pre-hook already ran. A hook is invoked once per entered write attempt, not per internal verification/cleanup step.

Rejected strict DTOs, malformed aliases, stale revision/token, protected paths and failed preflight disk/native checks do not change assistant identity if rejected before these hooks. Do NOT claim every externally replaced directory/path or permission denial is pre-boundary: a change detected by source's second verification, or a native create/rename failure after preflight, follows invalidation. Disk/native checks remain necessary preflight I/O; “nonblocking capture” does not mean save/explorer validation has zero I/O. Do not duplicate a weaker bridge-only path preflight or split explorer operations into a generic prepare/apply framework.

For settings, identical ignores may preserve current publication; malformed/no-op rules do not invalidate analysis. Changed valid settings invalidate before persistence. Once the settings file is atomically replaced, it is authoritative: update the active controller ignore rules directly, retain any old analysis only as `Outdated` with `analysis config changed`, and finish the same generation as ineligible. Do not reopen the same project merely to activate settings: source/Explorer bindings and epoch stay unchanged, and a successful settings change increments generation once. If persistence fails, retain old ordinary rules and analysis but do not restore old assistant eligibility. This narrows the failure surface after independent architecture review of a disk/new-rule versus memory/old-rule mismatch. Bridge project opening still passes selected rules into controller `open_project(project_root, *, ignore_paths: tuple[str, ...] | None = None)` so a different project's proposed policy activates only after open invalidation; failed open preserves the prior ordinary rules and completes ineligible at the actual ordinary identity.

## Epoch and existing session publication

Controller generates the epoch once per valid open and passes it to its SourceEditingService.bind_project. Bridge `_open_project` passes selected ignores to controller and then binds its ProjectExplorerService with that captured epoch. Delete bridge UUID generation and redundant source rebind. Both services keep their existing optional standalone epoch generation for isolated use; only application composition uses controller epoch. Source revision prefixes, explorer listing epoch and operation tokens must match it. Old same-root source revisions/tokens fail after reopen. Save/file mutations and analysis keep epoch; generations distinguish accepted analysis/source changes.

Existing `_publish_session` captures ONE immutable context and stores its epoch/generation pair alongside the existing display tuple in a single replaced `_snapshot` value. `_session_snapshot` adds nullable `projectEpoch` and `analysisGeneration` from that stored pair to the existing payload; no `session_payload` signature or web_contract/desktop/frontend edit is needed in this slice. Preserve existing tuple status indexing used by `_write_call`. Preserve get_session's bridge-lock-busy fallback: it returns the last internally consistent session tuple/pair, not current fields mixed with old display state. An old fallback is a display snapshot, never future assistant admission authority. It cannot require publication lock capture while serving the fallback.

Bridge analyze currently publishes display Analyzing before controller work (`web_bridge.py:DesktopBridge.analyze`), while `controller.py:AppController.analyze` sets ordinary Analyzing before its first fingerprint. Merely adding controller invalidation leaves an eligible-publication/display-Analyzing interval; bridge pre-invalidation plus controller invalidation would incorrectly increment twice. The following narrow synchronous notification resolves this actual integration gap and is part of Task 2:

```python
AppController.analyze(*, before_analysis: Callable[[], None] | None = None) -> ProjectAnalysis
# Inside DesktopBridge.analyze.operation, replacing both the early publish
# and the current no-argument controller call:
self._controller.analyze(before_analysis=self._publish_session)
```

This is a NEW optional internal per-call keyword, not an existing port. Controller is the sole invalidation owner. Exact order: existing project-present/already-analyzing guards -> save old ordinary analysis -> `_assistant_invalidate(status=AnalysisStatus.ANALYZING, ...)` once -> set ordinary `self.status = AnalysisStatus.ANALYZING` -> release publication/service locks -> invoke `before_analysis()` once if supplied -> first `fingerprint_project` (when cache is enabled) -> `analyze_project`. Invalidation failure prevents notification and analysis work. Guard rejection invokes neither invalidation nor notification. Direct `controller.analyze()` follows the identical invalidation order with no notification. Completion/failure replaces context at the SAME generation; neither bridge nor the notification increments generation.

`before_analysis` is only the trusted bridge display-snapshot publisher (or an observation fake in local tests), supplied under existing bridge operation serialization. `_publish_session()` uses the now-current ordinary status, so no forced status argument is needed. It performs no `_session()`/recent-project lookup, snapshot build, invalidation, service admission, operation reentry or asynchronous dispatch. It may briefly capture publication after controller has released its lock; it cannot be called under publication/service locks. No registry, constructor listener, persistent callback field, new context tag, bridge pre-invalidator or async framework is added. The callback itself is not retained in AssistantContext. Before notification begins, current context is already Analyzing/ineligible with cleared snapshot/maps; before expensive work begins, display Analyzing has been published. The earlier display tuple may remain observable until this replacement, as an old display snapshot only.

Place notification inside the controller's guarded failure path: if this trusted publisher unexpectedly raises, do not fingerprint or analyze, retain old ordinary analysis as existing failure behavior permits, set ordinary Failed and publish a same-generation ineligible Failed completion, then propagate through existing bridge error handling. Never restore old eligibility or leave ordinary status stuck at Analyzing. Existing `_call` finally still publishes completion/failure. This does not introduce a general callback error policy or service integration.

Task 2 keeps `_publish_session`'s existing six-element tuple and `_session_snapshot` payload. Task 3 adds the immutable captured epoch/generation pair to that publisher and stored fallback. Preserve `_snapshot[1]` as status for `_write_call`'s fast write guard, its under-operation-lock status recheck, and get_session's nonblocking busy fallback throughout. Busy fallback reads only the last replaced tuple, with no publication capture; ordinary read/finally publication does not replace assistant context. After completion/failure, synchronize the display snapshot with the current ineligible/eligible identity when Task 3 adds the pair.

## Visible context projection only when a view is already built

Publish only at successful `get_view`/`get_detail_view` boundaries using the graph already built and validated for delivery. Prefer bridge calling controller `_assistant_publish_view` after graph construction, passing explicit parsed root and detail source. Do not hook controller's internal sanitize/valid_identity probing views; those are not necessarily displayed views. Graph DTO conversion remains unchanged.

At that boundary, outside publication/service locks, freeze whitelisted VisibleNode IDs, semantic_component_ids, code_node_ids, member_ids and SourceAnchor values. Do not copy labels/metadata, mutable graph/layout/query dictionaries, hidden structure trees or unsupported edge IDs. StructureResolver.resolve is allowed only here when an actually displayed architectural projection needs occurrence validation, using current controller namespace/root; retain only matching snapshot-backed typed targets and anchors, never resolver/component objects. No resolver construction at capture/start.

Bounds: total current context_map <=100 ViewNodeKey entries; visible node ID/root/detail strings <=512; targets <=20 and source anchors <=20 per entry, processing only bounded prefixes and setting truncated when additional provenance exists. Use immutable deterministic order. Budget total additional projection text at 256 KiB UTF-8; stop adding entries/targets before exceeding it, with honest truncation, without changing B3 inventories. Snapshot-backed initial source anchors reuse immutable B3 tuples, rather than copying unbounded data. Do not scan all graph members for a “better” target.

Key includes view kind + caller root + detail source + visible ID. Replace a root graph bucket on its next delivered view; replace a detail bucket on its next delivered detail. Retain only the latest root bucket per view kind and currently open controller detail-island buckets. Prune obsolete root/detail buckets on the next existing graph delivery, without inventing frontend visibility events. A detail request not associated with an open island may retain only its current bounded bucket until replaced. Aggregate cap is 100 across all buckets, not 100 per root/detail. Admit the just-delivered bucket first, preserving node order, then prior buckets in deterministic key order until full. Omitted nodes have no map entry and cannot gain false authority. This records delivered-view provenance, not a claim that Python knows browser selection or every live tab.

Targets are namespace-qualified and verified against snapshot inventories. Known semantic component IDs remain semantic, known code IDs remain code, known symbol IDs remain symbol; same string across namespaces is not evidence of equivalence. Group/member IDs map only to actual known entities represented by the delivered node. Unknown/project/aggregate/projection IDs never become fabricated symbol IDs. If members cannot be resolved, use valid source anchors and `source_only=True` with fixed reason `unmapped_visible_identity`; without anchors return an inert empty source-only projection. No recursive hidden-member expansion. Source anchors must match actual immutable inventory/provenance facts before enabling a source entry; mere normalized arbitrary input is insufficient.

Task 5 architecture clarification: Project Structure may place Symbol Index IDs in `code_node_ids`; in that view classify a verified symbol as `symbol`, never as both `symbol` and `code` merely because strings collide. Elsewhere a `code_node_ids` entry becomes a `code` target only when verified in the code inventory. For non-island detail requests, retain the latest orphan detail bucket per view kind; prune older orphan buckets on the next delivered view. These choices do not broaden group-member inference or invent frontend visibility events.

Navigation facts retain only snapshot anchors and observed ViewNodeKey locations whose NodeContext explicitly contains that target. No `plan_reveal` preferred-route emulation or metadata-derived automatic root promises. Future C6 may select those honest locations or source-only navigation and must still revalidate actual source/range/evidence. A previously unknown grouped entry with no map cannot be repaired by start-time graph scanning. C7 permits known real entities/source entries when no map exists; that future admission policy does not infer hidden grouping identities.

Commit maps and navigation facts together by expected-revision CAS, sharing the SAME immutable snapshot/epoch/generation. Identical bounded projections are a no-op; changed projection increments revision and mirrors replacement so future prepared work is invalidated. Failed/superseded map commits leave the previous current publication unchanged; they never publish a partly updated pair or recover an ineligible context.

`_assistant_publish_view` immediately returns False when expected is ineligible, snapshot is absent, or expected analysis identity does not match. It never calls `_assistant_publish_accepted` to recover eligibility. Build identity/projection candidates outside locks; under locks only compare already validated bounded identity and replace references. Keep potentially long map equality/byte accounting outside the lock, followed by expected-revision verification.

## Failure-first test groups and execution order

Use existing unittest fixtures and real bounded services. Fresh/cache parity should use actual analysis plus CacheStore round trip, with current B2 provenance; synthetic B3 fixtures may supplement poisoning tests. Deterministic threading.Event barriers exercise publication interleavings; timeouts prevent deadlock, not assert machine-speed promises. No provider fake is needed to prove zero network: import guards and forbidden operations suffice. Future service mirror tests are explicitly local port tests, not job acceptance claims.

### Task 1 — value and capture contract

- [ ] test_writer creates `tests/test_assistant_publication.py`: initial identity, read-only properties, copied/frozen nested mappings, typed namespace collisions, immutable retained old context, and absence of mutable analysis/AST/metadata/source references.
- [ ] Write capture contention test holding `_assistant_publication_lock` on another thread; expect fixed `AI_ANALYSIS_BUSY`. Separately hold bridge operation lock while capture succeeds from another thread. Patch graph builders/file readers/import factories to fail if capture touches them.
- [ ] Run `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_publication.py -v`; new contract assertions fail before implementation, not only missing test collection.
- [ ] core_implementer adds the value module, initial controller publication and capture/properties only. Run the same group; expect PASS.

### Task 2 — acceptance, CAS and mirror

- [ ] Wire the actual controller `open_project` and `analyze` entry/acceptance/failure paths, not only helper methods. Task 2 owns validated-open invalidation/epoch creation, controller source binding to that epoch, cache acceptance and fresh-analysis acceptance, same-generation ineligible completion, and exactly one invalidation per admitted analysis. Existing controller guards preserve identity on rejection. Task 3 owns the open ignore-policy keyword/bridge activation, removal of bridge's second UUID/source rebind, explorer epoch unification and session identity fields; cross-service epoch equality is not a Task 2 claim.
- [ ] Add controller notification ordering tests with and without cache: observe current context and ordinary status at the hook and first fingerprint/analyze seam; require one generation increment, cleared maps/snapshot and no publication lock held. Direct no-hook calls invalidate once before the first seam; missing-project/already-analyzing rejection neither invalidates nor calls the hook. Inject a hook failure and prove no fingerprint/analyze call, old ordinary result retained as applicable, and Failed/ineligible completion at the same new generation.
- [ ] Add bridge integration barriers at the trusted publisher and first fingerprint, before AST work, plus the no-cache analyze seam. At publisher entry capture must already be Analyzing/ineligible; after publisher replacement, get_session's busy fallback must return Analyzing and `_write_call` must promptly reject a state write without running its operation. Assert bridge notification adds no second generation. Preserve `tests/test_ui_web_bridge.py:test_session_snapshot_does_not_block_behind_real_analyze`, `test_state_writes_are_rejected_while_analysis_is_active`, `test_failed_analysis_publishes_final_snapshot`, and `test_view_state_roundtrip_and_immutable_session_snapshot`; extend coverage rather than weakening their existing assertions. No public session identity fields are required until Task 3.
- [ ] Add real fresh/cache-equivalence tests: same typed facts/hash/provenance ignoring intentional new binding; cache tree=None. Poison excluded AST/metadata values so any access fails. Detach original collections AFTER publication and verify old context unchanged.
- [ ] Add barriers: candidate build pauses, invalidation replaces expected identity, commit rejects; separate same-generation map-revision supersession rejects; changing accepted analysis object/root/ignore/status rejects. Assert mirror sees exact same revision/eligibility as controller.
- [ ] Add analysis/cache failure tests: old ordinary result retained as applicable but new assistant generation ineligible; snapshot validation/budget failure leaves successful ordinary analysis usable; Ready/Partial eligible only with valid accepted provenance. No success-null-hash repair.
- [ ] Add bounded fake mirror transaction tests for synchronous eligibility revocation, pending closure revision overwrite and stale captured identity rejection; assert no callbacks/I/O/clock/teardown under locks. Do not create jobs/service to test this port.
- [ ] Run publication, controller, persistence-controller and web-bridge groups; confirm new assertions fail. Implement controller open/analyze invalidation and acceptance, snapshot build/CAS/mirror, optional `before_analysis` keyword and only the bridge analyze call-site replacement described above; run those groups again, expect PASS. Broader bridge epoch/policy/session changes remain Task 3.

### Task 3 — project and policy identity

- [ ] Add tests for rejected nonexistent/file path and analyzing switch preserving identity; valid same-root reopen rotates epoch; controller source prefix, explorer listing/token and session pair agree; cache acceptance and first live analysis use expected generation.
- [ ] Changed ignore policy invalidates BEFORE settings mkdir/write; persistence failure retains old ordinary rules but remains assistant-ineligible. Successful persistence activates new ordinary rules directly, marks retained analysis Outdated, keeps the same epoch, and completes the single new generation ineligible. Unchanged policy and invalid DTO do not invalidate. A separate explicit project reopen still rotates epoch.
- [ ] Test get_session's busy fallback is internally consistent and requires no publication capture; ordinary read/_publish_session leaves publication revision unchanged. Store both identity fields from one context and verify no mixed pair during open/analyze barriers.
- [ ] Run controller/web-bridge groups to see failure. Extend Task 2's controller-owned open/epoch behavior with the open policy keyword, unify bridge service epoch binding and add existing session identity pairs. Reuse Task 2's analyze notification without adding another invalidator. Run both groups; expect PASS.

### Task 4 — actual source/explorer write boundaries

- [ ] Source tests observe context before `_verify`/native `r+b` and at patched `mkstemp`, second `_verify`, `os.replace`: hook exactly once, ineligible at first temporary mutation, pre-hook rejected revision/encoding/protected/native-handle validation preserves identity, post-hook conflict/permission/replace failure remains ineligible. Preserve security/newline/encoding/cleanup behavior.
- [ ] Explorer tests inspect context at actual `open('xb')`/mkdir/rename/recycle seams. Invalid name/protected path/revision/prepare/stale token/final preflight rejection preserves identity. Native known-no-mutation failure AFTER hook still invalidates; uncertain mutation keeps existing affected_paths/error/document invalidation. Token is consumed as before.
- [ ] Bridge integration tests invoke real save/create/apply and verify after-success helpers do not double increment, analysis-blocked operations do not run hooks, and listing/preparation/source open are nonmutating for publication.
- [ ] Run source/project-explorer/web-bridge groups; confirm targeted assertions fail. Add only three optional trusted hook keywords and their owner calls, preserving existing preflight and public signatures. Rerun; expect PASS.

### Task 5 — delivered views and bounded honest maps

- [ ] Publication/web-bridge tests build actual project/architecture/function/code views and details; compare explicit root/detail keys and typed represented IDs. Include grouped unknown members, namespace collision, source-only fallback, invalid structural occurrence, project aggregate, >100 total entries, >20 targets/anchors, long IDs and text-byte limit.
- [ ] Assert initial acceptance constructs no views/resolver, internal sanitize probing creates no displayed map, and capture constructs none. Patch `navigation.plan_reveal` to fail to prove no mutable global scan is used. Freeze without retaining any VisibleNode.metadata or graph.
- [ ] Test same-generation changed map increments revision, identical map is no-op, root/detail bucket replacement/pruning is bounded, rejected view request changes nothing, and invalidation discards paused map candidates. Navigation/map CAS is atomic; no hidden members are labelled as graph symbols.
- [ ] Run publication/web-bridge groups; confirm new failures. Implement only successful existing view-delivery projection hooks and CAS. Rerun; expect PASS.

### Task 6 — focused quality and independent review

- [ ] Run targeted publication, ui_controller, ui_web_bridge, source_editing, project_explorer and persistence_controller unittest files. Re-run B3 snapshot tests and structure/navigation tests because this slice consumes those boundaries. Expect no failures; report actual counts when executed.
- [ ] Run `.desktop-venv/Scripts/python.exe -m ruff check src tests`, `.desktop-venv/Scripts/python.exe -m mypy`, and `.desktop-venv/Scripts/python.exe -m mypy --platform linux`; the current working environment is `.desktop-venv`, and `.venv` is broken. Use the existing desktop environment only. No installation/network fallback. Report unavailable tools honestly.
- [ ] Parent inspects scoped file changes against saved pre-edit content without Git if Git remains prohibited; preserves unrelated dirty edits. reviewer independently checks invalidation timing at native seams, bounded lock transaction, epoch/session consistency, cache provenance, no mutable retention and honest grouping. Passing tests alone are insufficient.
- [ ] Resolve reviewer findings within this slice; debugger handles unclear failures. Do not invoke final_auditor merely because the plan is long. No new service/jobs/SDK/UI work is accepted as a “fix” to publication tests.

Exact targeted command (existing desktop environment, no dependency acquisition):

```powershell
$publicationTestFiles = @(
    'test_assistant_publication.py', 'test_ui_controller.py',
    'test_ui_web_bridge.py', 'test_source_editing.py',
    'test_project_explorer.py', 'test_persistence_controller.py',
    'test_assistant_snapshot.py', 'test_structure_exploration.py',
    'test_navigation.py'
)
foreach ($publicationTestFile in $publicationTestFiles) {
    & .desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p $publicationTestFile -v
    if ($LASTEXITCODE -ne 0) { throw "Publication verification failed" }
}
```

During each failure-first task, run the same command with only that task's listed files, record the failing contract assertions, then rerun after the minimal implementation. This document creation does not run or claim those implementation tests.

Example assertion pattern for test_writer (proposed ports, real saved-source/explorer fixtures supply operation arguments):

```python
before = controller.capture_assistant_context()
with patch('codeviz.ui.source_editing.tempfile.mkstemp', side_effect=OSError):
    with self.assertRaises(SourceEditingError):
        controller.save_source_document(project_path, file_path, text, revision)
after = controller.capture_assistant_context()
self.assertEqual(after.project_epoch, before.project_epoch)
self.assertEqual(after.analysis_generation, before.analysis_generation + 1)
self.assertFalse(after.eligible)
self.assertIsNone(after.snapshot)
self.assertEqual(dict(after.context_map), {})
# Failure AFTER invalidation does not restore the old accepted context.
```

## Architecture review result and handoff

Approved for this bounded offline C3 implementation after parent reads the full contract. Critical preconditions are the three internal pre-write hooks, controller-owned epoch and expected-revision atomic commit. Bridge-only after-save invalidation, start-time mutable scans, or treating a stale captured object as admission authority fail this review.

Approved minor C3 ordering refinement before Task 2 dispatch: the per-call trusted `before_analysis` display notification above follows controller-owned invalidation/status and precedes fingerprint/AST work, outside publication/service locks. Task 2 explicitly wires controller open/analyze and this one bridge call site; Task 3 retains bridge epoch/policy/session unification. Parent must read this complete refined plan before dispatching Task 2 tests. Task 1's delivered immutable values/capture contract and existing AnalysisStatus enum are unaffected. This refinement is architecture approval only, not a test result or permission to change C2/SDK/service work.

The explicit trusted mirror call is the narrow exception required by approved C3's atomic publication -> service rule. It is bounded in-memory state replacement/cancellation marking only; loop wakeups, callbacks and teardown remain outside locks. Its fake tests reserve the seam but do not prove future production service behavior. Those proofs belong to C4 admission/dispatch/cancellation integration.

Completion of this slice proves local immutable publication and mutation ordering only. Future phases must still implement service identity guards, real B4 freshness/authorization, B5 retrieval/evidence adaptation, disclosure budgets, C6 navigation, C7 endpoints, optional SDK activation and native/frontend acceptance. No assistant availability or application delivery claim follows from this plan.
