# Project understanding implementation plan

2026-10-29 UI supersession: the hierarchy recovery plan
`2026-10-29-project-understanding-hierarchy.md` replaces the shared standalone AI
canvas and AI-mode static-query suppression below. Original ownership/proof,
privacy and backend contracts remain applicable; completed implementation history
is retained here rather than rewritten.

Process: AGENTS.md autonomous mode; no per-task reviewer, no automatic commit or
push. Technical authority: the project-understanding design spec and architect
GO in build/project-understanding-20261028/architecture.md. Seven model sections,
current budgets/repair and source-security requirements remain mandatory.

## Phase P1 — project retrieval, artifact and main Overview

### Task P1.1 — explicit project intent and multi-file evidence (core_implementer)

Goal: a real project request must not be treated as a single-file selection or
the entire question string as a symbol-search query.

Likely files: assistant/bridge_requests.py, jobs.py, _explanation_context.py,
service.py; new assistant/project_retrieval.py; index_tools.py/tools.py only if a
private project-only diversity strategy is needed. Existing ordinary query
behavior and three public model-tool schemas remain compatible.

Contract: add the closed context {kind:"project"}; preserve question/node/source.
Job identity/digest records project purpose explicitly. Build a bounded worker
retrieval plan over current immutable snapshot and consent, using existing tool
authorization/projection/provenance/wire reservation paths. Select representative
different files and entry/dependency roots, not a single filename's top-k.
Do not place unverified path/symbol strings in untagged synthetic tool arguments.
Record any necessary internal strategy choice here before implementation.

Tests: one failure-first synthetic three-file import/call/entry scenario. Assert
two-file metadata/source exposure and real cross-file proof, unchanged ordinary
contexts, denied/foreign metadata never on wire, source/metadata/attempt quotas,
budget truncation and unsent fragments not counted as model coverage. Run only
new retrieval/context tests and direct seed/budget/source consumers.

### Task P1.2 — owned project understanding reference (core_implementer)

Likely files: new assistant/project_understanding.py; service.py and its actual
state/public serialization owners, conversation capacity/invalidation helpers.
Reuse validated Answer and retained owner; do not copy source, mint evidence,
permanently pin conversations or allocate a second unbounded memory store.

Public assistant state gains optional projectUnderstanding; absent/null means
no effective result, and older ordinary states remain accepted by the new UI.
Its closed version-1 reference includes artifactId/revision, projectEpoch,
analysisGeneration, owner conversationId/conversationRevision/jobId,
configRevision/consentRevision and coverage. Content is resolved from the same
owner's validated answer, not duplicated arbitrary model output. Fix exact
JSON keys/types/limits in this plan before writing serializers.

Frozen P1 public extension (no change to model answer/poll/cancel schemas):

- `projectUnderstanding` may be absent or null. Otherwise it has exactly
  `schemaVersion:1, artifactId, revision, projectEpoch, analysisGeneration,
  conversationId, conversationRevision, jobId, configRevision, consentRevision,
  coverage`. All IDs use the existing bridge bounded identifier grammar. All
  revisions/counts are non-bool safe integers; artifact revision and analysis
  generation start at 1. Revision increases for each successful replacement
  within a project epoch, never silently wraps. The artifact ID is server minted.
- `coverage` has exactly `indexedFileCount, authorizedFileCount,
  metadataFileCount, sourceFileCount, gapCodes`. Counts are nonnegative safe
  integers. Authorized counts the current parsed `.py` inventory intersected
  with the grant; metadata/source count distinct files actually forwarded.
  Both forwarded counts are <= authorized <= indexed. `gapCodes` is a unique,
  sorted subset of `representative_sample, retrieval_gap, unverified` (max 3).
  These local display counts are not provider context or proof of full reads.
- The owner conversation/revision must exist with exactly one non-stale
  `validated_answer` for jobId matching all epoch/generation/config/consent
  bindings. No answer body/source/evidence copy is added to the reference.
  Reference bytes participate in the same ConversationStore capacity transaction;
  failed retention never publishes or replaces the prior effective reference.
- Only `get_state.activeJobs` explanation entries for explicit project tasks
  gain optional `purpose:"project_understanding"`. This is a closed optional
  extension; ordinary entries omit it and navigation entries cannot carry it.
  Other poll/start/cancel payloads keep their existing exact schemas. Frontend
  strips and validates this state-only field before existing receipt validation.
