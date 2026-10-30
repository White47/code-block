# Code Agent C/D/E Runtime Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Process amendment (2026-10-15):** Process is governed by the repository `AGENTS.md`. Where this plan and `AGENTS.md` disagree about process, `AGENTS.md` wins. Do **not** load `subagent-driven-development` or `executing-plans` for this plan, and do not run a per-task spec/quality review pair. Independent review runs once per phase boundary or at the milestone audit, not per task. Follow `AGENTS.md` "Execution mode" for how far to run: a single task when the user gave one, the plan's task queue when the user named a boundary.
>
> Every technical requirement below — file ownership, security and disclosure invariants, budgets, cancellation and evidence contracts — is unchanged and must still be met.

**Goal:** Deliver the approved read-only code-understanding assistant inside the existing PC application, with explicit disclosure consent, bounded asynchronous requests, authoritative evidence and safe source/graph navigation.

**Architecture:** Keep Python responsible for authorization, retrieval, budgets, provider transport, jobs and evidence validation. React consumes a narrow start/poll/cancel bridge and adapts assistant-ui to the existing editor and graph workspace. Reuse A contracts and the separately delivered B3/B4/B5 snapshot/source/tools; neither mutable graphs nor DesktopBridge methods become model tools.

**Tech Stack:** Python 3.11+, pywebview 6.2.1, React/React DOM 19.1.1, TypeScript 5.9.2, Vite 7.3.6, existing Monaco/React Flow, unittest/Vitest. Candidate pins: `pydantic-ai-slim==2.55.0` and `@assistant-ui/react==0.15.28`, subject to isolated compatibility gates.

---

## Authority, current state and execution boundary

Revision status (2026-10-12): independent final architecture review approves the amended backend design, including coordinated budgets, final-wire capture, cancellation/publication and bounded evidence/poll contracts. This is not implementation or production acceptance. Production runtime/disclosure remains gate-off until coordinated B4/B5 implementation reviews and dependency/integration gates pass. Isolated SDK source/frozen and component-native fake probes have since passed; they change no application dependency and do not prove full application acceptance.

Raw source-context paths from bridge/model inputs must pass source_policy.validate_source_path before constructing normalized B3 AnchorRecord/SourceAnchor values. Preserve B3 normalization for trusted analysis records; reject original aliases before lookup/I/O rather than validating their normalized spelling afterward.

The entire approved `docs/superpowers/specs/2026-10-11-code-understanding-agent-design.md` governs this plan, including its security, response semantics, delivery and packaging requirements. Supporting contracts: `docs/superpowers/plans/2026-10-11-code-agent-foundations.md`, `2026-10-11-code-agent-source-review.md` and `2026-10-12-code-agent-analysis-snapshot.md`. This document is an architect's implementation contract; no production change or dependency compatibility pass is claimed.

A already exposes `RequestScope`, `JobStatus`, `BudgetLedger`, `BudgetReservation`, `BudgetUsage`, `EvidenceRecord` and request-local `EvidenceRegistry`. Its 2 MiB/1,000 limits are per registry, not global. B3's planned immutable records retain original edge ordinals, not core edge IDs. B4 source/freshness and B5 pure tools are separate work: do not edit their implementation or duplicate their secure-reader policy here. C can build against typed fake ports before B lands; real disclosure/navigation stays disabled until the B acceptance contracts below pass. Coordinate overlapping files with the main agent, preserve unrelated work and do not inspect `.codex/` or secrets.

Existing facts that constrain implementation:

- `DesktopBridge._call()` holds its shared RLock and publishes a session; `_write_call()` additionally excludes analysis-time mutations. Assistant network/polling must use neither wrapper nor the frontend `App.enqueue()` / `saveQueue` / `write()` queue.
- `_open_project()` currently generates an epoch for source editing/explorer but does not expose an assistant analysis generation. `AppController.analyze()` replaces analysis synchronously; no reusable immutable agent publication exists yet.
- `desktop.py` exposes only `_API_METHODS` with `_API_ARITIES`; adding bridge methods alone does not make them available. CSP is `connect-src 'self'`; WebView2 also blocks remote resources and navigation.
- `frontend/src/api/bridge.ts` gives ordinary calls 15 seconds, while existing project mutations wait for native completion. Retain these behaviors. Assistant start/poll/cancel are ordinary quick calls; a frontend timeout cannot cancel Python work.
- `App.tsx` has `openSource(anchor)`, `reveal(SearchResult)`, project-generation guards, source dirty guards and native close protection. `SourceDocumentStore` retains drafts; source tabs render `SourceViewer`. Its Anchor columns currently represent AST coordinates, so do not pass Monaco coordinates into existing AST conversion twice.
- `PreferencesStore` accepts exactly version/language; do not silently add AI fields there. Core runtime dependencies are currently empty; optional AI must not become mandatory.

The user supplied public metadata after scoped network approval: slim 2.55.0, assistant-ui 0.15.28 and its React peer range `^18 || ^19`. Pin-selection evidence is not compatibility evidence. The main agent has now installed slim 2.55.0 in isolation with OpenAI 3.29.0, httpx2 2.13.1 and Pydantic 2.14.1; installed slim metadata declares the `openai` extra, `openai>=3.26.0` and `httpx2>=2.7`. Use actual installed source/interfaces, not historical httpx/OpenAI 2 examples. The existing-stack exception is confirmed: no Vercel/eve migration, Next.js scaffold, web server, cloud history, vector DB or Docker runtime. Planning does not require a new internet lookup. Isolated package acquisition may fetch public dependency metadata/packages, never project source or a real model API. User authorization to complete/push belongs to the main execution task; this architect revision changes only this document and performs no code/test/git edits or push.

## File ownership map

All paths are repository-relative and exact. Create only modules with these responsibilities; no generic agent framework or repository-wide refactor.

| Stage | Files | Responsibility / owner |
| --- | --- | --- |
| C gate | `scripts/verify_assistant_dependencies.py`, `frontend/scripts/verify-assistant-dependencies.mjs`, `docs/CODE_AGENT_DEPENDENCIES.md` | Isolated compatibility report, exact tested lock graph, licenses; core_implementer |
| C transport | `src/codeviz/assistant/provider.py`, `src/codeviz/assistant/sdk_provider.py`, `src/codeviz/assistant/config.py` | Stdlib port/errors, lazy slim adapter, HTTPS policy/key/config; core_implementer |
| C authorization | `src/codeviz/assistant/consent.py` | Memory grants, allowlist and revision; core_implementer |
| C execution | `src/codeviz/assistant/jobs.py`, `src/codeviz/assistant/service.py` | Bounded jobs/events, global conversations, orchestrator/budget enforcement; core_implementer |
| C trust | `src/codeviz/assistant/response.py` | Strict answer schema and authoritative citation/relation validation; core_implementer |
| C publication | `src/codeviz/ui/controller.py`, `src/codeviz/ui/web_bridge.py`, `src/codeviz/ui/web_contract.py`, `src/codeviz/ui/desktop.py` | Analysis binding/publication, thin safe bridge DTOs, expose whitelist/lifecycle; core_implementer |
| C manifests | `pyproject.toml`, `requirements/assistant-lock.txt` | Optional `ai` extra and exact transitive lock generated after successful gate |
| D runtime | `frontend/src/app/assistant/types.ts`, `client.ts`, `store.ts`, `runtime.tsx`, `context.ts`, `navigation.ts` | Typed bridge, bounded store, assistant-ui adapter, context and navigation adapters |
| D components | `frontend/src/app/components/AssistantPanel.tsx`, `AssistantSettings.tsx`, `AssistantEvidence.tsx`, `AssistantConsent.tsx` | Sidebar, API settings, evidence cards, explicit scope confirmation; implementer |
| D integration | `frontend/src/types.ts`, `frontend/src/api/bridge.ts`, `frontend/src/app/App.tsx`, `frontend/src/app/components/Inspector.tsx`, `frontend/src/app/components/SourceViewer.tsx`, `frontend/src/app/components/SettingsPanel.tsx`, `frontend/src/app/i18n.ts`, `frontend/src/styles.css` | Existing workspace callbacks/entries and localized UX; implementer, core_implementer for source mapping |
| D dependencies | `frontend/package.json`, `frontend/package-lock.json`, `frontend/public/third-party-notices/assistant-ui/LICENSE.txt` | Gate-passed pinned dependency and MIT notice; existing third-party notices preserved |
| E native | `scripts/assistant_smoke_host.py`, `scripts/verify_assistant_native.py`, `scripts/codeviz.spec`, `scripts/build-desktop.ps1` | Fake-provider source/wheel/EXE smoke, conditional slim hidden imports/resources |
| E docs | `README.md`, `docs/CODE_AGENT_GUIDE.md`, `docs/CODE_AGENT_PROGRESS.md`, `docs/SECURITY_AND_PRIVACY.md`, `docs/PRD.md`, `docs/UX_SPEC.md`, `docs/CACHE_SPEC.md`, `docs/KNOWN_LIMITATIONS.md`, `docs/QUALITY_GATES.md`, `docs/CODE_AGENT_DEPENDENCIES.md` | Instructions, privacy exception, gate and limitation evidence |

Tests: `tests/test_assistant_provider.py`, `test_assistant_consent.py`, `test_assistant_jobs.py`, `test_assistant_service.py`, `test_assistant_response.py`, `test_assistant_integration.py`, `test_assistant_packaging.py`; extend `tests/test_ui_web_bridge.py`, `test_ui_web_contract.py`, `test_ui_desktop.py`, `test_ui_controller.py` for their direct contracts. Frontend: `frontend/src/app/assistant/client.test.ts`, `store.test.ts`, `context.test.ts`, `navigation.test.ts`, `runtime.test.tsx`, `frontend/src/app/components/AssistantPanel.test.tsx`, `AssistantSettings.test.tsx`, and `frontend/src/app/AssistantIntegration.test.tsx`. test_writer authors bulk fixture tests; debugger investigates failures a normal fix already failed to resolve; reviewer inspects once per phase boundary or milestone audit (see the process amendment above), not after every implementation. final_auditor only for unresolved repeated failures, architecture-level uncertainty, or an explicitly required major audit.

## C1. Minimal provider and compatibility gate

`provider.py` imports only stdlib and A value types. SDK is loaded through `importlib.import_module` in the explicitly selected factory, never at package/controller/desktop import. No key means no transport construction or DNS. Missing extras return `AI_DEPENDENCY_MISSING` and leave ordinary PC features available. Transport and SDK exceptions are converted here to fixed safe codes; bridge logging must not log SDK exception chains, raw payloads, headers or keys.

```python
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

@dataclass(frozen=True, slots=True)
class ProviderDraft:
    body: bytes                 # bounded service protocol input; not yet wire proof
    max_output_tokens: int
    deadline: float             # task monotonic deadline

@dataclass(frozen=True, slots=True)
class PreparedRequest:
    preparation_id: str         # opaque one-shot handle, internal to this adapter/job
    method: Literal['POST']
    endpoint: str               # exact confirmed absolute HTTPS endpoint
    body: bytes                 # final materialized SDK HTTP body; exact UTF-8 bytes
    max_output_tokens: int      # effective encoded output limit, verified by adapter
    deadline: float

@dataclass(frozen=True, slots=True)
class ProviderEvent:
    kind: Literal['text', 'tool', 'answer', 'usage']
    payload: bytes              # bounded JSON/text, never automatic execution

class ProviderAdapter(Protocol):
    async def prepare(self, draft: ProviderDraft) -> PreparedRequest: ...
    def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]: ...
    async def cancel(self) -> None: ...
    async def aclose(self) -> None: ...

@dataclass(slots=True, repr=False)
class MemoryKey:
    value: str = field(repr=False)
```

