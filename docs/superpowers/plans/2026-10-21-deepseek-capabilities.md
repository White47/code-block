# DeepSeek capability contract implementation plan

> AGENTS.md governs execution. Use its required architect/core routing, not
> per-task review dispatch. Do not load the prohibited execution skills, modify
> old tests to relax strict behavior, or commit/push without a new request.

**Goal:** carry the approved distinction between remote strict output and
JSON plus local validation through connection jobs and the current PC UI.

**Architecture:** an immutable internal capability value feeds a closed public
result union. Lifecycle success and resource cleanup remain independent. The
new variant is testable offline while the production factory stays OFF.

**Tech stack:** current Python dataclasses/unittest, React/TypeScript/Vitest;
no dependency, SDK version, bridge request or configuration migration.

**Spec:** `../specs/2026-10-21-deepseek-capabilities-design.md`.
**Status:** user approved the capability distinction; Goodall approved the
internal propagation design before Tasks 1–2 implementation. `None` preserves
legacy strict success; an explicit frozen local-capability record selects the
new branch only after validated probe success.
Tasks 1–3 are complete for the offline capability slice. The full Python gate
passed after the authorized test-precision and resource-readiness corrections.
Failed/interrupted historical receipts remain below; production is still OFF.

## Task 1 — Immutable capability result and job projection (done)

**Owner:** core_implementer (cross-module connection/service invariants).
**Files:** create `src/codeviz/assistant/_connection_values.py` and
`tests/test_assistant_connection_capabilities.py`; modify
`src/codeviz/assistant/connection.py`, `jobs.py`, `poll_public.py`, `service.py`.
Do not change SDK transport, parser/resolver/source handles or existing tests.

- [ ] Write one failure-first public projection test showing the local variant
  is not reported as remote strictness. The expected public object is exactly:

  ```python
  {"toolSupported": True, "structuredOutputSupported": False,
   "jsonObjectSupported": True, "outputValidation": "local-json-schema"}
  ```

- [ ] Run the new file with the existing interpreter:
  `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_connection_capabilities.py -v`.
  Observe failure for missing capability representation, not fixture/import typos.
- [ ] Implement a frozen, closed capability representation after architect
  approval. Append fields with compatible defaults to existing DTOs. Preserve
  legacy strict completed fixtures; never change the old strict result shape.
  `ConnectionCapabilities` is frozen/slots with exactly `tool_supported=True`,
  `structured_output_supported=False`, `json_object_supported=True`,
  `output_validation="local-json-schema"`; reject other exact types/values.
  Append `connection_capabilities: ConnectionCapabilities | None = None` to
  `ConnectionProbeResult`, `_ConnectionJob`, and `PollState`. Retired jobs use
  their existing frozen `PollState`, not a second capability field.
- [ ] Propagate the validated runner result through `_ConnectionJob`, completed
  `PollState` and retired connection snapshots. Do not derive local/remote
  capability from editable URL, frontend input, or merely `Completed` status.
  A non-completed job publishes null. Cross-kind capability state fails closed.
- [ ] Add focused negative projection cases (invalid mode/type, hybrid or
  cross-kind/non-completed values) and one fake-runner retirement/replay test.
  Keep resource ownership, usage and revision handling unchanged.
- [ ] Run the new file plus direct consumers: `test_assistant_poll_public.py`,
  `test_assistant_connection_jobs.py`, `test_assistant_connection_runner.py`,
  `test_assistant_connection_estimated_input.py` via unittest discovery patterns.
  Expected: zero failures; report any environment skips explicitly.
- [ ] Inspect only this task's diff; retain the unchanged strict SDK rejection
  and production factory. Emit one progress line, continue to Task 2.

## Task 2 — Closed frontend union and truthful settings label (done)

**Owner:** main agent, after Task 1 architecture approval; frontend files are
disjoint and may be prepared while the core implementation runs.
**Files:** modify `frontend/src/app/assistant/types.ts`, `poll.ts`,
`components/AssistantSettings.tsx`, `i18n.ts`; create
`frontend/src/app/assistant/connection-capabilities.test.ts` and
`frontend/src/app/components/AssistantConnectionCapabilities.test.tsx`.

- [ ] Add a failure-first parser test passing the exact four-field local result
  to `connectionPoll`; expect the same result back. Use a complete, source-free
  completed connection fixture with the existing lifecycle/usage fields.
- [ ] Run `npm --prefix frontend test -- src/app/assistant/connection-capabilities.test.ts --maxWorkers=2` under the existing Node 22.23.3 toolchain;
  confirm the old parser rejects the new authorized variant.
