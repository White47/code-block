# Estimated input budget implementation plan

> Execution process: AGENTS.md wins. Do not load the prohibited execution skills,
> dispatch routine reviewers, change old tests, or commit/push without a new request.
> This document is a Task10 continuation, not a new product or API permission.

**Goal:** implement the authorized estimated-input-budget contract without
misrepresenting it as an upper bound or weakening disclosure/output controls.

**Architecture:** immutable provider metadata binds an estimate to final wire
bytes; a private per-job accounting policy separates estimated admission from
reported actual usage. Service/connection own dispatch and cleanup; a closed
poll union tells the UI which meaning the reserved input number has.

**Tech stack:** current Python/unittest and React/TypeScript/Vitest; stdlib
hashing and JSON only. No new dependency, tokenizer, SDK profile or live request.

**Governing contract:**
`docs/superpowers/specs/2026-10-21-estimated-input-budget-design.md`.
The user authorized that budget policy change; this turn only writes the design
and plan. Implementation starts at Task 1, not at production factory enablement.
Work in the existing workspace, preserving all pre-existing uncommitted work.

## Ownership and dependencies

Tasks 1–4 and Task 5's backend projection are cross-module core implementation:
route to `core_implementer`. Main agent owns frontend wiring/docs/integration.
Pascal's architecture consultation approved the formula/record/facade/closed
union design; no implementation or E release acceptance follows from it.

Dependency order: 1 → 2 → 3 → 4 → 5 → 6. Do not run a full suite at every task.
No task edits parser/resolver/graph IR/source_handles or analyzes by execution.

## Task 1 — Frozen estimate records and shared wire inspection

**Goal/deliverables:** explicit accounting metadata and a pure validator that
preserves the original strict budget and source checks.

**Files:** modify `src/codeviz/assistant/provider.py` and the value-class
import/re-export only in `src/codeviz/assistant/poll_public.py`; create
`src/codeviz/assistant/_usage_values.py`,
`src/codeviz/assistant/_wire_accounting.py`,
`tests/assistant_estimated_fixtures.py`,
`tests/test_assistant_estimated_input_records.py`.
Move only the two usage value records described in the spec into `_usage_values`;
re-export the existing `ObservedUsage` from `poll_public.py` atomically in this
task. Keep all imports pointing to the same canonical class, not two copies.
No new public usage shape is emitted before Task 5.

- [ ] Write failure-first tests for valid strict and estimated inspections,
  source attribution still false, mismatched hash/size/profile and both/missing modes.
- [ ] Use this complete synthetic fixture as the new test-only helper. No project
  source, external code, credentials or network is involved:

```python
import hashlib
import json

from codeviz.assistant._wire_accounting import TrustedEstimatedProfile
from codeviz.assistant.provider import EstimatedInputAccounting, PreparedRequest

PROFILE = TrustedEstimatedProfile(
    "estimated-fixture-v1", "https://estimated.invalid:443/v1/chat/completions",
    "estimated-fixture")

def prepared(size: int, identity: str = "one") -> PreparedRequest:
    body = json.dumps({"model": PROFILE.model,
        "messages": [{"role": "user", "content": "x"}]},
        separators=(",", ":")).encode("utf-8")
    if size < len(body):
        raise ValueError("Synthetic body size is too small")
    body += b" " * (size - len(body))
    estimate = EstimatedInputAccounting(PROFILE.profile_id, "wire-bytes-v1",
        len(body), hashlib.sha256(body).hexdigest())
    return PreparedRequest(identity, "POST", PROFILE.endpoint, body, 32,
        1_000_000_000.0, None, (), (), True, estimate)
```

- [ ] Example regression inside a unittest case:

```python
from dataclasses import replace
from assistant_estimated_fixtures import PROFILE, prepared
from codeviz.assistant._wire_accounting import WireAccountingError, inspect_wire

request = prepared(1000)
accepted = inspect_wire(request, trusted_profiles=(PROFILE,))
self.assertEqual(accepted.reservation.input_tokens, 1000)
bad = replace(request, estimated_input_accounting=replace(
    request.estimated_input_accounting, estimated_input_tokens=999))
with self.assertRaises(WireAccountingError):
    inspect_wire(bad, trusted_profiles=(PROFILE,))
with self.assertRaises(WireAccountingError):
    inspect_wire(request, trusted_profiles=())
```

- [ ] Run RED with `unittest discover -s tests -p test_assistant_estimated_input_records.py -v`.
  Initial failure must be the missing record/validator, not an accidental dependency.