Factory: `create_provider(config: ProviderConfig, key: MemoryKey | None) -> ProviderAdapter`; None raises fixed AI_NOT_CONFIGURED before optional imports, client construction or DNS. A provider instance owns at most one prepared/in-flight request. These are our proposed adapter methods, not claimed SDK APIs. `PreparedRequest.body` replaces the previous `ProviderRequest.body` contract: budget the final captured bytes, which need not match the service's canonical serialization. The service owns the conversation/tool loop. The slim adapter may translate schemas/streaming events, but must not run SDK tools, nested agents, implicit repairs, retries, alternate models, remote tools, tracing or telemetry. Budget and authorization cannot live solely in prompts or SDK per-call limits. If slim cannot expose/control every send with the frozen body and output envelope, it fails the gate.

C2 bounded local port review approves ProviderConfig.from_base_url(base_url, model, revision), immutable TransportPolicy plus target/status validators, and memory ConsentStore.grant/get/revoke/is_current. Backend owns revision replacement; grants preserve B4 sorted/unique exact paths and reject raw/case aliases. These local validators do not prove SDK transport enforcement, freshness, cancellation or resource release. Use a separate fixed-code ValueError subtype for AI_* errors rather than passing unsupported codes into A AgentError.

Minimal prepare/freeze feasibility path, to prove with installed source and a fake transport before adoption:

1. Run one SDK request on the worker loop with an injected capture transport that has no network capability during preparation: no DNS, socket, provider test, telemetry or auxiliary fetch. Capture only after SDK message/schema mapping, auth and HTTP request construction. Materialize a bounded replayable body into bytes; reject unbounded/streaming request bodies and more than one SDK transport entry. Keep the SDK coroutine suspended at this transport boundary, with its response parser intact. Do not issue a second SDK request to reconstruct the captured one.
2. Freeze a read-only view (`PreparedRequest`) and retain the exact captured HTTP request/continuation privately, with secret headers excluded from DTOs/logs/repr. Verify method/origin/path, headers/query policy, effective output limit and source-bearing message attribution against the draft's internal source tags. Count every source occurrence in the captured body; unexpected/unattributable source transformations fail preparation. No registry/history/bridge retains this captured request. Preparation is bounded by the job deadline and is cancellable.
3. Service computes the input bound from those final bytes and checks freshness/identity/consent outside locks. `BudgetLedger.reserve` atomically reserves C's source/token/model amounts outside the service lock (its clock callback must not run under that lock); then a short service section rechecks identity/revisions/cancel/deadline and records a one-shot permit, or discards the preparation without refund. This permit is prepared work, not proof of dispatch. On the same event loop, C4's dispatch guard validates the permit again and releases the capture boundary to the underlying transport with the same request. Recheck exact body/target/output envelope at that handoff; no subsequent SDK serialization, rebuilding, auth replay, hidden retries or redirects may intervene. Changing frozen bytes requires a new preparation and reservation; no implicit replacement.
4. Consume one handle once; abort/cancel drops its request and suspended continuation, closes response/client resources and cannot fall through to network. The capture wrapper must reject any second unpermitted send, including SDK error recovery. Fake tests compare the captured `body` byte-for-byte with the final underlying transport request, and assert zero network during prepare and zero sends after confirmed pre-dispatch cancellation.

Installed slim's `models/openai.py` maps messages, output schema and profile-dependent `max_tokens`/`max_completion_tokens` before calling OpenAI. OpenAI 3.29.0 builds an HTTP request before `_send_request`; httpx2's async transport boundary is a plausible capture seam, not a proven supported freeze API. Its `AsyncClient.send` documentation says an explicit request is sent as-is, but auth/hooks and retry paths still require evidence at the final transport boundary. OpenAI defaults include two retries and redirect-following clients; explicitly disable both and use httpx2's actual transport interfaces with TLS verification and `trust_env=False`. Do not rely on private SDK overrides without documenting and testing the exact pinned seam. If suspension/resumption, bounded materialization, source attribution or unchanged single dispatch cannot be proved, record gate failure and the minimal transport alternative for a separate architecture decision; do not invent a SDK freeze method or silently substitute a stack.

Candidate production pin is `pydantic-ai-slim==2.55.0`; include only the chat/OpenAI protocol extra that the 2.55.0 package metadata actually declares. Do not guess an extra name or install the full `pydantic-ai` distribution. Gate report must record that exact extra, transitive versions/hashes and licensed distributions before production manifest changes. `@assistant-ui/react` pin is `0.15.28`, with current React 19.1.1 retained; peer declaration alone does not pass gate.

Isolated gate in task-specific temporary virtual environment and temporary frontend copy, containing repository fixtures only:

1. Resolve/install pins in isolation, record package metadata/actual optional-extra names, license notices and frozen dependencies. Production manifests and existing environments stay untouched until pass.
2. Prove the prepare/freeze contract above against the actual installed SDK using injected capture/fake dispatch transports: zero network in prepare, exact final-body/source attribution and output envelope, same captured request dispatched once only after reservation, confirmed pre-dispatch cancel => zero sends. Assert correct chat/tool and strict output schema encoding, incremental plain-text streaming, no internal tool execution, no implicit retry after 429/500/schema failure, and no redirect follow. Verify transport `follow_redirects=False`, TLS verification enabled, retry count zero, `trust_env=False` (no proxy/netrc redirection or headers), no telemetry. Assert cancellation closes the active response/client and limits/deadline can be enforced. Semantic JSON equality or inspecting only pre-SDK messages is insufficient wire proof.
3. Mount assistant-ui in React 19/Vite with a custom external runtime. Test abort, polling updates, partial text, final structured cards and controlled history without provider URL/key in React runtime. Build with no remote assets/CDN, no cloud persistence/telemetry and existing CSP. Test a local WebView2 rendering probe, not just jsdom.
4. Build a minimal Windows PyInstaller probe including the lazy SDK and packaged frontend; verify local resources, TLS CA material and offline import/start without key and separately without installed AI extra. Record time, artifact and pass/fail/skipped.

Gate decisions are explicit: pass slim => optional ai extra and locked adapter; fail slim => stop SDK integration, report exact failing requirement and recommend a small explicit OpenAI-compatible HTTP adapter using a pinned minimal transport, subject to architecture decision. Pass assistant-ui => install pinned component only; fail => stop component integration and report exact failure plus an existing-React custom chat recommendation. Do not silently substitute either. Independent C domain/fake tests can continue while a dependency decision is pending. No real API connectivity test in this gate.

## C2. Configuration, transport target and consent

`ProviderConfig`: `{base_url:str, origin:str, endpoint:str, model:str, revision:int}` frozen/internal. Browser supplies only `baseUrl` and `model`; backend computes origin/endpoint/revision. `baseUrl` <=2,048 chars; model 1..128 printable UTF-8 chars, no control characters. Key <=8,192 printable chars, no CR/LF/NUL, internal repr excluded. Do not persist any AI settings in this first version (saving non-sensitive URL/model is optional in the spec and adds no launch requirement).

Accept absolute HTTPS only; reject credentials, query, fragment, whitespace/control chars, backslash, ambiguous encoded separators/dot segments, HTTP and protocol-relative URL. Canonicalize DNS via IDNA/lowercase and effective port (443 default); validate IPv6/port explicitly with URL parsing. Permit base path only empty, `/` or `/v1` (optional trailing slash); fixed endpoint is respectively `/chat/completions` or `/v1/chat/completions`. Reject other prefixes with a fixed compatibility error instead of guessing routes. Pin normalized `(scheme, host, effective_port)` and endpoint for every send; no model override, discovery, `/models` fetch, redirect, proxy environment, fallback URL or TLS exception. Never retry 301/302/307/308; even same-origin redirects fail. Tests inject mock transport, never relax HTTPS for a live localhost server.

Connection test is an explicitly clicked, source-free background job using the same transport policy. Prompt contains a fixed harmless instruction, one artificial tool and one strict synthetic answer schema; verify actual tool support and structured response, report exact incompatibility safely. No project identifiers, paths, chat history or metadata leave Python. Maximum 2 admitted model requests, 1,024 input tokens, 128 output tokens, 15 seconds, one config test at a time; share global worker limits and cancel it on config/key replacement. This is a separate user action from enabling project disclosure and may still cost money; no automatic startup/config save test. All automated tests use fake providers.

`ConsentGrant`: `{project_key:str, origin:str, config_revision:int, allowed_files:tuple[str,...], revision:int, enabled:bool}`. `project_key` is internal canonical project identity, not supplied by the model; use backend root normalization and Windows case semantics. Grants are in-memory, partitioned by project+origin; config revision additionally prevents silent changes in path/model/key from reusing an in-flight request. File whitelist is explicit normalized relative files (bounded by B snapshot inventory), not an unrestricted recursive glob. The consent screen lists provider/address/model, source excerpts/question/recent history/symbol and relation metadata, scope, protected exclusions and all limits. No checked default and no grant created by opening the panel, explanation entry or config getter.

Effective readable/disclosable scope = B3 successful analyzed file inventory ∩ current discovery/ignore rules ∩ user whitelist − B4 protected files. B4 handles `.env`, secrets/certificates/Git internals, path/handle identity, secret suspicion and fail-closed scope. C additionally filters search names/anchors and graph metadata by authorized provenance: a relation with unauthorized endpoint/provenance is omitted rather than leaking its name or path. Source-free/external nodes may appear only as bounded identifiers already derived from authorized evidence; otherwise omit. Filtering reports a fixed coverage gap without listing denied filenames. Tool arguments and names are bounded JSON; only `search_symbols`, `read_source_excerpt`, `get_local_relations` can dispatch.

B5 pure search/relations outputs are local metadata candidates, not wire-ready authorization. Before every wire, including history/repair retransmission, C computes the complete provenance-file dependency closure of all outbound metadata, source and retained explanations. Through the reviewed B4 reader/security primitives it verifies actual raw hashes against B3 and applies B4's whole-file secret gate, including secrets outside any selected excerpt; file-set freshness or path consent alone is insufficient. Include semantic component code-symbol provenance, relation anchors and both endpoint provenance. Unknown attribution/closure is not permission: exclude the affected material or reject the send. Any blocked/unverifiable/changed dependency excludes its names, IDs, semantic static explanations, related facts and dependent history; recompute outward counts/truncation from safe candidates only, without denied names/counts. Changes also follow the existing Stale rules. These bounded local security reads do not supply model context: discard source buffers, do not expose their receipts automatically, and count any retained metadata under C5. Only explicit model-context retrieval through B5 dispatch can contribute source to a later fully budgeted wire. No new B4 public method or relaxed reader policy is assumed.