- [ ] Define the `ConnectionResult` closed union from the spec; update only
  capability validation in `connectionPoll`, retaining all other strict checks.
  Reject extra fields, missing local marker, hybrid strict+local shape and
  capabilities on non-completed polls. Do not broaden unrelated result parsers.
- [ ] Add a failure-first settings test using the real store and fake desktop
  bridge, then show fixed translated capability text only for a completed
  result at the current configuration revision. English text distinguishes
  `JSON output + local schema validation` from `Remote strict schema`;
  Chinese distinguishes `JSON 输出＋本地结构校验` from `服务端严格结构校验`.
  Explain lack of remote schema guarantee for the local branch. No mount send.
- [ ] Run the two new files plus direct consumers `client.test.ts`,
  `store-jobs.test.ts`, `AssistantSettings.test.tsx`; expected zero failures.
  Do not edit old tests to accommodate failures. Inspect scoped diff, emit one
  task line, proceed directly to Task 3.

## Task 3 — Capability slice boundary (done)

**Owner:** main agent; one independent review at this boundary only.
**Files:** this spec/plan and at most five new progress lines at closure.

- [x] Run full Python tests once with visible progress, Ruff, default mypy and
  `mypy --platform linux` using `.desktop-venv/Scripts/python.exe`.
- [x] Run frontend lint/typecheck/full tests/build once under Node 22.23.3;
  use `test -- --maxWorkers=2 --reporter=verbose` for visible bounded progress.
  Preserve pre-existing frontend parallel timing and large-chunk warnings.
- [x] Request one review of this slice, citing concrete invariant/line findings.
  Fix applicable findings; if a fix is needed, rerun affected gates once on
  that final snapshot and stop under AGENTS.md. Do not dispatch a second review.
- [x] Record passed/skipped/todo separately. No full run may be quiet for over
  120 seconds; no command may be retried more than twice.
- [x] Update progress at the boundary with exact last command/result and next
  task. Neither this slice nor fake connection tests constitute real DeepSeek,
  native desktop or packaged release acceptance.

## Next queue after this slice

Continue the independent Task10 DeepSeek admission plan, not the completed
estimated-accounting slice: dedicated standard-endpoint SDK protocol and bounded
SSE adaptation → complete final-byte source/metadata attribution and frozen
accounting fingerprint proof → trusted profile/factory wiring → source/wheel/exe
offline delivery gates. Architecture must fix each implementation contract
before that slice starts. No beta/model fallback, new dependency, paid request
or source upload is implicit. Native Linux and real API acceptance stay explicit
separate limitations, not inferred from mypy or injected fake transports.

## Task 3 resumable gate record — 2026-10-21

Tasks 1–2 targeted evidence: backend 33 passed, frontend 31 passed. New UI
tests use the existing `zh-CN` language literal after a fixture type error;
final frontend typecheck/build passed. Full frontend lint passed before that
literal correction; the corrected file's scoped ESLint also passed. The build
retains the pre-existing large-chunk warning. Ruff and default/Linux mypy passed
on the final production snapshot (101 source files); do not rerun unchanged
passing gates to recover the interrupted commands.

The initial Python and frontend full commands lost their sessions during a
conversation interruption, without terminal summaries; logs
`build/assistant-capabilities-python-final.log` and
`build/assistant-capabilities-frontend-final.log` are **interrupted**, not passes.
Resuming the same reviewer session did not create a second review pass.

Frontend completion: `npm --prefix frontend test -- --maxWorkers=2 --reporter=verbose`,
Node 22.23.3, exit 0; 48 files, 561 passed, 2 todo, 193.27 seconds.
Receipt: `build/assistant-capabilities-frontend-resume.log`. No frontend code
has changed since that run. Todo cases are not counted as passed.

Python first resumed full command: exit 1; 1490 tests, one failure,
69 skipped, 333.316 seconds. The failure was
`test_assistant_jobs.JobsFirstSliceTests.test_shutdown_reports_unreleased_cleanup_and_can_retry`
at line 1063 (`ServiceError` was not raised).
Receipt: `build/assistant-capabilities-python-resume.log`.

