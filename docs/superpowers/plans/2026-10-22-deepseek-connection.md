# DeepSeek stream and source-free connection implementation plan

AGENTS.md governs execution and review; do not load prohibited execution skills.
Spec: ../specs/2026-10-22-deepseek-connection-design.md. Architecture: Goodall,
read-only, completed before implementation. Existing pinned SDK; no dependencies.
Start only after 2026-10-21-deepseek-wire-profile.md Task3 closes.

## Task 1 — Bounded DeepSeek terminal SSE (done)

Owner: core_implementer. Files: sdk_provider.py and new
tests/test_assistant_sdk_stream_deepseek.py.

- Write one focused failing behavior test, then implement private-profile-only
  terminal single-choice usage validation without changing generic framing.
- Cover actual pinned SDK consumption, fragmented UTF-8/SSE, premature/duplicate
  usage, empty choices, reasoning, post-terminal JSON, missing DONE, allocation
  limits, unknown usage and cancellation/release with bounded synthetic frames.
- Run the new file plus generic and DeepSeek SDK files. Inspect scoped diff.
  No full gates or per-task review. Report one task line and continue.

Acceptance: actual SDK recognizes valid terminal usage, invalid sequences cannot
yield successful completion, unknown stays unknown, generic tests stay intact.
Out of scope: service/runtime/connection injection or production activation.

## Task 2 — Trusted source-free local-JSON connection (done)

Owner: core_implementer. Files: connection_profile.py, connection.py,
_deepseek_admission.py, sdk_provider.py, new test_assistant_connection_deepseek.py.

- Add frozen validated trusted connection profile and optional keyword binding;
  preserve default strict behavior and fixed logical probe bytes.
- Share the fixed pure probe JSON instruction helper. Validate closed final SDK
  wire fields, empty occurrences and exact trusted endpoint/model/profile.
- Suppress only the fixed probe's provisional text; preserve strict existing
  readers. Return local-validation capability only after both probes succeed.
- Freeze/recheck binding across callbacks/provider creation. Test spoofing via
  estimate-only injection, duplicate/mismatched/mutated profiles and cleanup.
- Test actual SDK plus fake transport and socket guards end to end. Run new file
  and direct connection/profile/capture/capability/accounting/SDK consumers.
- Apply the architecture-reviewed allocation correction in the spec: only the
  trusted DeepSeek probe uses 96 tools + 32 final, total 128 unchanged; generic
  remains 32+96. Add optional binding to connection_draft, private validated
  wire profile to SDK _draft and exact cross-allocation refusal. Update only new
  DeepSeek fixtures, not existing strict tests; preserve all framing charges.
  Verify 96/97-byte tools and 32/33-byte final boundaries plus unknown usage.

Acceptance: exactly p({}) then locally verified {}, no remote-strict claim,
unknown usage keeps sticky blocking, malformed data and profile drift refuse.
Out of scope: general answer changes, repair, source disclosure, app activation.

## Task 3 — Private runtime propagation (done)

Owner: core_implementer. Files: service.py, new
tests/test_assistant_deepseek_runtime.py; profile validation helper if needed.

- Append optional trusted input/connection profile tuples to private runtime
  construction; copy/validate exact primitives and propagate into lazy engine.
- Select a unique binding only from frozen app-owned configuration; pass it to
  the connection runner. No inference of capabilities from estimated accounting.
- Test source-free runtime job, capability completion/retirement, default OFF,
  estimate-only denial, mismatch/mutation and cancellation using no network.
- Run new file and direct runtime/settings/connection/jobs/poll consumers.

Acceptance: injected offline runtime reaches truthful local capability; default
desktop remains unavailable and import guard stays valid. No public DTO change.
Out of scope: production registry, real API, packaging and native manual tests.

## Task 4 — Phase boundary (done)

Owner: main agent. Files: this plan/spec and <=5 progress lines.

- Inspect exact pinned-SDK receipts and scoped changes. One independent review
  of this new slice only; fix findings and close with gates, no routine re-review.