Revocation, scope replacement (including shrink), destination/model/key replacement or AI disable increments consent/config revision, cancels matching tasks, closes their evidence, deletes eligible history and prevents retransmission. Scope enlargement requires explicit new confirmation; smaller scope still invalidates existing material. Switching projects cancels jobs and disables old citations; current-memory grants may be reused only after comparing exact scope/target/revision. Reanalysis/new files never silently enlarge grants. Frontend key field is emptied immediately when submission starts, also on failure/unmount; Python getter returns `keySet` only. Exit drops key/history references, without claims of physical memory erasure.

## C3. Publication and B boundary

Controller owns one immutable current `AssistantContext`: `{publication_revision:int, project_epoch:str|null, analysis_generation:int|null, project_key:str|null, root:Path|null, ignore_paths:tuple[str,...], status:AnalysisStatus, eligible:bool, snapshot:AnalysisSnapshot|null, navigation_facts:immutable projection, context_map:immutable projection}`. Expose epoch/generation as read-only properties of this object, never independently updated fields. All replacement objects and their nested projections are immutable. Bridge source editing/explorer use that same epoch; frontend `projectGeneration` remains only a rendering guard. Session DTO adds nullable `projectEpoch`/`analysisGeneration` copied together from one captured context. `publication_revision` is internal and increments on every replacement, including later view/context-map publication in the same analysis generation.

Ordered publication transaction, with a short dedicated publication/state lock (not the bridge lock):

1. Validate proposed operation without changing current project first. Immediately before analysis, cache load/project switch, ignore change, save or file mutation can change analysis/source identities, replace the current context with an ineligible context: increment generation (rotate epoch for a valid project-open attempt), clear live snapshot/maps, synchronously invalidate permit eligibility/set cancelRequested on affected jobs under the service state lock and enqueue C4's loop gate-closure command, then release locks before the operation. This is invalidation, not a premature cancellation confirmation. Lock order is publication -> service state; no reverse acquisition. A rejected operation before this boundary leaves identity unchanged. Failure after invalidation stays ineligible and never restores an old eligible generation; retained old explanations remain readable with actions disabled. Successful same-root reopen rotates epoch as well.
2. At the controlled analysis/cache acceptance boundary, capture the expected context identity/revision, build snapshot/navigation/context maps while the trusted caller guarantees original analysis collections cannot mutate. No worker sees mutable graphs; no construction occurs in assistant start. Build valid cache snapshots without AST subject to B2 provenance. Do not hold publication/service state locks while building or doing I/O.
3. Commit only if expected epoch/generation/publication revision, project/root/ignore and accepted analysis identity still match the current context. Install the entire eligible context atomically and update the service's current identity in the same publication -> service transaction. Discard a candidate superseded by any invalidation or context-map publication; rebuild from current accepted data if needed. Failure disables assistant only; ordinary analysis remains usable. Analyzing/Outdated/Failed contexts are never eligible.
4. `start` captures one immutable context, then under the service state lock compares its complete current identity/revision and eligibility before admission. A capture overtaken by invalidation fails fast; it cannot admit against a saved snapshot merely because the old epoch/generation match the request. Dispatch and final-result/navigation publication repeat the same current-identity, consent/config and conversation-revision guard. Context-map refresh invalidates prepared work tied to the replaced publication; it does not relabel old evidence with new identities.

Quick `assistant_get_settings/get_state/start/poll/cancel` use only bounded in-memory DTOs and short dedicated state/publication sections; they never acquire the shared bridge/controller operation lock, enumerate mutable graphs, scan/read files, load SDKs, build clients or perform network. A busy publication capture returns `AI_ANALYSIS_BUSY` instead of waiting for analysis. Cache/file mutation hooks must invalidate before changing data, not only from an after-save callback; late external changes are still handled by B4 freshness.

B integration ports below are new C adapter contracts in `service.py`, not assumptions that B4/B5 already export these method names. Implement thin adapters to their reviewed public interfaces after delivery; no replacement secure reader:

```python
class RetrievalPort(Protocol):
    def execute(self, name: str, arguments: bytes) -> 'ToolResult': ...
    def check_freshness(self) -> 'FreshnessResult': ...
    def resolve_evidence(self, evidence_id: str) -> 'ResolvedEvidence': ...
```

`ToolResult={payload:bytes, source_fragments:tuple[tuple[str,str],...], evidence_ids:tuple[str,...], dependencies:tuple[str,...], truncated:bool}`. Payload is filtered canonical UTF-8 JSON, including only permitted source/metadata after C2's wire gate. `source_fragments` tags every source occurrence for wire accounting; it is not additional unbudgeted source. `FreshnessResult={fresh:bool, checked_at:str, coverage_complete:bool}` verifies file set plus dependency hashes through B4; if dependency closure is uncertain verify all analyzed files. Failure/unsupported platform => not fresh. These are C internal DTOs, not B5's public return types: execute strictly decodes bounded JSON into the existing B5 dispatch schemas and projects its actual typed results; it never bypasses ToolSession.dispatch for model context.

`ResolvedEvidence={record:EvidenceRecord, proof:B5EvidenceBinding}` is a frozen internal C value; `B5EvidenceBinding` is an import alias for B5's actual `tool_contracts.EvidenceBinding`, not a replacement public type. Its actual fields are `evidence_id, scope, snapshot_binding, target:EntityRef|RelationKey|None, provenance:SymbolRecord|CodeNodeRecord|ComponentRecord|CodeEdgeRecord|RelationRecord|None, returned_anchor, content_hash, confidence, truncated, status:'bound'|'source_only'`. Resolve only an ID in C's request-local provided-ID map: obtain its single chosenTarget from that map, call B5 `lookup_binding(evidence_id, chosenTarget)` and A `registry.lookup(evidence_id, scope=scope, expected_file_hash=proof.content_hash)` using the already B4-verified identity. Require exact ID/scope/current snapshot binding, hash, returned-anchor and truncated agreement with the actual receipt/registry; require target agreement with C's immutable map. A record confidence remains source Exact; bound proof confidence must match its actual immutable B3 provenance, not be overwritten by A. Validate typed target/provenance against the current snapshot and B5's actual returned-range support. `bound` requires non-null matching target/provenance; `source_only` requires both null and cannot prove a relation or enable graph navigation. The adapter never accepts a model ordinal/endpoint as authority and never invents a core edge ID. B4 actual UTF-8 boundaries and B5 receipt proof remain mandatory; A lookup alone is insufficient.

C deliberately narrows B5's multiple-target binding capability: per request, one evidenceId has exactly one immutable chosenTarget (an EntityRef, RelationKey, or None). Select it only from the already authorized local retrieval facts, in deterministic snapshot order, before exposing that ID to the model. Verify the actual B4 receipt, then call `bind_evidence` once for that chosenTarget; publish the ID-to-fact association only after successful verification. Repeated use of the same target is idempotent; another target for that ID is rejected, never reassigned or resolved by guessing from claim prose. A relation-bound ID can also supply a source card/action from the same A record without binding target=None. A source-only ID cannot later be promoted. For another fact, obtain a separate actual B4 receipt/ID through a new `read_source_excerpt` dispatch (even for the same range), then bind its own chosenTarget. This simplest policy has no free proof-read loop: each such read consumes B5's existing eight-attempt aggregate, B4's fixed per-read bounds, the same 90-second deadline and registry/global capacities. Stop when any limit fails; no automatic full-neighborhood proof expansion. Facts without proof can only be presented as non-clickable unknown/unconfirmed material after the metadata wire gate, not static cited claims. Model-supplied arbitrary endpoints/targets cannot fill the map. Internal verification does not add extra source to wire; explicit excerpt payloads count every actual occurrence under C5.

Caps remain search 20, relations 2-hop/50 nodes/100 edges, source 8 distinct files/12,000 cumulative disclosed chars. Budget ownership is a coordinated B/C acceptance prerequisite: B4 reader uses only `ledger.reserve(BudgetReservation())` for cancellation/deadline checkpoints and charges neither tool attempts nor source characters. B5 `ToolSession` is the sole tool-attempt counter; every seed and received attempt, including malformed arguments/unknown name/rejected scope, passes its dispatch admission once before validation/lookup and consumes one `tool_calls=1` reservation unless already cancelled/expired. Valid transport framing must be bounded before a call is recognized; reject oversized/unparseable response envelopes without executing tools. C never adds another tool charge, including for seeds or retries; B4 internal double reads are not tool attempts. C alone charges each dispatched model body as C5 specifies. Main must coordinate this revised ownership into B4/B5 before real adapters are enabled; do not silently compensate with larger limits or separate unbounded ledgers.

Function entry resolves the smallest indexed symbol containing a saved-source anchor; file entry uses bounded file excerpts; free question seeds a pure indexed search. Seeds do not call the UI search/view/inspector or execute project imports. Partial requests disclose coverage gaps; NotAnalyzed/Analyzing/Failed/Outdated never start a new explanation.

## C4. Jobs, cancellation and idempotence

Use one bounded background asyncio event-loop thread for provider I/O, an executor with two workers for bounded B source/freshness work, and at most one active explanation per project (including Queued). Global admission cap 2 including connection tests; cancelling tasks retain their slot until resources actually close. No daemon task spawning per token/request, UI-thread I/O or network under any bridge/controller/service/global lock. The service state lock protects only in-memory maps/counters; callbacks, clocks, B operations and SDK awaits run outside it. Publication/invalidations follow C3's publication -> service order; worker guards read the mirrored current context identity under service state lock and never acquire bridge lock. Lifecycle hooks set cancellation/revision bits synchronously and enqueue loop teardown without waiting in native callbacks.

`start` performs strict DTO/consent/context validation, canonical digest and in-memory capacity admission, then schedules work and returns `{jobId,status,scope,conversationId}`. It creates the task ledger/deadline but does not reserve a model/source/token send before a final wire body exists. No scanning, reading, snapshot construction, SDK import or network in start. Non-blocking context capture during analysis returns `AI_ANALYSIS_BUSY`, not a blocked bridge call. Target <100 ms with fake clock/barriers in loaded-state tests; no reliance on a machine-specific hard performance promise. Worker checks freshness before first send and before every subsequent send/tool request, completion and citation navigation. Deadline starts at successful admission, including queue/retrieval/prepare time; 90 seconds total, monotonic. Cancellation and stale take precedence over late completion; record a single terminal event. Completed may later become Stale when dependencies change; Failed/Cancelled do not become Completed. C7 separately reports closure/release and cancel confirmation without inventing additional job states.

Idempotence key is `(projectEpoch, requestId)` for the active epoch. Digest includes generation, conversationId, question, context, model/config revision, consent revision and normalized scope. Identical replay returns original job/status without another admission or charge, even after clear/cancel/finish. Different digest => `AI_REQUEST_CONFLICT`. No key/source in digest tombstones. Keep at most 1,024 request tombstones per epoch; once full, reject new IDs with `AI_REQUEST_LIMIT`, never evict then silently reissue a billed request in the same epoch. Retire tombstones on epoch rotation; old epoch always fails admission. User Retry uses a new requestId and explicit send after scope/freshness recheck; start timeout recovery reuses the original ID with identical payload and is not a fresh model retry.

Cancellation is two-phase, idempotent and does not add a seventh JobStatus. For a nonterminal job, quick `cancel` sets `cancelRequested=true`, queues the worker-loop cancellation command and returns `cancelAccepted=false` until confirmation; this is a request receipt, not a stop guarantee. Snapshot uses `cancellation:'requested'` while its existing Queued/Running status remains, then terminal Cancelled once closure is confirmed. Repeated calls return current fields. Unknown/retired/foreign IDs fail safely. Already terminal jobs retain their status; cancelling their unfinished teardown can still close resources and confirm the gate without rewriting a Completed result into Cancelled.