- Effectiveness depends on project/generation/config/grant and retained owner,
  not the current view-map revision or selected chat. Public state omits an
  ineffective reference. Retention removal/clear/eviction removes its ownership;
  a view-only publication may invalidate a short navigation job but does not
  delete this project understanding. No permanent lease or owner pin is added.
- Item IDs for later phases are server-derived `project` or
  `<section>:<zero-based claim index>` with a canonical decimal index and an
  existing seven-section kind. Item authority is resolved against this exact
  retained answer/revision, never accepted from UI/model supplied membership.

Coverage distinguishes indexedFileCount (local only), authorizedFileCount,
metadataFileCount, sourceFileCount and fixed gap codes. Source fields mean
fragments actually included in dispatched wire, not security scans, planned or
unsent snippets, or complete file understanding. No denied names/counts leak
into provider context. Additional reference bytes count within existing caps.

Commit only with the original retained-answer transaction/freshness/CAS. Keep
one effective artifact for current project, atomically replace on successful
explicit project tasks only. View-only publication does not destroy it.
Clear/evict/revoke/config/project/analysis invalidation removes effectiveness.
Tests cover commit failure, stale/cancelled late result, replacement, view-only
map changes, owner invalidation and memory accounting with fake providers only.

### Task P1.3 — primary Overview and project task ownership (primary)

Files: frontend assistant/types.ts, context.ts, client/state strict decoders,
store.ts, runtime.tsx; App.tsx, Overview.tsx, AssistantPanel.tsx and i18n.ts.
Add explicit project intent through the existing single controlled send entry;
remove project-prompt string matching and default setGraphOpen(true).
Main Overview consumes effective artifact + owner Answer and coverage while
leaving static health/framework/model fields intact. Expose project progress
and Stop in main area. An explicit user action is still needed to send.

Use the architect's project-owner supplement: project analysis is owned by the
current main project, ordinary chat by its panel. Closing the panel cannot
hide an uncontrollable paid job, fake visible=true, add a second start entry or
cancel a separately owned main task. Project/app close or authority/generation
invalidation cancels all corresponding work; existing resource acknowledgement
rules apply. No automatic send after configuration or consent becomes ready.

Tests: static initial Overview, explicit project start/deduplication, validated
cross-file result updates same Overview, panel hide/project Stop, failed result
retains baseline/old effective result, no auto dialog, stale responses ignored.
Phase gate: affected tests, one implementation review, full backend/Ruff/both
mypy platforms and frontend suite/lint/typecheck/build once on final snapshot.
Append <=5 progress lines, then continue P2. No package/live claim yet.

Intentional legacy-test correction: AssistantArchitecture's sidebar popup and
localized-prompt recognition are superseded by server-owned references in the
primary workspace. Preserve its isolated source-close test; replace its popup
workflow cases with no-popup/prompt-is-not-authority, explicit project context,
ordinary-answer exclusion and explicit node-context restoration checks. The new
main-view tests additionally cover same-owner state rendering and authority loss.

P1 gate scheduling decision: the default concurrent frontend run reported nine
UI/timing failures plus one ordinary first-chat stale-error regression. Preserve
that regression's existing rejection contract in code. All nine failed files
(63 tests) then pass with two workers, without changing assertions or timeouts.
Cap Vitest workers at two for reproducible local/CI resource use; no tests are
removed or skipped. Retain the failed gate log and repeat the frontend gate once
on the corrected snapshot. Backend passing evidence is independent and reused.

## Phase P2 — same-version primary views and independent source navigation

### Task P2.1 — artifact constrained navigation (core_implementer)

Files: bridge_requests.py ResolveRequest/parser; service.py navigation admission,
job/replay/eligibility/poll lifecycle; internal job identity where actually owned.
Add optional understanding={artifactId,revision,itemId}, exact bounded IDs and
non-bool revision, no unknown keys. Missing field retains original behavior.
The outer conversationId must equal the active artifact's server-owned owner;
evidence must belong to that item's verified references. Apply the same artifact
constraint at admission, completion and replay, then original C6 lease/B5/
freshness/authority checks. Preserve capacities, no automatic retry.