- [ ] Add `EstimatedInputAccounting(profile_id, method, estimated_input_tokens,
  body_sha256)` and append `PreparedRequest.estimated_input_accounting=None`.
  Implement exact field/XOR/hash/body/model/endpoint/profile checks from spec §7.
- [ ] Extract the original final-wire/source occurrence validation into
  `inspect_wire()`. Do not remove duplicate-key/fragment/metadata checks.
  Return `WireReservation(reservation, files, mode, profile_id, method)`.
  `wire_reservation()` returns the original two-tuple for compatibility.
- [ ] Define fixed-code `WireAccountingError` locally; never import service
  into this module or include body/source/SDK exception text in its message.
- [ ] GREEN and direct consumers: new file, `test_assistant_service_executor.py`,
  `test_assistant_poll_public.py` and `test_assistant_connection_runner.py`.

**Acceptance:** unknown trusted profile or incomplete provenance still refuses;
strict callers keep the original two-tuple and default constructor meaning.
**Out of scope:** SDK `_capture`, actual profile registry, changing factory or tests.

## Task 2 — Per-job cumulative accounting policy

**Goal/deliverables:** implement the frozen mode and the max-of-estimate/actual
admission rule, with irreversible unknown and no refunds.

**Files:** `_wire_accounting.py`; new
`tests/test_assistant_estimated_input_policy.py`.
Depends on Task 1's exact records and test helper.

- [ ] Add one failure-first test for each changed behavior: cumulative admission,
  actual zero vs unknown, finalization after missing usage, and legacy strict façade.
  Keep the examples small, not dozens of sub-assertion risk groups.
- [ ] The central test uses only the synthetic helper and pure state:

```python
from assistant_estimated_fixtures import PROFILE, prepared
from codeviz.assistant._usage_values import ObservedUsage
from codeviz.assistant._wire_accounting import WireAccountingError, WireAccountingPolicy
from codeviz.assistant.budget import BudgetLedger

ledger = BudgetLedger(clock=lambda: 0.0)
policy = WireAccountingPolicy(ledger, trusted_profiles=(PROFILE,))
policy.admit(prepared(1000), "ordinary")
policy.dispatch("one")
policy.observe("one", ObservedUsage(19000, 1, "actual"))
policy.finalize("one")
with self.assertRaises(WireAccountingError):
    policy.admit(prepared(2000, "two"), "ordinary")
self.assertEqual(ledger.usage.model_requests, 1)
self.assertEqual(policy.snapshot.blocked_reason, "estimated_limit")
```

- [ ] Run RED with `unittest discover -s tests -p test_assistant_estimated_input_policy.py -v`.
- [ ] Implement `WireAccountingPolicy` interfaces from spec §8. Preserve old
  phase/output/file/request/preparation-id protections; fix mode/profile/method
  after first admission. Serialize one active send per policy.
- [ ] Estimated admission algorithm:

```python
if not self._actual_complete:
    self._block("usage_unknown")
base = max(self._reserved_estimate, self._known_actual_input_tokens)
if base + estimate > self._input_limit:
    self._block("estimated_limit")
self._ledger.reserve(reservation)
self._reserved_estimate += estimate
```

  `_block` stores the reason and raises fixed `AI_BUDGET_EXCEEDED`; no mutation
  of the ledger occurs before its atomic reserve succeeds. Existing reservations
  are never decremented. All expensive validation precedes short state exchanges.
- [ ] `dispatch(id)` marks a potentially sent request once; `observe(id, usage)`
  validates and records once before deciding input overrun. Actual input above
  estimate is allowed only on estimated mode; output overflow is still an error.
  `finalize(id)` records sticky unknown if dispatched without actual and clears
  the active send idempotently. Preparing/admitting a never-sent request does
  not invent actual billing, but still does not refund reservations.
- [ ] `report_usage(reservation, dict)` remains strict-only and callable directly
  after admit, preserving the old no-dispatch test contract. Estimated calls to
  that façade reject. `observed` never washes unknown into actual later.
- [ ] GREEN plus direct strict executor tests; actual 21000 on a 1000-byte
  estimated send must be retained, block subsequent calls and not throw merely
  because the input estimate was low. Reverse estimate/actual case does not refund.

**Acceptance:** `R=1000,A=19000,E=2000` and the reverse both refuse; actual 0 is
known, missing/estimated usage prevents more sends. Strict unknown usage retains
its prior proof-backed admission behavior; truthful aggregation is still sticky.
**Out of scope:** raising numeric limits, new observed error strings or paid calls.