The worker loop owns each job's dispatch gate and every prepared handle. Service reservation/permit creation is distinct from actual dispatch. Define dispatch as entry into the permitted underlying transport operation with the same captured request, not prepare, reservation, scheduling or SDK coroutine creation. Loop processing serializes cancellation commands against this entry: check current context/config/consent/conversation identity, cancelRequested and one-shot permit under the short service lock, mark that permit dispatched, release the lock, and immediately enter the transport without a scheduling await. No cancel acknowledgement is issued by another thread in this interval. DNS/connect/read proceed outside locks; once entered, that operation is in flight and only best-effort abort is possible.

On the loop cancellation command, close the dispatch gate first, destroy all prepared-but-undispatched permits/captured continuations, cancel in-flight work, and initiate response/client/B-worker cleanup. Only after the gate is permanently closed, prepared handles cannot resume and owned resources are actually released does the loop publish `cancelAccepted=true`, `dispatchClosed=true`, `resourcesReleased=true`, `cancellation:'confirmed'` and the single Cancelled terminal event for a previously active job. Cleanup failure/still-running OS work is not release confirmation: keep requested/closing fields and retain the slot; expose a fixed safe issue. No GUI callback joins or awaits this cleanup. A task that has already dispatched may have provider work/billing despite confirmation; there can be no new dispatch after confirmation.

Deterministic pre-transport proof: pause a prepared request at an async barrier before the final dispatch guard (not by blocking the event loop), request cancel, allow the loop command to close/discard that handle and publish confirmed+released, then release the barrier. The fake underlying transport send count must remain zero. Separately test cancellation after dispatch, which may have count one and must close resources with no second send. This distinguishes receipt from confirmation and eliminates the earlier admission-as-in-flight assumption. Every tool dispatch and final publish also checks identity/revisions/cancelRequested; drop late content and never register evidence after closure.

Events use monotonically increasing `seq` per job, including post-terminal release/confirmation updates; ring max256 events, each <=8 KiB exact encoded JSON. Split UTF-8 text on boundaries; chunk text is plain unvalidated text with no navigation. `poll(afterSeq)` returns up to32 events/max64 KiB delta payload, no wait/long polling; negative/bool/future cursors rejected. If cursor fell behind return `reset:true` with no events and bounded authoritative current state/current seq, not a replay of all tokens. C7 defines the flattened, kind-specific snapshot and complete160 KiB envelope ceiling. Explanation display text is bounded by64 KiB and final validated answer by64 KiB; other job kinds have no explanation text/answer. Completed event carries a result marker; large answers live only in the bounded snapshot, not a >8 KiB event. Cap raw response at256 KiB and tool calls/arguments at8 KiB each before parse. Reject excessive nesting (>16), duplicate JSON keys, NaN/Infinity and oversized collections before accumulating them. Internal provider/tool events are never passed through as public DTOs.

## C5. Global memory and whole-request budgets

One application `ConversationStore` in `service.py` owns every project's histories, evidence maps and retained results. Globally max10 conversations, max40 messages/conversation, max2 MiB across all conversation content + final DTOs + retained evidence metadata + relation bindings, max1,000 evidence records total. Count canonical UTF-8 JSON bytes including keys, IDs, nulls, anchors, messages and duplicate retained payloads; logical accounting is not Python heap measurement. Events have the separate spec ring cap; do not duplicate full histories into rings. Job snapshots reference stored result rather than retain duplicate source. Frontend mirrors the same maximums and applies backend eviction IDs, preventing memory escape through assistant-ui history/runtime caches. No localStorage/IndexedDB/chat files or analysis-cache writes.

Before appending messages/evidence, reserve combined global capacity atomically. Evict oldest inactive complete conversation and its registries/bindings together; do not evict active tasks, drafts in editors, or arbitrary evidence independently from its conversation. At40 messages or when no complete conversation can be evicted, reject the new turn with a clear capacity message/new-chat action. Clear deletes messages/evidence/results, increments a conversation revision, cancels active job and prevents re-creation by late events; retains only bounded idempotence tombstones. New conversation requires explicit user action. Failed/budget-exhausted jobs retain safe evidence already acquired while capacity permits; display unavailable/incomplete, never convert an invalid model answer into successful content.

Reuse `BudgetLedger` unchanged; ownership is exactly C3's coordinated B4/B5/C contract, with limits upper bounds that configuration can lower only. For each prepared final HTTP body, collect all source occurrences, including retransmitted initial excerpts, tool messages, prior answer quotations and recent history. B4 reads/secret checks/registers evidence but only reserves empty checkpoints; B5 ToolSession alone charges dispatch attempts. Reading one excerpt without sending charges zero source characters/model requests; sending that excerpt twice charges its full character count twice, with one tool attempt if it was read once. C does not duplicate B5 tool reservations. Keep tagged source segments internally in backend eligible history only; revalidate/reissue historical evidence into this request's scope if reused. Prior evidenceId alone is not usable. If text cannot be attributed safely, exclude it from outbound history; cap does not imply permission. Files deduplicate by normalized relative identity while source chars sum every occurrence on every request. Metadata-only authorized files also participate in a service distinct-disclosure-file set <=8, preventing metadata from leaking unrestricted inventory.

History selection = most recent6 eligible messages from same project, conversation, origin, generation and consent/config revision. Exclude stale/failed unvalidated tool traces, revoked scope, blocked snippets and old configuration; no SDK-owned history. Include system instructions, schemas, message framing, question/context, source, search/relations metadata, tool args/results, history, repair prompts and every repeated request in input token accounting. Bound the service draft and captured final body to256 KiB before accumulation; C1 prepares the actual SDK HTTP body without network, then C computes the reservation from that frozen body, not a draft reserialization. Known tokenizer uses verified model framing bound; unknown tokenizer uses UTF-8 bytes of full frozen wire body plus a conservative verified framing allowance from the adapter. If protocol hidden overhead or source attribution cannot be bounded, fail before dispatch. Provider actual usage is separately reported as actual/estimated/unknown and never refunds reservations or claims precise cost.

Retain internal complete dependency tags for metadata/history as well as source occurrence tags; apply C2's whole-file gate afresh for every send. If filtering changes a prepared body, discard it and prepare/reserve the new final body under C1; never patch captured bytes after their reservation. Global C5 accounting includes B5 receipts/bindings, full retained provenance and C's chosenTarget maps under B5's existing local caps; sharing immutable references does not exempt their logical retained metadata from accounting. History reuse needs new request-local receipts/bindings through the same bounded dispatch policy, not an old ID or an uncounted verification read turned into context.

```python
# service.py, after no-network prepare and before issuing a one-shot permit.
# C4's loop guard may discard this permit before dispatch; no refunds.
ledger.reserve(BudgetReservation(
    model_requests=1,
    input_tokens=wire_input_upper_bound,
    output_tokens=allocated_output_limit,
    source_fragments=all_wire_source_occurrences,
))
```

Allocate output maximum from remaining2,000 aggregate, including tool/structured JSON output; initial allocation512, later min512/remaining, and final/one repair can use remaining. Transport must enforce/count tool argument output as well as text. Unknown output tokenization additionally enforce UTF-8 byte upper bound and abort conservatively if necessary; max_tokens alone is insufficient if a protocol does not count reasoning/tool output. Usage exceeding the bound fails compatibility and prevents another send; report possible provider discrepancy, not exact billing. Max6 total model sends and8 tool executions, including seeded retrieval, one optional structured-output repair and any explicit protocol resends. Transport/auth/429/5xx/timeouts have no automatic resend. One schema repair is allowed only after a fresh/cancel/authorization check, a new no-network prepare and C reservation, with bounded sanitized validation codes; budget exhaustion => no repair. B5 charges every tool attempt once before validation; C/B4 never charge it again. Source/model/token reservation is at final wire admission, never reader time; a permit cancelled before dispatch may retain its conservative reservation but is not reported as actual disclosure/provider usage. Time budget checks also run during prepare/stream/tool parsing; network and B operations have bounded deadlines.

## C6. Answer and trustworthy navigation DTOs

Strict model answer schema uses seven section kinds `summary`, `entry`, `steps`, `branches`, `relations`, `evidence`, `unknowns`; all required once in that order, arrays <=20 claims/section, claim text <=2,000 chars, max60 claims overall, encoded response <=64 KiB. `ModelClaim={text:str, basis:'static'|'inference'|'unknown', evidenceIds:list[str], relationEvidenceId:str|null}`; evidenceIds <=8 and no duplicates. No URLs, filenames, coordinates, endpoint types, confidence or navigation fields accepted in the schema. Unknown factual claims explicitly say unable to confirm. Key static facts require evidence; relationships require relation evidence. Backend downgrades unsupported business-intent/order claims to inference or rejects inconsistent structural claims; it cannot prove arbitrary prose truth solely from citations.

Each cited ID must be on this request's provided-ID list, resolve in its A registry with the complete RequestScope and backend verified hash, match allowed provenance, valid actual text range and existing typed symbol/code/semantic namespace. Relation binding resolves snapshot `(code|semantic, original_index)`: backend supplies source/target IDs, EdgeType, original Confidence and supporting anchors. Do not invent `code_edge_id`; keep A field None until a real core identity exists. Duplicate/uncertain CALLS preserve candidate targets/confidence; data/control/structure/call categories derive from actual EdgeType mapping, semantic tensor/gradient relations never become runtime proof. Source evidence cannot authorize a claimed arbitrary graph edge. Invalid citations/relations generate fixed issues and no actions; one bounded repair may occur, then job Failed (`AI_EVIDENCE_INVALID`) with safe explanation and acquired evidence retained separately.

`relationEvidenceId` resolves only C3's unique chosenTarget; it must be a bound RelationKey, with endpoints/type/confidence taken from `ResolvedEvidence.proof.provenance`. No model target field is added. Two distinct relations require two distinct provided receipt IDs even if their source ranges coincide; an entity/source-only binding cannot substitute. Navigation uses this same verified association, with source action derived from the A record and graph action only from its supported chosenTarget plus existing local reveal facts.

Validated DTO: `Answer={sections:Section[], evidence:EvidenceCard[], relationFacts:RelationFact[], incomplete:bool, coverageGaps:string[], checkedAt:string}`. `EvidenceCard={evidenceId,label,confidence,truncated,sourceAvailable,graphAvailable,unavailableReason}`. Label/path comes from local evidence, never model. No model link is rendered as actionable. `RelationFact={evidenceId,sourceId,targetId,type,confidence,candidateTargets,category}`. Cites remain disabled until Completed validation; even valid citations do not elevate inference to confirmed static fact. Markdown raw HTML disabled, images and external links not fetched/opened; render text safely or inert Markdown with explicit local card buttons. Escape malicious code comments/model strings.