Frozen P2 request extension: when present, `understanding` is non-null and has
exactly `artifactId` (existing bounded bridge ID), `revision` (non-bool safe
integer >=1), `itemId` (`project` or canonical existing-section decimal index,
<=128 characters). Missing retains the original C6 path; no new poll/start/
cancel response fields or public error codes are introduced. Malformed shape
is invalid input; unavailable/stale ownership follows the existing safe C6
failure model and never silently drops the constraint.
The server derives item membership from the original typed Answer: `project`
can reference its validated evidence set; a claim item can reference only its
own evidenceIds/relationEvidenceId that also exist in that answer. Being an AI
group does not confer graph authority. Binding the exact artifact/item/version
and retained owner in job identity is required at admission and every later
eligibility/replay/completion check, including after source freshness work.

Tests: wrong/old artifact, wrong item/owner/receipt, replacement/revoke/clear,
publication changes, cancellation and release. Do not create view maps in worker.

### Task P2.2 — main structure/architecture layer (primary)

Files: App.tsx, Overview.tsx, main GraphEditor integration, Sidebar.tsx status;
new frontend project-understanding projection/canvas/inspector modules and
assistant/workspace-navigation.ts; existing client/poll/navigation intent reuse.

All three original primary views consume one effective artifact revision, with
static/AI-enhanced switch. ProjectStructure and generic Architecture project
validated summary/entry into explicitly inferred logical groups and related
source/evidence. Other sections remain explanations. No free member/link schema.
Only original RelationFact/static dependencies support edges, and group mapping
is separately inference. Ambiguous or unsupported relations become gaps; never
use old decorative root-to-claim/step arrows as dependencies. Limit visible
nodes to <=100, keep AI layout in memory and out of static save_layout.

Embedded logical-canvas details replace the stale static Inspector while this
layer is active. Explicitly opening the question sidebar remains available.
With the seven-section MVP schema there is no exact group-to-endpoint membership;
therefore ambiguous RelationFacts stay in a verified-source details list and
produce no group dependency arrows. Do not manufacture a root/step flow to fill
the canvas. Graph navigation explicitly switches to the static layer to consume
the original native destination.

Main navigation consumer key is projectEpoch/generation + artifact/revision +
origin view/tab, with server owner revisions. It is independent of sidebar
visibility/current chat; tab close/switch, model replacement and authority loss
cancel/reject late actions without stealing focus. Do not silently downgrade
artifact requests to ordinary navigation or bypass proof with source anchors.

Tests: primary views update/default layer/toggle exact baseline, ordinary project
without model roots, unsupported inference/relationships, panel hidden/chat
switch navigation, source dirty/closing guards, tab cancellation and no leaks.
Close with one review and applicable phase gate once; continue P3.

## Phase P3 — item-scoped follow-up and final delivery

### Task P3.1 — typed project item context (core_implementer)

Add closed context {kind:"understanding",artifactId,revision,itemId}. Resolve it
from current effective artifact, not a title pasted by UI. Compile bounded prior
interpretation as untrusted, source-provenance-tagged context; apply fresh scope/
source checks and wire accounting to all reused data. Old evidence IDs are never
new provided receipts. Tools re-read/re-bind current evidence. Include reference
in request digest; reject old/foreign/cleared references without model dispatch.
Ordinary follow-up does not produce a new artifact; only explicit project update.

Frozen P3 extension: context has exactly `kind:"understanding", artifactId,
revision, itemId`; reuse the P2 UnderstandingRef bounds/grammar. The question's
new conversation may differ from the artifact owner (selected chat is not the
project model owner). The server must resolve and preserve both identities,
recheck the artifact/item owner before dispatch and final commit, and reject a
replacement/clear/revoke while the question is in flight. Keep this an ordinary
explanation purpose; never overwrite the project artifact from a follow-up.
Only a bounded prior interpretation (<=1000 Unicode characters in total) may
enter context, explicitly untrusted and derived from the current verified owner.
No old receipt IDs are provided as fresh evidence. Reused data must follow an
existing valid provenance/accounting path; if a new wire provenance contract is
needed, obtain the architect's concrete decision before implementing it. Do not
label generated prose as exact source or relax validators/budgets/history guards.