## Task 3 — Explanation lifecycle and frozen-field integration

**Goal/deliverables:** apply policy at the existing owned dispatch boundary and
preserve accounting through final/repair/error/cancel without losing usage.

**Files:** `src/codeviz/assistant/service.py`, `src/codeviz/assistant/jobs.py`;
new `tests/test_assistant_estimated_input_service.py`. Depends on Tasks 1–2.

- [ ] Failure-first regression: an actual-overrun tools response stops before
  another prepare/dispatch; a complete valid final with unknown/overrun may
  complete with a warning; an invalid final under that condition never repairs.
  Use application-owned existing service fixture setup and a provider double
  which constructs Task 1's exact metadata for each final body, not a boolean bypass.
- [ ] Run `unittest discover -s tests -p test_assistant_estimated_input_service.py -v` RED.
- [ ] Add keyword-only `trusted_input_profiles=()` to `AssistantService` only;
  validate/capture the tuple outside locks. Existing runtime/desktop calls keep
  the empty default, so no real profile becomes enabled.
- [ ] Retain service's `_wire_reservation` and `_SendPolicy` thin wrappers. Convert
  `WireAccountingError` to the original `ServiceError` code without importing
  service from the pure accounting module. Ledger AgentError mapping stays intact.
- [ ] Add optional `input_accounting=None` at the end of job records and, in Task 5,
  poll records; preserve old constructors and retired-record snapshot behavior.
- [ ] Refactor only the current send/usage/finally path to this sequence:

```text
ensure_can_prepare → prepare → admit → existing consent/freshness/frozen guards
→ dispatch(id) → provider.dispatch → validate usage → observe(id, usage)
→ publish immutable observed/accounting snapshots
→ finally: finalize(id), publish final accounting, run existing owned cleanup
```

- [ ] Freeze all four new metadata primitives, not an object reference, at every
  before/after await and dispatch comparison. Refuse profile/mode/body/digest
  mutation; verify source and metadata attribution exactly as before.
- [ ] Before ordinary/final/repair preparation, consult the blocked state.
  A complete final still must pass seven-section/evidence validation; budget
  warnings alone do not discard it. Any needed further request is refused;
  no fabricated answer, implicit retry, refund or new job is created.
- [ ] GREEN; direct consumers are `test_assistant_service_executor.py`,
  `test_assistant_service_loop.py`, `test_assistant_service_response.py` and
  `test_assistant_runtime_explanation.py`. Do not run a broader suite yet.

**Acceptance:** snapshots retain actual overrun/unknown even on error or cancel;
old strict exceptions/identity/publication rules are not relaxed.
**Out of scope:** production runtime provider binding, SDK changes, core graph changes.

## Task 4 — Source-free connection accounting

**Goal/deliverables:** use the same policy for the two-phase connection probe
without converting its 1024/128/2/15-second bounds into explanation limits.

**Files:** `src/codeviz/assistant/connection.py`, connection projection in
`service.py`/`jobs.py`; new `tests/test_assistant_connection_estimated_input.py`.
Depends on Task 3's error/snapshot interfaces.

- [ ] RED case: tools phase returns unknown actual usage; assert the provider's
  final prepare and second dispatch counts both remain zero. A complete second
  phase may finish with a warning; no repair exists in connection flow.
- [ ] Run `unittest discover -s tests -p test_assistant_connection_estimated_input.py -v`.
- [ ] `run_connection` gains keyword-only `trusted_profiles=()`; construct policy
  with its source-free ledger and `input_limit=1024`. Default remains strict.
  Preserve exact source-free schemas, no source occurrences, 32/96 phase output
  allocations, total output 128, 2 requests and 15-second deadline.
- [ ] `_FROZEN_FIELDS`/`_frozen` include a flattened primitive snapshot of the new
  metadata. Hold/update policy in `_ConnectionResources`; append default
  `input_accounting=None` to `ConnectionProbeResult`. On exception, service
  reads resource observed/accounting before closing, not only result-on-success.
- [ ] For pure accounting protocol errors keep ProviderError; budget stopping
  maps to `AgentError("budget_exceeded")`. Never feed an unsupported budget
  code into config.ProviderError, which would turn it into AI_INVALID_INPUT.
- [ ] GREEN plus `test_assistant_connection_runner.py`,
  `test_assistant_connection_jobs.py`, `test_assistant_connection_capture.py`.
  The actual generic-SDK unknown-framing refusal assertion stays unchanged;
  missing isolated SDK prerequisites remain honestly skipped.