`assistant_resolve_evidence` is a quick-start navigation-check job, not a synchronous filesystem scan in the bridge. Request `{projectEpoch,analysisGeneration,conversationId,evidenceId,action:'source'|'graph'}`; poll yields a short-lived local `NavigationTarget` only after freshness/authorization/ownership checks. It does not invoke a model. Max one navigation check per project, shares bounded executor; deduplicate identical active checks. Navigation checks do not clear explanation history or create new chat turns. Target includes binding+checkedAt, `astAnchor:Anchor`, `monacoRange:{startLineNumber,startColumn,endLineNumber,endColumn}|null`, `reveal:Reveal|null`. Source action is whole-line if columns absent. Graph reveal uses existing `RevealTarget` semantics from immutable navigation facts, with no fabricated node if no supported mapping. Disable stale target on any identity change; frontend consumes it immediately under local project-generation guard.

2026-10-18 navigation projection decision (accepted continuation of the recommended defaults): explicitly project the closed target below; never serialize the internal dataclass wholesale. `scope.requestId` identifies the original evidence/explanation request, not the navigation job. Public `binding` carries only epoch/generation; internal project root and ignore paths remain in Python. Every public revision/coordinate is a non-boolean integer within JavaScript's safe-integer range (line/Monaco coordinates start at 1; AST byte columns start at 0). Text bounds count Unicode characters, with valid UTF-8, and the complete compact UTF-8 target is at most 8 KiB.

```typescript
type NavigationTarget = {
  evidenceId: string;
  scope: { projectEpoch: string; analysisGeneration: number; requestId: string };
  binding: { projectEpoch: string; analysisGeneration: number };
  action: 'source' | 'graph'; astAnchor: Anchor;
  monacoRange: { startLineNumber: number; startColumn: number;
    endLineNumber: number; endColumn: number } | null;
  reveal: { viewKind: ViewKind; rootId: string | null; selectedId: string; reason: null } | null;
  checkedAt: string; conversationId: string; conversationRevision: number;
  configRevision: number; consentRevision: number;
};
```

The target scope and binding epoch/generation and its evidence/action/conversation/config/consent fields must match the outer completed navigation snapshot. Evidence IDs and graph IDs are at most 512 characters; conversation IDs at most 128. `astAnchor.filePath` is a normalized relative POSIX path of at most 512 characters, with no absolute path, backslash, colon, empty/dot/parent segment or control character. If either AST column is absent, the Python projection emits both columns null and no Monaco range; the frontend accepts only paired-null or paired-valid columns. A source target has `reveal:null`; any verified Monaco range must remain within its AST line range and ordered, with no coordinate conversion in the projection. A graph target has `monacoRange:null` and a usable reveal with a nonempty selected ID and no unavailable reason. These shape checks do not replace the receipt, ownership, disk/editor freshness or immediate consumption guards.

Source adapter calls existing `openSource(target.astAnchor)` and displays converted Monaco selection through a new optional verified-range prop on `SourceViewer`, not by inserting UTF-16 columns into AST Anchor. Current dirty document => warn that disk evidence differs; do not jump to stale offsets inside the draft. Offer disk excerpt evidence card and let user save/reanalyze themselves; never save/discard or overwrite drafts from the assistant. On opening a non-dirty document, compare its existing saved `revision` semantics with freshly read content through the B adapter (do not assume revision equals SHA256); mismatch disables selection/rechecks. Source check and navigation have a race interval: show checkedAt, recheck the target file immediately on open, and do not promise an atomic immutable disk/editor. Graph action explicitly dispatches a reveal using approved local context; answering alone never changes Visible Graph/layout/tabs/cache/confidence.

## C7. Exact frontend bridge contracts

All return existing `Envelope<T>` schemaVersion1; assistant errors use fixed messages/codes without raw provider exceptions. Extend `AllBridgeMethods` with imported `AssistantBridgeMethods`; keep AI DTOs in `app/assistant/types.ts`. All methods below have arity `(1,1)` except getters `(0,0)`. Add every method to `_API_METHODS`, `_API_ARITIES` and native security tests. Never expose private runtime/model methods.

| Method | Exact request | Result |
| --- | --- | --- |
| `assistant_get_settings` | no args | `{available,enabled,baseUrl,model,keySet,configRevision,issue}` |
| `assistant_configure` | `{baseUrl,model,key:string|null,enabled:boolean}` | settings above; null keeps key, empty clears key, destination change requires resubmission/clear |
| `assistant_test_connection` | `{configRevision,requestId}` | `{jobId,status,scope:null,conversationId:null,kind:'connection'}`, no project context |
| `assistant_get_state` | `{projectEpoch}` | closed `AssistantState` below; bounded project displays/grant/active jobs, no source/key |
| `assistant_grant_consent` | `{projectEpoch,analysisGeneration,configRevision,allowedFiles:string[],confirmed:true}` | `{consentRevision,origin,allowedFiles,enabled}`; exact current file set verified |
| `assistant_revoke_consent` | `{projectEpoch}` | `{revoked:true,consentRevision}` |
| `assistant_new_conversation` | `{projectEpoch}` | `{conversationId,evictedConversationIds}` |
| `assistant_clear_conversation` | `{projectEpoch,conversationId}` | `{cleared:true,conversationRevision}` |
| `assistant_start` | `{projectEpoch,analysisGeneration,conversationId,requestId,configRevision,consentRevision,question,context}` | `{jobId,status,scope,conversationId,kind:'explanation'}` |
| `assistant_poll` | `{jobId,projectEpoch:string|null,afterSeq}` | strict `AssistantPoll` union below |
| `assistant_cancel` | `{jobId,projectEpoch:string|null}` | `CancelReceipt` below; receipt is not confirmation |
| `assistant_resolve_evidence` | navigation request in C6 | `{jobId,status,scope,conversationId,kind:'navigation'}` |

Frozen `get_state` recovery contract (Task6): use the existing request and `Envelope<AssistantState>`; no new bridge arguments or dependencies. Types below reuse C7's `ExplanationIdentity`, `NavigationIdentity`, `Lifecycle` and C6's validated `Answer`. Every object is closed, every listed field is required, and arrays are never null. Only `analysisGeneration` and the entire `grant` may be null in the state-specific fields; reused Answer/Lifecycle retain their declared nullable members.

```typescript
type StateMessageBinding = {
  jobId: string; projectEpoch: string; analysisGeneration: number;
  conversationRevision: number; configRevision: number; consentRevision: number;
  seq: number; stale: boolean;
};
type StateMessage = StateMessageBinding & (
  | { type: 'question'; text: string }
  | { type: 'provisional'; text: string }
  | { type: 'validated_answer'; answer: Answer }
);
type StateConversation = {
  conversationId: string; conversationRevision: number; messages: StateMessage[];
};
type StateGrant = {
  consentRevision: number; origin: string; allowedFiles: string[]; enabled: boolean;
};
type StateActiveJob = (ExplanationIdentity | NavigationIdentity) & Lifecycle & {
  consentRevision: number; seq: number;
};
type AssistantState = {
  projectEpoch: string; analysisGeneration: number | null; configRevision: number;
  conversations: StateConversation[]; grant: StateGrant | null;
  activeJobs: StateActiveJob[];
};
```

Identity and absence: top-level identity/config revision are current captured backend values; a foreign/retired project epoch fails with `AI_RESULT_UNAVAILABLE`, publication capture contention with `AI_ANALYSIS_BUSY`. A current project without eligible analysis, SDK, key, enabled AI or engine can still return its local conversations. Null generation means no generation has been assigned; an assigned but ineligible generation remains an integer. No configuration yet means configRevision 0. `grant:null` means no current matching grant (including revoked or superseded config); a retained current grant uses its exact revision/origin/sorted allowedFiles and enabled flag, without granting new authority. Empty state uses empty arrays, not an implicit conversation. IDs follow existing C7 bounds (job/conversation IDs <=128 characters); every revision/seq is a non-boolean JavaScript-safe integer >=0, and assigned generations are >=1. Grant origin/files reuse C2 validation and the consent receipt shape.

Messages are ordered safe display records only. The enclosing conversation supplies conversationId; every message must match its current conversationRevision and retains its original jobId, epoch, generation, config and consent revisions. Message epoch may differ from the current epoch when reopening the same project restores older inert display. For each original job there is at most one question followed by at most one assistant record; provisional is replaced atomically by validated_answer on successful validation, never duplicated alongside it. Text is the admitted question (1..2,000 characters) or bounded provisional display (<=64 KiB UTF-8); answer is the existing <=64 KiB validated public Answer, never raw model JSON. All messages for one job carry the same captured authoritative job seq, including after retirement; that seq is a recovery cursor, not a new event. Restore replaces the project display atomically, keys turns by (projectEpoch, conversationId, conversationRevision, jobId), and resumes only listed active jobs at their returned seq. Subsequent polls replace provisional text/answer from their authoritative snapshot rather than concatenate recovered text. Poll identity must match the resumed job's captured binding; historical display never becomes an active-job identity. Reject foreign identity or an older conversation revision for display updates; an older seq cannot overwrite a newer captured job snapshot. Clear removes all messages and increments revision, so late polls cannot recreate a cleared turn.

Stale and active jobs: stale is computed locally from the original binding versus current eligible publication/config/grant/conversation authority, never supplied by a model. Revocation/config replacement/clear retain their existing deletion and cancellation rules; any display that remains after invalidation is stale and inert. Restored Answer card flags describe prior evidence eligibility, never an issued action; stale disables every source/graph action regardless of those flags. Even a non-stale recovered answer requires a fresh resolve-evidence job and immediate consumption guards. State never contains a NavigationTarget. activeJobs contains only this epoch's explanation/navigation jobs that are Queued/Running OR still own unreleased resources, including terminal-but-closing jobs; connection jobs are excluded. Each entry keeps its original captured identity/revisions and seq, with existing C7 lifecycle invariants. A cleared conversation's older-revision cleanup job may remain here only to poll/cancel cleanup; it cannot restore messages. When a job also has displayed messages, their seqs match its summary in the same capture. Entries have no answer, text, target, tool data or extra result fields.

Capacity and disclosure: globally retain <=10 conversations and <=40 display messages per conversation, counting question and provisional/final assistant records within that limit. The complete compact UTF-8 Envelope<AssistantState> must fit 2 MiB, alongside the existing independently enforced 2 MiB logical store cap; display/result retention and any recovery metadata count under that existing store accounting. Capture a consistent in-memory snapshot before serialization. If any field/count/complete-envelope limit cannot be met, return fixed `AI_CAPACITY`; never silently truncate messages, answers, grants or job summaries. Normal admission may still evict whole inactive conversations under the existing policy. Project conversations are returned in store order; active job IDs are unique and bounded by existing admission limits. Only admitted questions, safe provisional text and validated answer/cards are display content: no raw excerpts, tool transcripts/results/arguments, source/metadata tags, retained proofs, internal root/ignore paths, provider wire/history/headers or key. Existing inert quotations in safe assistant display remain permitted by C7; this DTO never authorizes their retransmission.

Task6 internal composition ports for the core implementer: `_AssistantRuntime.get_state(ProjectRequest, ctx: AssistantContext)`, `grant_consent(ConsentRequest, ctx)`, `new_conversation(ProjectRequest, ctx)` and `clear_conversation(ClearRequest, ctx)` receive one controller capture made outside runtime/engine locks. Runtime constructs and owns exactly one ConversationStore even without SDK or worker threads. These four methods perform bounded memory-only validation/transactions and never construct an engine, import SDKs, invoke provider factories or start threads. New/clear/get_state work without a grant; grant validates the explicit selection against the captured B3 parsed/hash inventory, ignores and protected-file policy, then uses ConsentStore.grant, AuthorityState.from_trusted and SourceConsent. Disk/secret/freshness verification stays with existing B4/B5 worker gates. Reuse store register/clear/eviction/tombstones; do not make no-grant conversation operations depend on the authority-requiring `_set_conversation_revision` helper. Update conversation revision and cancellation/revocation eligibility atomically; drain actual cleanup after releasing state locks.