Last permitted full retry, run separately after frontend completion:
`.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v`, exit 1;
1490 tests, 2 failing subtests, 69 skipped, 97.747 seconds. Both failures are
`test_assistant_runtime_explanation.RuntimeExplanationTests.test_source_and_mapped_node_admission_use_only_saved_identifiers`
(`kind=file` and `kind=node`), line 192: exact comparison of
`job.deadline - job.admitted_at` yielded `89.99999999999989`, not `90`.
Receipt: `build/assistant-capabilities-python-last-retry.log`, with explicit
`GATE_EXIT_CODE=1`. The earlier shutdown case passed on this retry; its earlier
failure remains recorded and is not declared fixed or pre-existing.

The 69 skips are 47 Tk, 10 isolated-SDK, 4 native-Linux, and 8 symlink-environment
cases. None is a pass. No tests were altered to accommodate either gate failure.
No further rerun, diagnosis/fix or next-phase implementation was performed after
the final failing retry. Permission to replace the exact floating-time assertion
with a strict numerical-tolerance assertion is a user decision under AGENTS.md;
the 90-second budget and cleanup requirements must not be relaxed.

Meitner completed the one independent review, with no actionable findings.
The original result was supplemented with its already-checked evidence:
`service._work_connection` guarded copy, `poll_public._project` kind/status and
legacy-strict handling, and frontend `validConnectionResult` exact variants.
No second inspection or independent review was requested. Review is not a
substitute for the failed full gate. Production remains OFF; no real API,
source upload, native acceptance, artifact rebuild, commit or push occurred.

Resume Task 3 first: resolve the test-precision decision, diagnose the recorded
cleanup timing failure without assuming it is fixed, and verify the resulting
snapshot within the user's authorized scope. Existing passing frontend/static
evidence remains valid where unchanged. Only after this boundary is accepted
may the recorded follow-on SDK architecture be turned into an implementation
plan. Do not claim Task10 or E delivery complete.

## Authorized Task 3 recovery — 2026-10-21

The user's next “继续” resumes the explicitly proposed precision correction
and cleanup diagnosis. The prior failed/interrupted gate records remain above;
no unchanged failing snapshot is retried again. This recovery changes only the
two affected test fixtures, not runtime budgets, shutdown behavior, SDK gates,
strict wire tests, dependencies or any production module.

Root-cause evidence and the minimal corrections:

- Forcing the admission clock to `934.1` reproduced the deadline assertion
  failure in all three existing context subtests: `(934.1 + 90.0) - 934.1` is
  `89.99999999999989`. Production sets `deadline=admitted_at+90.0` correctly.
  The existing test now uses that deterministic clock and permits at most
  `math.ulp(job.deadline)` of subtraction roundoff. The draft/job deadline
  equality, saved-identifier checks and no-send assertions are unchanged.
- `service._launch` publishes Running before `_work` creates/adopts the
  provider. Suspending `_work` before resource creation deterministically
  reproduced the shutdown test's missing-ServiceError failure: shutdown has
  no provider resource to fail closing in that valid early-cancel case.
  The test now waits for its existing `before_dispatch` seam, with an Event
  proving the fake provider is owned, instead of treating Running as ownership.
  It still requires cleanup failure, a live worker, unreleased resources and
  successful explicit retry. No cleanup assertion was removed or weakened.

The two affected test files passed 39 tests; additionally delaying `_work`
before provider creation by 50ms passed the corrected cleanup case. Ruff
`check src tests` passed. The new full Python run records its own exit marker
in `build/assistant-capabilities-python-precision-final.log`.
Unchanged frontend (561 passed/2 todo), default/Linux mypy (101 source files),
build and the same completed independent review are reused, not repeated.
Completion of this recovery still requires the new full Python run to finish
successfully; do not infer it from the targeted tests or this diagnosis.

## Recovery closure — 2026-10-21

The recovery's full Python command completed with exit 0: 1490 tests in
387.598 seconds, **1421 passed and 69 skipped**, no failures. It maintained
visible progress throughout; receipt
`build/assistant-capabilities-python-precision-final.log` ends in
`GATE_EXIT_CODE=0`. No test cases were removed or added; only the documented
precision/readiness fixtures changed. This resolves the recorded gate blockers
without changing production behavior, the 90-second budget or cleanup assertions.

Ruff passed on the corrected snapshot. Reuse unchanged frontend 561 passed /
2 todo, frontend lint/typecheck/build and default/Linux mypy for 101 source
files. The original independent review remains the sole pass; no second
review was dispatched. Previous failure/interruption records are retained.
Task 3 and this capability slice are accepted, not Task10/E or real API,
native Linux/desktop, or refreshed wheel/exe delivery. Next is the separately
recorded SDK-only prepare/capture/provenance/fingerprint implementation plan.