**Acceptance:** default strict source-free behavior stays intact; estimated
tools unknown/overrun prevents the second request; failure snapshot is retained.
**Out of scope:** changing the probe's strict schema capability claim to fit DeepSeek.

## Task 5 — Closed usage projection and explicit PC labels

**Goal/deliverables:** expose honest accounting meaning with no new source,
provider secret, request input field, navigation payload or generic extra-key allowance.

**Files, backend core owner:** `src/codeviz/assistant/poll_public.py`,
`_usage_values.py`, job-to-poll wiring in `service.py`; new
`tests/test_assistant_poll_estimated_input.py`.
**Files, main owner:** `frontend/src/app/assistant/types.ts`,
`frontend/src/app/assistant/poll.ts`, `frontend/src/app/components/AssistantPanel.tsx`,
`frontend/src/app/i18n.ts`; create `frontend/src/app/components/AssistantUsage.tsx`,
`frontend/src/app/components/AssistantUsage.test.tsx`,
`frontend/src/app/assistant/estimated-usage.test.ts`.

- [ ] Backend RED: old strict public usage still has exactly reserved/observed;
  estimated has those plus inputAccounting and the six exact spec §9 keys.
  Test actual zero, unknown partial actual lower-bound and invalid extra/bool/method.
- [ ] Backend run: `unittest discover -s tests -p test_assistant_poll_estimated_input.py -v`.
- [ ] Keep one canonical `ObservedUsage` class in `_usage_values.py` and re-export
  it from `poll_public`; add `PollState.input_accounting=None`. Projection and
  usage events/reset snapshots use the same immutable accounting record.
- [ ] Frontend RED: add new tests without changing old strict fixtures. Assert
  the two approved shapes and reject extra outer/inner keys, unsafe numbers,
  `upperBound:true`, unknown method/reason and inconsistent actual totals.
- [ ] Concrete new type shape:

```typescript
type EstimatedInputAccounting = {
  basis: 'estimated';
  method: 'wire-bytes-v1';
  limitTokens: 20000 | 1024;
  upperBound: false;
  knownActualInputTokens: number;
  blockedReason: null | 'estimated_limit' | 'observed_overrun' | 'usage_unknown';
};
```

  Existing reserved/observed fields remain unchanged. Closed shape, not observed
  basis or numerical size, discriminates the usage union. Reject new shapes
  outside the appropriate explanation/connection kinds; enforce 20000 for an
  explanation and 1024 for a connection, including usage events/reset snapshots.
  Navigation stays unchanged. Pass the outer job kind into the private usage
  validator rather than inferring kind from the proposed token limit.
- [ ] Move only the current usage `<dl>` into the small `AssistantUsage` display
  component. Render input estimate separately from output reservation; use
  `??` for unknown, not falsy fallback. Display the known actual lower bound as
  partial when aggregate is unknown. Do not calculate policy in the renderer.
- [ ] Add Chinese/English static first-send caveat plus labels for each blocked
  reason. An accounting warning does not imply cancellation or resource release.
  Keep the sidebar open/close behavior and all source evidence actions unchanged.
- [ ] Frontend commands:

```powershell
npm --prefix frontend test -- src/app/assistant/estimated-usage.test.ts src/app/components/AssistantUsage.test.tsx src/app/assistant/client.test.ts src/app/assistant/connection-client.test.ts src/app/components/AssistantPanel.test.tsx
npm --prefix frontend run typecheck
```

  Run RED first, then GREEN; scoped ESLint covers the touched components/types/parser.
  Do not claim jsdom is a native PC test.

**Acceptance:** a user sees estimated input versus reported actual/unknown and
why further sends stopped; no extra source/secret payload enters the closed DTO.
**Out of scope:** settings/request schema migration, UI redesign or new dependencies.

## Task 6 — Offline regression boundary and honest handoff

**Goal/deliverables:** integrate the new accounting slice and record what is
actually accepted, without calling it complete DeepSeek or E release support.

**Files:** `docs/CODE_AGENT_GUIDE.md`, `docs/CODE_AGENT_DEPENDENCIES.md`,
`docs/QUALITY_GATES.md`, this plan/spec; at boundary append at most five lines
to `docs/CODE_AGENT_PROGRESS.md`. No per-task progress-file writes.

- [ ] Inspect scoped diff; preserve unrelated dirty files. No new test skip,
  weakened/deleted test or public contract beyond this approved spec is inferred.