Engine construction remains deferred to an admitted background-work action and serialized outside publication/state locks. Inject the runtime's same store into AssistantService (a private constructor port); never allocate a second application store or copy histories into the engine. Before exposing the new engine, reconcile the latest mirrored publication/config/grant/conversation revisions; reject a superseded admission. Lock order is publication -> runtime -> engine -> store; ring/retained locks are downstream only. No reverse controller capture, runtime callback, clock, I/O, SDK await, task scheduling or resource close under these locks. Hashing/projection/serialization and AuthorityState preparation occur outside short state sections, followed by expected-revision checks; ConsentStore calls are outside engine/store locks. Publication mirror only swaps identity and marks bounded active work/pending invalidation; bulk store/retained scans and closure scheduling run in the existing lock-free drain. Capture retained ownership under store state, acquire any clock-creating lease outside state locks, then recheck authority before use; release rejected leases outside locks. This freezes only the recovery DTO and the required composition seam, without changing connection acceptance, production gate-off or SDK fail-closed proofs.

`context` is a strict discriminated union: `{kind:'question'}` / `{kind:'node',viewKind,visibleNodeId,rootId:string|null,detailSourceId:string|null}` / `{kind:'source',filePath,anchor:Anchor|null,mode:'function'|'file'}`. Question 1..2,000 chars, IDs follow A format, node IDs <=512 chars; unknown/extra fields rejected including sourceText, draft, key, url, arbitrary tool names. Visible grouping IDs are UI identities, not automatically B3 symbols: backend trusted context adapter uses controller structure resolver at publication/context mapping boundary and maps to bounded real code/semantic members, or refuses with source-only reason. Context map must not enumerate graphs under shared lock on start; publish mapping when existing views are built and bind to epoch/generation. If no map is available, require an actual code/semantic/symbol node or source entry; no false graph identity.

The poll schema is closed by job kind, including reset and navigation. The following are application DTO contracts to implement in Python validation and TypeScript, not SDK types. `Answer` and `NavigationTarget` retain C6's locally constructed schemas; `AIErrorCode` is exactly C7's taxonomy and `SafeIssue.message` is a fixed localized-safe string. `PublicUsage` contains reserved input/output/model/tool/source counters plus optional actual token counts with `basis:'actual'|'estimated'|'unknown'`; it has no body, fragments, tool args or headers.

```typescript
type JobStatus = 'Queued' | 'Running' | 'Completed' | 'Cancelled' | 'Failed' | 'Stale';
type ExplanationIdentity = {
  kind: 'explanation'; jobId: string; projectEpoch: string;
  analysisGeneration: number; conversationId: string; conversationRevision: number;
};
type ConnectionIdentity = {
  kind: 'connection'; jobId: string; projectEpoch: null;
  analysisGeneration: null; conversationId: null; conversationRevision: null;
};
type NavigationIdentity = {
  kind: 'navigation'; jobId: string; projectEpoch: string;
  analysisGeneration: number; conversationId: string; conversationRevision: number;
};
type JobIdentity = ExplanationIdentity | ConnectionIdentity | NavigationIdentity;
type SafeIssue = { code: AIErrorCode; message: string };
type PublicUsage = {
  reserved: { inputTokens: number; outputTokens: number; modelRequests: number;
    toolCalls: number; sourceCharacters: number; fileCount: number };
  observed: { inputTokens: number | null; outputTokens: number | null;
    basis: 'actual' | 'estimated' | 'unknown' };
};
type Lifecycle = {
  status: JobStatus; configRevision: number; consentRevision: number | null;
  cancelRequested: boolean; cancelAccepted: boolean;
  cancellation: 'none' | 'requested' | 'confirmed';
  dispatchClosed: boolean; resources: 'held' | 'closing' | 'released';
  resourcesReleased: boolean; issue: SafeIssue | null;
};
type DisplayEvent = { seq: number } & (
  | { type: 'status'; status: JobStatus }
  | { type: 'display_text'; text: string } // explanation only, provisional inert text
  | { type: 'usage'; usage: PublicUsage } // explanation/connection only
  | { type: 'result'; available: boolean } // marker only; payload is in snapshot
  | { type: 'lifecycle'; cancelRequested: boolean; cancelAccepted: boolean;
      dispatchClosed: boolean; resourcesReleased: boolean }
);
type ExplanationEvent = ExplanationIdentity & DisplayEvent;
type ConnectionEvent = ConnectionIdentity & Exclude<DisplayEvent, { type: 'display_text' }>;
type NavigationEvent = NavigationIdentity & Exclude<DisplayEvent, { type: 'display_text' } | { type: 'usage' }>;
type ConnectionResult = { toolSupported: true; structuredOutputSupported: true };
type ExplanationSnapshot = ExplanationIdentity & Lifecycle & {
  consentRevision: number; displayText: string; usage: PublicUsage; answer: Answer | null;
};
type ConnectionSnapshot = ConnectionIdentity & Lifecycle & {
  consentRevision: null; usage: PublicUsage; connectionResult: ConnectionResult | null;
};
type NavigationSnapshot = NavigationIdentity & Lifecycle & {
  consentRevision: number; action: 'source' | 'graph'; evidenceId: string;
  target: NavigationTarget | null;
};
type PollDelivery<E> =
  | { reset: false; seq: number; events: E[]; evictedConversationIds: string[] }
  | { reset: true; seq: number; events: []; evictedConversationIds: string[] };
type AssistantPoll =
  | (ExplanationSnapshot & PollDelivery<ExplanationEvent>)
  | (ConnectionSnapshot & PollDelivery<ConnectionEvent>)
  | (NavigationSnapshot & PollDelivery<NavigationEvent>);
type CancelReceipt = { seq: number } & (
  | (ExplanationIdentity & Lifecycle & { consentRevision: number })
  | (ConnectionIdentity & Lifecycle & { consentRevision: null })
  | (NavigationIdentity & Lifecycle & { consentRevision: number })
);
```

Snapshots above are flattened into the poll result to avoid divergent outer/inner identities or status. This supersedes C4's generic nested `snapshot` wording. Reset means the client atomically replaces its job display/status/result with this bounded authoritative snapshot at `seq`; it does not append old events/history. For delta delivery, event seqs are strictly increasing, above `afterSeq` and at most the returned current `seq`, and every event identity/revision exactly matches the snapshot. Event rev is the job's captured conversation rev; after clear that job cannot repopulate the incremented conversation. Every valid poll includes the authoritative bounded snapshot, even when there are no events. Sequence advances for resource/confirmation changes after a terminal result as well; stopping at terminal status alone loses release confirmation. Request `afterSeq` is a strict nonnegative integer, not bool, never greater than current seq. Kind-specific unknown/extra fields and cross-kind events are rejected; navigation/connection cannot carry explanation text/answers.

Lifecycle invariants: `cancelAccepted` iff `cancellation='confirmed'`; requested/confirmed imply `cancelRequested`; confirmation implies dispatchClosed and resourcesReleased. `resourcesReleased` iff `resources='released'`; all terminal jobs close dispatch, but may still be closing resources. Connection consentRevision is null; explanation/navigation require an integer. A new cancelled active job becomes Cancelled only at confirmation; late replies cannot make requested cancellation Completed. Result fields are null unless that kind is Completed and its validation succeeded; Stale clears actionable answer/target fields in poll while preserving the older explanation as inert conversation display. Navigation target binds epoch/generation/conversation revision/config/consent and C6's checkedAt/action/evidence, with AST Anchor/verified Monaco range or honest local reveal as appropriate. It is returned only in NavigationSnapshot, never as model output or streaming event. Consuming it still requires immediate local guards and B target-file recheck from C6.

Snapshot budgets remain bounded: explanation displayText <=64 KiB and final answer <=64 KiB; public issue/usage/identity fields bounded, navigation target <=8 KiB and connection result fixed booleans. Delta delivery keeps C4's maximum32 events/64 KiB limit; the complete encoded poll envelope has a160 KiB ceiling (including answer/display text/events/identity/eviction IDs), with reset events empty. Emit fewer delta events when needed to fit that ceiling; the authoritative snapshot/current seq still lets the client recover without retaining missing tokens. Enforce UTF-8 sizes of the complete serialized envelope as well as individual event limits. `get_state` returns at most10 safe conversation displays, their revisions and bounded grant/job summaries; each display message is a user question, provisional assistant text or validated assistant answer/card, not internal tool transcripts. Account the complete response within the existing2 MiB store cap. It never returns raw retrieved excerpts, ToolResult payloads, source tags, provider history/body/headers or key. Model quotations in safe assistant display are inert text; they do not become raw retrieval history or authorize navigation. Public event DTOs are separately constructed, never direct serialization of `ProviderEvent` or internal histories; no tool-source event kind exists. CSP/Markdown restrictions remain unchanged.

Poll verifies project/job ownership; null epoch only for source-free connection tests. Jobs/conversations unavailable after eviction return `AI_RESULT_UNAVAILABLE`. Error taxonomy: `AI_NOT_CONFIGURED`, `AI_DISABLED`, `AI_DEPENDENCY_MISSING`, `AI_ANALYSIS_REQUIRED`, `AI_ANALYSIS_BUSY`, `AI_ANALYSIS_STALE`, `AI_CONSENT_REQUIRED`, `AI_SOURCE_BLOCKED`, `AI_AUTH_FAILED`, `AI_RATE_LIMITED`, `AI_PROTOCOL_INCOMPATIBLE`, `AI_NETWORK_TIMEOUT`, `AI_EVIDENCE_INVALID`, `AI_CANCELLED`, `AI_BUDGET_EXCEEDED`, `AI_CAPACITY`, `AI_REQUEST_CONFLICT`, `AI_REQUEST_LIMIT`, `AI_RESULT_UNAVAILABLE`, `AI_INVALID_INPUT`. Translate A errors at service boundary; avoid widening A literal codes unnecessarily.

## D. Current UI adapters and lifecycle

`client.ts` wraps existing `call()` directly. `store.ts` consumes C7's strict kind union, one poll in flight per job at250 ms while visible/active, uses seq reset snapshots and rejects foreign kind/epoch/generation/conversation revision. Stop ordinary polling only after terminal AND resourcesReleased; after cancel receipt continue acknowledgement polling until cancelAccepted AND resourcesReleased, even while the status is still Queued/Running. If teardown remains unresolved, show cancelling/cleanup issue and use bounded backoff/attempts; never infer confirmation from local abort, timeout or elapsed time. Reopening can resume safe state polling without sending a model request. No token-by-token React component re-creation; updates batch per poll. Panel close requests cancel and preserves safe memory display; reopen/remount/React StrictMode never sends. Avoid auto Retry. Start retry after bridge timeout reuses exact ID/payload only when user requests recovery; present billing ambiguity clearly. Settings getters and offline browsing never instantiate provider or load SDKs.