Architect's concrete conditional GO is P3-architecture.md in the delivery area:
introduce one private prior-interpretation origin with the original full wire
dependency closure, an internal interpretation occurrence, and atomic original
source-character/file accounting. Public sourceCharacters includes charged
derived interpretation characters; the UI label explicitly says both. The
usage test changes only this label and retains its exact numeric assertion.
An artifact without a provable origin cannot supply prior generated text.

Tests: cross-file follow-up through a main item, forged/stale IDs, old scope,
prompt injection as data, proper new provided/ledger and no unintended replacement.

P3.1 private wire contract (Raman conditional GO): implement the complete
`build/project-understanding-20261028/P3-architecture.md` derived-origin decision.
Freeze each original answer's possibly dispatched source/metadata/derived dependency
manifest in its existing atomic retention transaction. A job-owned immutable
prior-interpretation origin binds that manifest, exact answer occurrence/digest,
artifact/item and both owner identities; old artifacts without proof fail closed.
Only private interpretationTags/interpretation_occurrences and internal
BudgetReservation derived_characters/derived_files extend the wire/accounting
ports; existing public usage/limits, source/metadata meaning and history guards
remain unchanged. The same origin closure and per-occurrence character count
must reach preview/headroom, real SDK mapping, admission and dispatch/commit.

### Task P3.2 — main-item Ask and hardening (primary)

Wire main group/overview Ask to the existing switchable right assistant using
typed item context. Questions use the project result and dependencies even when
selected source is one file. Closing/reopening sidebar preserves main result.
Retain static/AI mode and source traceability, honest incomplete coverage.

End-to-end fixtures: generic Python three-file app plus small PyTorch layout;
static initial state -> project purpose -> main updates -> panel close -> source
reference -> typed detail question -> stable project version. Preserve no AI,
Partial, cancelled/failed jobs, clear/revoke/reanalyze and capacity behaviors.
One implementation review and final full backend/Ruff/Windows+Linux mypy and
frontend gate. Build isolated package, verify source/assets/TLS and fake-only
SDK probes. Native/UI/manual and any real-provider request are separate evidence;
do not repeat unchanged failed native automation. No real source/API requests
without the user's specific destination/scope/fee consent. No commit/push.

## Final acceptance and boundaries

The three original main views visibly reflect a validated project-level result;
the right panel is a question surface, not an independent architecture popup.
Cross-file coverage and dependencies have evidence. Static facts and real files
are unchanged/recoverable. Source links work without the sidebar; follow-up is
bound to the displayed artifact/version and obtains fresh evidence.
Cross-restart persistence, unrestricted group editing, complete-runtime/shape
observation, other-language/config parsing and arbitrary full-repository uploads
are outside this MVP. No amount of local software tests implies those acceptances.

## 2026-10-29 — user-observed desktop defects

Repair only the observed project-understanding slice. The real running window
showed hidden AI cards, an INVALID_REQUEST banner and a later ordinary response
ending with provider-reported length. A real Edge scene using the old production
assets proves cards exist with nonzero geometry but visibility:hidden. Retain
React Flow dimensions across controlled-node updates and test selection without
another resize. While an effective AI primary graph is displayed, do not issue
unneeded static graph queries; returning to Static analysis restores that query.
Do not hide genuine errors or claim the historical invalid request's exact
arguments were recovered. Isolated cached static queries remain independently
testable without executing the project or modifying its original cache.

The architect's output-phase decision is recorded in
`build/project-understanding-20261029-diagnostics/output-phase-decision.md`.
Make the existing tools-enabled retrieval phase explicitly request only needed
tools or short ready text; defer synthesis/seven-section JSON to the existing
tools-disabled final request. Keep the policy conditional in the retained system
message. Do not skip ordinary retrieval merely because the deterministic seed
exists, increase budgets, introduce retries, or accept length-truncated output.
No actual response body was read; a premature JSON answer is a risk, not an
established explanation for the provider's length termination.

Close this repair slice with affected tests, one boundary review, backend and
frontend gates, production-browser geometry/selection/resize-fit checks and a
separate source/assets/TLS-matched offline package. Reuse unchanged passing
evidence and retain initial failure logs. Keep the user's running process and
its memory key intact; no real provider/source request is authorized here.