- Run full base Python, Ruff, default and Linux mypy once on final snapshot;
  reuse unchanged frontend/native receipts honestly. Visible progress required;
  interrupt a run quiet for 120s, never blindly retry unchanged command >2 times.
- Record exact passed/skipped counts and log paths, production OFF and remaining
  live/native/packaged evidence. Append resumable next task at the boundary.
- Continue authorized admission queue: trusted application assembly then
  source/wheel/exe offline acceptance, with separate plans and required routing.
  Human-only API/paid request/source consent/native actions remain separate.

## Task receipts — 2026-10-22

Task1: actual pinned SDK new stream 12/12, generic SDK 21/21, DeepSeek SDK
23/23 passed. The premature-usage case failed before the private stream fix.
Base new stream cases skipped 12, not counted as actual SDK acceptance.

Task2: real two-probe test exposed the 32-versus-36-byte contradiction; the
architecture-reviewed 96/32 correction passed without changing old strict tests
or cumulative 128. Pinned SDK new file 19, direct connection 23, SDK 56 and
accounting 17 passed (115 total). Base new file: 3 passed/16 skipped. Exact limits,
unknown usage, cancellation, cross-profile refusal and original-request handoff
remain covered. This intentionally bounded profile can refuse longer/escaped IDs.

Task3: core implemented both trust-tuple validations/defensive copies and lazy
engine propagation/connection selection, then encountered a platform quota error.
Primary moved an accidentally misplaced new-test assertion block from the cancel
test back to its positive two-send test, preserving every assertion. Before:
14 passed/1 NameError; after: 15/15 passed in the pinned SDK interpreter, receipt
build/assistant-deepseek-runtime-fixed-target.log. Base optional-import guard
passed 1/1. Core later confirmed tool/model availability had recovered without
tier change, purchases or reset; it had not run direct-consumer tests, so those
are not claimed as its receipt. The main full base run covers these consumers.

Boundary so far: 1560 = 1433 passed + 127 skipped, 55.535s, exit0 and
GATE_EXIT_CODE=0 in build/assistant-deepseek-connection-python-final.log. Ruff
initially found five import-format diagnostics and two new-test loop captures;
mechanical formatting and explicit lambda defaults corrected them. Ruff and
default/Linux mypy then passed, 102 source files. Full base evidence belongs to
the same production behavior; post-mechanical pinned SDK connection tests passed
19/19 (build/assistant-deepseek-connection-fixed-target.log), runtime 15/15.
127 skips = Tk47 + optional SDK68 + native Linux4 + symlink8. Existing frontend
561 passed/2 todo plus lint/typecheck/build is reused unchanged, retaining the
large-chunk warning. No new native/packaged/API evidence or production activation.
These initial boundary results preceded the review fixes; final receipt below.

## Final boundary — 2026-10-22

Tesla completed the single independent review of this connection slice. Two
concrete findings were verified in _bounded_stream: the 2**63 counter ceiling
exceeded accounting's 2**53-1 safe bound, and all([]) admitted empty SSE frames
as comments. Core fixed only sdk_provider.py and its new stream tests. The first
failure-first test rejected six previously accepted unsafe counters; the second
failed for LF/CRLF at four stream positions. DeepSeek now uses 2**53-1 for all
counter groups and explicitly rejects empty frames. Generic bounds/framing,
nonempty comments, output allocation and the single-DONE rule are preserved.
No source attribution/parser/resolver/IR algorithm changed; no second review.

Post-fix pinned SDK targeted evidence: stream14 + generic21 + DeepSeek23 +
connection19 + runtime15 = 92 passed. Final base command
`.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -v` exited0:
1562 = 1433 passed + 129 skipped, 141.861s, continuously visible progress;
build/assistant-deepseek-connection-fixed-final.log has GATE_EXIT_CODE=0. Ruff,
default and Linux mypy passed, 102 source files. Skips: Tk47/optional SDK70/
native Linux4/symlink8. No further full run is needed for this unchanged slice.

All four tasks close here, with unchanged frontend evidence reused as above.
Production remains OFF; native/API/current packaged acceptance is not claimed.
Next: 2026-10-22-deepseek-application.md Task1, architecture already reviewed.