`runtime.tsx` owns an external assistant-ui controlled runtime over this store; use the exact custom-runtime API verified against0.15.28 rather than assuming examples for a different version. Library callback `onNew` -> explicit send/new request, `onCancel` -> Python cancel, rendering history -> bounded store. AbortSignal only aborts local polling after Python cancel request is delivered; it is not a guarantee of backend cancellation. Strip library cloud/history/upload/tool defaults. UI streaming is provisional plain text; custom final content renders seven validated sections and local evidence cards.

`context.ts`: graph entry captures selected `VisibleNode.id`, view/root/detail island identity only; `App.tsx` passes local dirty flags as display-only state, never editor text to bridge. Source entry uses active source tab's filePath/AST anchor and mode; containing function resolved on backend saved analysis. Free question uses current analyzed project without selection. Opening explanation entry shows proposed context and send control; no external call until enable+consent+explicit send. Dirty flags are recomputed at start and during result display; unknown query dependencies => warn for any project dirty document. No draft values/history from Monaco included in DTO, logs or consent screen.

Right panel becomes discoverable `Inspector / AI Assistant` tabs and close button; render independently of `view` so free/source questions work while a source tab is active. Keep Inspector props/behavior and workspace source tabs. Add assistant entry to application toolbar and node Inspector, and saved function/file entry to SourceViewer. Configure in existing SettingsPanel via AssistantSettings; consent dialogue displays effective scope. Closing panel cancels and preserves memory conversation; Stop cancels but leaves panel; Clear/New are explicit; switching projects uses the existing dirty decision before backend project change, then cancels old jobs and restores that project's memory history without resending. If project-switch dirty guard is cancelled, retain current project; do not destroy drafts/history through speculative switch.

Backend cancellation/invalidation follows C3 before `_open_project` cache/project replacement, ignore changes, analysis start, `save_source_document` writes and file mutations (not solely after `_project_files_changed`), plus revoke/configure/clear/exit. External disk changes require B freshness checks; no reliance on bridge save callbacks alone. `_install_close_guard` requests AI cancellation only when existing source guard permits exit or on actual `closed`; close veto leaves app/key/history usable. Add private `_shutdown_assistant()` callback invoked after validated close approval and again idempotently on closed. It sets reject-new/cancelRequested bits immediately, drops key/history references, schedules C4 gate closure/response/client/B-worker cleanup, and never reports confirmation or joins inside the close callback. If native destruction fails, retain normal editor recovery and require fresh assistant configuration after resources were released. Bridge lifetime has injectable service and explicit shutdown for tests; no destructor-only cleanup.

## Implementation tasks (failure first, small reviewable slices)

Tasks are sequential within each subsystem; C fake-domain tasks can run while B completes. Each checkbox is one action; repeat red/green/review for each stated test family, rather than implement the entire runtime in one commit. Commands assume repository root and existing `.desktop-venv`; gate installs are explicitly isolated.

### Task 1: Record isolated candidate compatibility (core_implementer)

Files: gate scripts/report from file map; `tests/test_assistant_provider.py`, `frontend/src/app/assistant/runtime.test.tsx` initially in isolated probes.

- [ ] Add actual-installed-SDK capture/fake-transport probe for zero-network preparation, exact frozen body/output/source attribution, same-request single dispatch, redirect rejection and zero retries; add React abort probe against a fake external runtime. Use httpx2/OpenAI3 source/interfaces and record the chosen capture seam, not historical examples.

```python
response = await probe.send_mock(status=307, location='https://other.invalid/v1/chat/completions')
self.assertEqual(response.code, 'AI_PROTOCOL_INCOMPATIBLE')
self.assertEqual(probe.transport.request_count, 1)
self.assertFalse(probe.transport.redirect_followed)
```

- [ ] Run isolated gate scripts with `--isolated --fake-only`; expected explicit failure when the candidate cannot fulfill C1, not skipped compatibility checks. Gate scripts must implement these options and exit nonzero on failed required probe.
- [ ] Test preparation suspension/cancel/discard, final transport byte equality, pre-dispatch confirmed cancellation with zero sends, schema/tool loop control, output counting, CSP/native bundling and no-extras/offline startup; write exact installed extra/transitive lock/licenses and pass/fail/skipped in `docs/CODE_AGENT_DEPENDENCIES.md`. If the actual SDK cannot freeze/resume unchanged requests safely, mark the SDK gate failed and record the alternative for architecture decision; do not create a fake passing freeze API.
- [ ] Only if all required probes pass, edit the production optional ai extra/pins/locks/notices. Dependency failures produce concrete report/alternative decision for the main agent; no silent replacement.
- [ ] Reviewer inspects hidden SDK behavior and package licenses; main stages only this task's changed files for a checkpoint commit after review (no git operation in this planning task).

### Task 2: Config and consent boundary (core_implementer, test_writer)

Files: `config.py`, `consent.py`, `tests/test_assistant_consent.py`, `tests/test_assistant_provider.py`.

- [ ] Write strict URL/type, key non-return/log and per-project scope tests before implementation.

```python
grant = service.grant_consent(epoch='e1', allowed_files=('app.py',), confirmed=True)
self.assertEqual(grant.origin, 'https://provider.invalid:443')
service.configure(base_url='https://second.invalid/v1', model='test', key='test-only', enabled=True)
self.assertFalse(service.can_send(epoch='e1', grant_revision=grant.revision))
self.assertNotIn('test-only', repr(service.settings()))
```

- [ ] Run `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_consent.py -v`; expect assertions exposing absent grant/config behavior, not collection errors.
- [ ] Implement C2 immutable config/memory key/grant records, strict URL endpoint policy and revision invalidation; gate internal transport with fake provider only.
- [ ] Add hostile equivalence classes: URL credentials/query/fragment/control/encoded traversal, port/IDNA, deny-path metadata, changed origin/scope, unconfirmed grant, grant bool/int coercion and key replacement mid-send. Run both targeted test files, expected no failures.
- [ ] Review config/consent diff, checkpoint only reviewed files.

### Task 3: B adapters and analysis publication (core_implementer)

Files: `service.py` ports; controller/bridge publication; `tests/test_ui_controller.py`, `tests/test_assistant_integration.py`.

- [ ] Add cache/live snapshot equivalence, invalidate-before-analysis/cache/save/mutation barriers, capture-before-invalidate/admit-after-invalidate rejection, expected-generation/revision commit rejection and source dirty-not-uploaded fixtures. Assert no mixed generation/snapshot/map context can become eligible.

```python
old = bridge.assistant_binding()
bridge.analyze()
self.assertNotEqual(old.analysis_generation, bridge.assistant_binding().analysis_generation)
self.assertEqual(provider.send_count, 0)
```

- [ ] Run targeted controller/integration tests and confirm new expectations fail; implement publication and B adapters after B interfaces pass their own review.
- [ ] Verify no AST retention, original mutable graph modification cannot affect runtime facts, failed analysis/cache provenance disables new sends, grouping context maps are bounded and no UI/tool side effects.
- [ ] Verify B4 added/deleted/renamed/file identity/hash/UTF-8 range/protected path failure handling using B fixtures; C integration tests call the real delivered adapter, not mocks claiming freshness.
- [ ] Add real B4 metadata/history wire-gate fixtures: suspected secret beyond the chosen 200-line/4,000-character excerpt still blocks search names, semantic explanations, related metadata and their history retransmission. Include multi-file semantic/code-symbol/endpoint provenance, failed read/hash change/unknown dependency, safe outward counts/truncation with no denied names/counts, and proof that security-check buffers/receipts never enter provider input as free context. Pure B5 index tests remain zero-I/O.
- [ ] Coordinate B4 empty-only reader ledger checkpoints and B5 sole ToolSession attempt counter before enabling real adapters; prove rejected/seed attempts count once, one read/two sends count one tool/two source occurrences, and unread/unsent fragments are never actual disclosure. Reviewer validates publication lock order/atomicity, B/C budget ownership and generation semantics; checkpoint reviewed files.

### Task 4: Jobs, ring, memory and cumulative reservations (core_implementer)

Files: `jobs.py`, `service.py`; `tests/test_assistant_jobs.py`, `tests/test_assistant_service.py`.

- [ ] Write deterministic barrier/clock tests for quick start/getters/poll under analysis lock contention, identical/different replay, two-phase cancellation at the pre-transport barrier, event overflow and all global capacities.

```python
first = service.start(request)
self.assertEqual(service.start(request).job_id, first.job_id)
# Proposed deterministic local harness, not methods claimed on the SDK:
await harness.wait_until_prepared_before_dispatch(first.job_id)
receipt = service.cancel(first.job_id)
self.assertTrue(receipt.cancel_requested)
self.assertFalse(receipt.cancel_accepted)
confirmed = await harness.wait_for_confirmed_release(first.job_id)
self.assertTrue(confirmed.cancel_accepted)
self.assertTrue(confirmed.dispatch_closed)
self.assertTrue(confirmed.resources_released)
await harness.release_pre_dispatch_barrier(first.job_id)
self.assertEqual(confirmed.status, JobStatus.CANCELLED)
self.assertEqual(harness.underlying_transport_send_count, 0)
```

- [ ] Run each named test file to observe RED; implement bounded admission/maps/async worker and resource teardown in separate small slices.
- [ ] Add budget tests with one B4 read/two sends, invalid B5 attempts, unsent excerpts, history twice, repeated snippets, Unicode metadata, final SDK protocol/schema overhead, tool arguments, repairs and output allocation; assert every final wire body has exactly one C whole-task reservation and no hidden reserialization/retries. Separately cancel after transport entry: send count may be one, never two, resources must close and no billing reversal is claimed.
- [ ] Test256/257 events,8 KiB UTF-8 boundaries, reset snapshots, monotonic cursors,10/11 conversations,40/41 messages,2 MiB/1,000 global evidence across projects and1,024 idempotence tombstones. Include active eviction denial and late-result recreation after clear/revoke.
- [ ] Prove shared lock availability while provider is blocked using barriers with bounded joins in tests; poll/cancel work while analysis holds bridge lock; no network occurs on GUI thread or through the project write queue.
- [ ] Verify resources close on normal/failure/cancel/stale/shutdown; run foundations direct regression and reviewer checks concurrency/budget logic before checkpoint.

### Task 5: Strict answer and authoritative citations (core_implementer)

Files: `response.py`, `service.py`; `tests/test_assistant_response.py`.

- [ ] Write response fixture with genuine evidence plus a fabricated request ID and fake relation endpoints; assert failed validation and no action DTO.

```python
result = validator.validate(model_answer, scope=scope, provided_ids=(real_id,))
self.assertEqual(result.error_code, 'AI_EVIDENCE_INVALID')
self.assertEqual(result.actions, ())
```