- [ ] One independent review at the new accounting implementation milestone,
  focused only on this new diff, if not already performed. This is not another
  review of the old E cleanup/controller diff. Follow AGENTS.md if findings exist.
- [ ] Run applicable boundary gates once for the completed new code snapshot:

  Use the already-accepted task-local Node 22.23.3 toolchain at
  `build/assistant-node22-probe-20261012/node-v22.23.3-win-x64` for frontend
  commands; check its presence/version without downloading/upgrading tools.
  If needed, prepend that path only in the current process, not globally.

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v
.desktop-venv/Scripts/python.exe -m ruff check src tests
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

  Do not rerun unchanged already-passing commands. Derive real counts from
  output, report skips/todo separately and retain existing warnings. Follow
  two-retry/no-progress rules; do not silently repair unrelated full-run failures.
- [ ] Record estimates as estimates and provider usage as reported actual;
  preserve explicit production factory OFF, generic SDK framing refusal,
  unaccepted DeepSeek-specific wire/provenance gate, Linux and live API gaps.
- [ ] Document next Task10 slice: DeepSeek protocol/profile compatibility,
  full source-attribution proof, SDK fingerprint and source/wheel/exe packaging
  under the already-accepted disclosure rules. It needs its own technical plan;
  do not implement it opportunistically inside this accounting slice.

**Acceptance:** new budget behaviors pass affected and boundary checks with
honest counts, old strict behavior remains, and no real request is made.
**Out of scope:** API key collection, paid request/source upload, native/Linux
claims from jsdom/types, dependency changes and Git operations.

## Planning completion / execution checkpoint

This turn's deliverables are the spec and this plan only. Inline consistency
checks must cover all referenced files, exact record/JSON fields, no placeholders,
and preservation of the already-approved boundaries. Runtime tests are not run
because no runtime code is changed. Resume implementation at Task 1 with
failure-first tests under the role ownership above. Never load the prohibited
execution skills or ask another general “continue?” question between tasks.

## Execution checkpoint — 2026-10-21

Tasks 1–5 are implemented. Task 6 remains **incomplete** pending the final
frontend full-suite gate; this is not DeepSeek or Task10/E acceptance.
The one independent review of the new accounting slice is finished, its
applicable findings fixed with new failure-first regressions, and both roles
closed. No second independent review was dispatched.

Final Python: 1,484 total = 1,415 passed + 69 skipped, recorded in
`build/assistant-estimated-input-final.log`. Ruff and both mypy platforms pass
for 100 source files. Frontend lint, typecheck and build pass; retain the
large-chunk warning. These results remain valid for unchanged code.
The final default frontend run has 555 passed, 1 failed and 2 todo. Its failure
waits for `Open to side` in `SourceEditingKeyboard.test.tsx`; no test was edited.
A full rerun with two workers produced no visible progress for 126 seconds and
was interrupted under AGENTS.md, so it is not a passing or skipped suite.
Preserve both frontend logs named in `CODE_AGENT_PROGRESS.md`.

Resume Task 6 with at most one remaining full frontend attempt using the same
Node 22 toolchain and `--maxWorkers=2 --reporter=verbose` for visible progress.
Do not repeat already-passing unchanged gates or repair unrelated old tests.
If it fails, stop and report. Only after acceptance may Task 6 be closed; then
follow AGENTS.md's review-fix boundary stop and hand off to the separate
Task10 DeepSeek protocol/provenance/fingerprint and packaging plan.

## Closure of the above checkpoint — 2026-10-21

Task 6 is now complete for this offline accounting slice. Its last remaining
gate ran under the existing Node 22.23.3 toolchain with
`npm --prefix frontend test -- --maxWorkers=2 --reporter=verbose`: exit 0,
46 files, 556 passed, 2 todo, 0 failed, 211.78 seconds. The full receipt is
`build/assistant-estimated-input-frontend-verbose-final.log`.
No source, test, dependency or runner configuration file changed during this
resume. Earlier default-parallel failures and the interrupted quiet run remain
recorded above; this result does not assert default-parallel stability.

Reuse the unchanged accepted Python, Ruff, both mypy and frontend lint/typecheck/
build results recorded above. Skipped and todo cases are not passed. The one
independent review and its fixes are closed with these gates, not a second review.
Follow AGENTS.md's review-fix boundary stop. Next work is a separately planned
Task10 DeepSeek protocol, full source-attribution and SDK fingerprint admission,
then source/wheel/exe delivery; the production factory remains OFF, and native
Linux, real API and refreshed packaged desktop acceptance remain incomplete.