- [ ] Observe RED, implement schema/ownership/range/typed node/relation validation; materialize only backend authoritative cards/facts.
- [ ] Test duplicate JSON keys/depth/size, missing sections, unsupported static edge claims, foreign-request/history IDs, old hashes, dangling node, false confidence, uncertain CALLS and semantic gradient semantics.
- [ ] Test the actual B5-to-ResolvedEvidence adapter with A lookup plus B5 lookup_binding: mismatch each scope/snapshot/hash/returned-anchor/truncated/target/provenance/status, and assert no action. Verify source Exact does not replace static confidence. A structurally valid registry entry without the actual session receipt is rejected.
- [ ] Test one receipt covering two real relations: C selects one target before wire, refuses a second mapping for that ID, and resolves relationEvidenceId only to the chosen provenance. A separate dispatch/receipt supports the second relation; same-ID repeated chosen target and relation-ID source action work, source_only/entity IDs fail relation claims, and model endpoints cannot repair the mapping. At the ninth attempted read, deadline/read-size/local registry/global-cap exhaustion, no further proof read/context is added; unproved facts stay non-clickable unknown. Verify proof verification adds zero extra source to wire and explicit repeated excerpts retain full tool/source/token accounting.
- [ ] Assert one bounded repair at most, repair/source/history token charge, failed repair remains Failed, budget exhaustion retains evidence with incomplete message and no invented successful answer.
- [ ] Reviewer compares each security claim with actual registry/B provenance and full approved response semantics, then checkpoint.

### Task 6: Bridge and lifecycle contract (core_implementer)

Files: bridge/controller/web_contract/desktop and their four test files; frontend types/client with `client.test.ts`.

- [ ] Add exact method/arity/envelope and C7 closed poll union tests: every kind/nullability/revision, delta/reset seq, navigation result branch, cancellation receipt versus confirmed release, terminal-but-closing polling and rejection of tool/source/internal-history DTOs. Add project lock contention and exit veto/approve/failed destroy tests; getters must have zero SDK/network/file work and no key/raw-source payload.

```typescript
await client.start(request);
expect(native.assistant_start).toHaveBeenCalledWith(request);
expect(projectWriteQueue).not.toHaveBeenCalled();
```

- [ ] Run targeted Python tests and `npm --prefix frontend test -- src/app/assistant/client.test.ts`; expect missing API/behavior assertions before implementation.
- [ ] Add methods and fixed assistant error conversion that bypasses raw `logger.exception`; extend allowlist/arity explicitly, preserve native dispatch token validation and CSP.
- [ ] Implement private close cleanup and analysis/file lifecycle hooks; simulate blocked provider and dirty-close veto, then confirm editor/window remains responsive and drafts survive.
- [ ] Targeted tests pass; reviewer checks API exposure and private lifecycle methods; checkpoint.

### Task 7: Controlled runtime, context and navigation (implementer/core_implementer)

Files: six frontend assistant modules and six tests from map, SourceViewer verified range seam.

- [ ] Add start-on-explicit-send, StrictMode remount/reopen no send, authoritative kind-specific cursor reset, switch/clear conversation-revision stale event rejection, requested-versus-confirmed cancellation and Python cancel-before-local abort tests. Keep resource/confirmation polling after a terminal status until the explicit release fields permit stopping; local abort/timeouts never imply confirmation.

```typescript
closePanel();
await waitFor(() => expect(native.assistant_cancel).toHaveBeenCalledTimes(1));
reopenPanel();
expect(native.assistant_start).toHaveBeenCalledTimes(1);
expect(store.current.messages).toHaveLength(2);
```

- [ ] Run `npm --prefix frontend test -- src/app/assistant`; observe RED. Implement custom assistant-ui API exactly as proven in gate, backed by controlled bounded store, no cloud runtime.
- [ ] Add context tests rejecting source text from SourceDocumentStore/Monaco; dirty state affects warning only. Node/group IDs go through trusted mapping; free/source entries work without graph view.
- [ ] Test non-BMP Unicode/UTF-8 byte columns ->1-based UTF-16, missing columns ->whole line, AST Anchor preserved, stale/hash mismatch/dirty draft disable source offset jump. Graph evidence without honest RevealTarget offers source-only reason.
- [ ] Test malicious HTML/link/image output stays inert and provisional tokens cannot invoke navigation; late navigation target rejected after project/save/generation change.
- [ ] Reviewer audits source/graph adapter mapping and history caps before checkpoint.

### Task 8: Sidebar/settings/entries and UX (implementer)

Files: four Assistant components plus App/Inspector/SourceViewer/SettingsPanel/i18n/styles; frontend component/integration tests in map.

- [ ] Write input/key-clear, unconfirmed-consent zero sends, seven sections/evidence cards, stop/clear/new/retry and source-view sidebar integration tests.
- [ ] Run `npm --prefix frontend test -- src/app/components/AssistantPanel.test.tsx src/app/components/AssistantSettings.test.tsx src/app/AssistantIntegration.test.tsx`; observe RED.
- [ ] Implement discoverable tab/close/settings/node/function/file/free entries using existing callbacks; localize zh-CN/en and distinguish all C7 error classes, coverage gaps, budget reservation vs actual usage, check time and best-effort cancellation.
- [ ] Reuse existing dirty guard for project/exit; no assistant action silently saves/discards. Closing and reopening retains memory; generation invalidation disables citations while preserving readable explanations.
- [ ] Run direct source/selection/project/bridge tests plus new UI tests; reviewer checks existing workspace behavior and accessible keyboard focus/labels/stop status before checkpoint.

### Task 9: Deterministic full-flow acceptance (test_writer; debugger for failures)

Files: `tests/test_assistant_integration.py`; add `tests/fixtures/assistant_project/app.py`, `helpers.py`, `dynamic.py`, `model.py`, `train.py`; frontend AssistantIntegration test.

- [ ] Build saved-file fixture containing entry -> validation -> conflict branch -> write -> result; fake provider must receive real snapshot-issued IDs, return references and verify source/graph clicks. No fixture user code is executed/imported; analyze statically.
- [ ] Add CLI conditions/recursion/cross-module/exception/dynamic unresolved paths plus nanoGPT-style model/training fixture; require unknown paths and AI intent separated from actual relation types.
- [ ] Add malicious comment instruction, forged evidence/tool names, protected path, HTML, changed/added/deleted inventory, cancel-before-send race, origin switch/clear, late cross-project response, exhausted budgets and network failure flows.
- [ ] Run targeted integration tests, then unchanged broad gate once per completed stage. Investigate root cause before fixes; reviewer inspects facts/permissions/late-result behavior beyond passing tests.

### Task 10: PC packaging and honest handoff (core_implementer + reviewer)

Files: E native/scripts/docs in map; `tests/test_assistant_packaging.py`.

- [ ] Add tests that source/wheel without extras imports and starts offline, absent key performs zero requests, key/source/history absent from caches/preferences/logs, package includes local UI/MIT notices and gate-passed optional SDK assets.
- [ ] Implement fake-provider smoke host/verifier injection through private constructor, never a public bridge method/environment endpoint bypass; apply conditional narrow PyInstaller hooks from successful gate, avoid collecting every AI provider.
- [ ] Run full gates below and native smoke against source, isolated wheel install and final `dist/CodeViz/CodeViz.exe`; desktop verification must exercise WebView2 close/dirty/source/graph behavior with fake provider and record screenshots/exit/cleanup. No browser-only substitute.
- [ ] Update security wording to “default no upload; explicit AI authorization sends only approved excerpts and permitted metadata/history”; synchronize README/PRD/UX/cache/limitations/guide/progress. Explain config/consent/scope, key reentry, no-extras/offline mode, every entry, Stop/Close/New/Clear, evidence/check times, dirty/stale and error remedies.
- [ ] Publish pass/fail/skipped plus retained pre-existing warnings and exact artifact path/dependency decisions. Real API acceptance remains explicitly skipped until user supplies configuration in app and actively approves disclosure; development/push permission is not API permission.
- [ ] Main agent inspects scoped diff, gets independent reviewer clearance and performs authorized integration/push as its own task; report actual remote status rather than assume success. No architecture-sensitive production changes are authorized by this read-only planning task itself.

## Required execution gates and evidence

Run affected files after each implementation slice; full applicable commands at C/D milestone and final E, not redundantly after every test-only edit:

```powershell
.desktop-venv/Scripts/python.exe -m ruff check src tests scripts/verify_assistant_dependencies.py scripts/assistant_smoke_host.py scripts/verify_assistant_native.py
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
.desktop-venv/Scripts/python.exe -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir build/assistant-wheel
./scripts/build-desktop.ps1 -CheckOnly
./scripts/build-desktop.ps1
.desktop-venv/Scripts/python.exe scripts/verify_assistant_native.py --source --fake-only
.desktop-venv/Scripts/python.exe scripts/verify_assistant_native.py --wheel-dir build/assistant-wheel --fake-only
.desktop-venv/Scripts/python.exe scripts/verify_assistant_native.py --exe dist/CodeViz/CodeViz.exe --fake-only
```

Create task-specific isolated wheel envs for base-only and ai-extra variants; resolve locked extras from gate-approved package cache for offline install, report unavailable cache as skipped rather than downloading silently. Newly added scripts must support exactly the listed options. Build/native network must never contact a provider; fake transport tests prove redirects/TLS policy without accepting live HTTP. Real Ubuntu unittest/wheel and supported Linux source-handle cases remain required release evidence; mypy `--platform linux` is only type checking. Existing Windows symlink privilege/environment skips stay separately reported. Retain current test/CI coverage and existing frontend chunk warnings; no dependency migration to fix unrelated issues.

Guide must identify the final launcher `dist/CodeViz/CodeViz.exe` and distributing its complete directory, not a stale executable from a previous build. Missing native SDK/assistant-ui gate or final package evidence means that capability is not yet accepted; ordinary offline application and fake-domain tests can still pass.

## Spec coverage and architect self-review

| Approved spec section | Implemented contract / acceptance |
| --- | --- |
| 1 goal and2 reuse/locks/cache | B adapters/publication, C3/C4, current workspace adapters |
| 3 dependency selection | C1 pinned isolated gates, optional imports, explicit decision report/MIT |
| 4.1 entries/status and4.2 response meaning | C3/C6, D entries, Task9 generic/ML fixtures |
| 4.3 chat and5 jobs/tool limits | C4/C5/C7, D controlled runtime, Tasks4/7/8 |
| 6 evidence/endpoints/coordinates/no graph writes | C3/C6, Task5 and navigation tests |
| 7 disk/dirty/version/dependency inventory | C3/C4/C6 and Task9 change races |
| 8.1 target/key and8.2 permission/injection | C1/C2/C5/C7, Tasks1/2/6, synchronized E docs |
| 9 fault isolation/exit/offline | C1/C7, D lifecycle, Tasks6/10 |
| 10 acceptance/delivery and11 stages | Tasks1..10, required gates, role routing and main execution |

Revision self-check: full approved spec and runtime plan read. Four prior blockers now have explicit planned contracts: coordinated B4/B5/C charge ownership, requested/confirmed cancellation at the loop dispatch boundary, single immutable context with invalidate-before-mutation/expected-commit guards, and closed kind-specific poll/reset/navigation/release DTOs excluding raw tools/history. C1's exact-wire contract now prepares/captures the final installed-SDK body before authorization/reservation and conditionally resumes the same request; its feasibility is unproven until the isolated actual-version gate. B3 is not a freshness/authorization proof; A registry is not an authoritative relation store; React peer metadata/isolated installation is not desktop compatibility. The main agent's isolated SDK installation is acknowledged; this revision itself installs nothing and changes only this plan, no production code/tests/git or B4 files. Test excerpts are future local-harness assertions, not passed tests. This author's self-check does not replace the required separate independent architecture review. Production runtime remains gate-off pending that review, coordinated B contracts, dependency gates and integration/native acceptance.
