# DeepSeek SDK wire profile implementation plan

> AGENTS.md replaces execution-skill/review-per-task boilerplate. Mandatory
> core implementation routing applies; do not load prohibited execution skills,
> relax existing tests, change dependencies, or commit/push without a new request.

**Goal:** independently prove DeepSeek prepare/capture, complete provenance and
immutable SDK handoff without enabling production dispatch.

**Architecture:** the existing SDK adapter accepts a closed private profile
selected by a separate private factory. Actual final SDK bytes, deterministic
occurrence mapping and a primitive frozen fingerprint are the acceptance seam.

**Tech stack:** existing pinned optional SDK, Python/dataclasses/unittest and
in-memory transport. No new dependency or frontend/application migration.

**Spec:** `../specs/2026-10-21-deepseek-wire-profile-design.md`.
Goodall approved this SDK-only architecture; prior capability Task1–3 are closed.
No new public contract decision is introduced. All tasks run in this workspace.

## Task 1 — Closed private protocol profile (done)

**Owner:** core_implementer. **Files:** modify
`src/codeviz/assistant/sdk_provider.py`; create
`src/codeviz/assistant/_deepseek_admission.py` and
`tests/test_assistant_sdk_provider_deepseek.py`.
Main agent owns the disjoint base-only direct-consumer test
`tests/test_assistant_deepseek_import_boundary.py`, proving importing the private
admission module neither imports the SDK, makes a connection nor opens the
ordinary runtime's production factory. Core implementation owns the three
files above, not this base-import test.

- [ ] Write a focused failure-first test using the actual pinned SDK and the
  existing injected capture transport, not a second handwritten HTTP encoder.
  Import the new private factory and await its tools/final `prepare` request.
  The expected captured properties are:

  ```python
  assert wire["model"] == "deepseek-flash"
  assert wire["max_tokens"] == draft.max_output_tokens
  assert wire["thinking"] == {"type": "disabled"}
  assert wire["response_format"] == {"type": "json_object"}  # final mode
  assert underlying_sends == 0  # prepare has not dispatched
  ```

- [ ] Run with
  `build/assistant-sdk-probe-20261012/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_sdk_provider_deepseek.py -v`.
  Confirm the new factory/profile is missing or refused before implementation.
  Actual SDK cases must not be counted as passes if the interpreter is wrong.
- [ ] Add the frozen `_SDKWireProfile` and private
  `_create_deepseek_provider(config,key,*,sdk_loader,transport_factory,clock)`.
  App-owned constants bind endpoint/model/method/profile version; reject beta,
  other models, unsupported destinations and untrusted override fields before
  optional loading. Keep generic `_create_provider` behavior and signatures.
- [ ] Map supported SDK options to `max_tokens`, explicit non-thinking and JSON
  object final mode. Validate actual resulting wire and app-owned JSON
  scaffolding; reject unexpected copies rather than altering captured bytes.
  Preserve TLS/zero retry/no redirects/proxy restrictions and cleanup.
- [ ] Add one base-safe invalid-destination/lazy-import test and focused
  wrong-model/settings tests. Do not register any trusted service profile,
  modify the connection probe, SSE decoder or desktop assembly in this task.
- [ ] Run only the new test file plus direct consumer
  `test_assistant_sdk_provider.py` in the existing explicit SDK interpreter.
  Inspect scoped diff and emit one task line, then proceed to Task 2.

Task 1 receipt: the explicit pinned-SDK interpreter passed 5 new profile tests
and 21 existing generic adapter tests. Main's base-only import test passed
separately without loading optional SDK modules or permitting socket connects.
That test checks the actual `test_connection` rejection
`AI_DEPENDENCY_MISSING` with no engine creation; it does not mistake the current
settings owner's `available` flag for production dispatch admission. The
initial false-premise assertion on that flag was corrected without changing
the runtime or weakening the actual no-dispatch requirement.

## Task 2 — Complete source proof and primitive frozen handoff (done)

**Owner:** same core_implementer; same three-file write scope. No existing tests
may be changed to accept weaker framing, attribution or schema semantics.

- [ ] Add a failing test demonstrating that valid source/metadata tags survive
  the real SDK encoding, while an undeclared duplicate or transformed occurrence
  is rejected. Use logical-slot-to-wire-slot provenance, not substring grants.
  Expected accounting after successful proof:

  ```python
  assert prepared.input_token_upper_bound is None
  assert prepared.attribution_complete is True
  record = prepared.estimated_input_accounting
  assert record.method == "wire-bytes-v1"
  assert record.estimated_input_tokens == len(prepared.body)
  assert record.body_sha256 == hashlib.sha256(prepared.body).hexdigest()
  ```

- [ ] Run the focused actual-SDK test and observe rejection/missing proof. Then
  implement deterministic mappings and complete pure wire validation for both
  source and metadata, including keys/escapes/history/tool copies/scaffolding.
  Only successful proof produces the estimated record; generic remains
  unproven `None/False`, without profile self-registration or a fake upper bound.
- [ ] Add a failure-first mutation test changing a nested estimate or occurrence
  after prepare. Extend `_fields` to capture each exact primitive field; keep
  request identity, raw headers/URL/body and single-use checks intact. Assert
  rejection before the injected transport enters; do not send a rebuilt request.
- [ ] Cover repeated and Unicode/escaped data with a few focused cases, plus
  pre-dispatch cancellation and same-original-request single handoff using
  bounded synthetic responses. No claim about DeepSeek terminal SSE yet.
- [ ] Run the new file, generic SDK file and direct connection capture/profile
  consumers in the explicit existing SDK interpreter; base runs must honestly
  skip optional-SDK-only cases. Inspect only this slice's edits and continue.

## Task 3 — SDK-only phase boundary (done)

**Owner:** main agent; one independent review of this new diff only.
**Files:** this plan/spec, admission plan and at most five progress lines.

- [ ] Check the actual-SDK positive/negative receipts rather than treating base
  skips as proof. Confirm generic strict capture tests have not been relaxed and
  service/connection/desktop production wiring is unchanged and OFF.
- [ ] Run full base Python tests once with visible progress; Ruff; default then
  Linux mypy on the changed SDK snapshot. Reuse unchanged passing frontend gates
  and explicitly scoped native evidence; do not rebuild a new E artifact here.
- [ ] Perform the one independent boundary review with concrete symbol evidence.
  If findings require fixes, close with affected gates under AGENTS.md, not a
  second independent review. Never retry a failing command more than twice;
  no full run may remain without visible progress for over 120 seconds.
- [ ] Record exact pass/skip counts, commands/results and new profile proof
  limitations. Update progress only here or at a genuine blocker. No real API,
  source upload, dependency installation, commit or push is part of acceptance.

## Following queue

Only after this boundary: separately planned DeepSeek bounded SSE and source-free
connection validation, then trusted application assembly and source/wheel/exe
offline acceptance. Preserve the approved local-output capability variant,
unknown-usage sticky blocking and all existing budget/disclosure/cancellation
controls. Native Linux and real API acceptance remain distinct pending evidence.

## Boundary receipt — 2026-10-22

Tasks 1–3 are complete for this SDK-only slice. In the existing pinned SDK
interpreter, new DeepSeek tests passed 23/23, generic SDK 21/21, connection
capture 2/2 and connection profile 3/3. Base optional-import test passed without
SDK imports/network or a production engine. Base optional-only cases are skips.

Tesla performed the single independent review and found missing capture-time
config/profile binding rechecks. The actual original URL/body was already frozen;
this was not demonstrated redirection. The fix freezes exact config/profile
primitives and owner identities at successful capture and rechecks before and
after transport_factory. Extended mutation test was observed failing first,
then passing with zero underlying transport entries. No semantic-core algorithm
was changed and no second independent review was run. Ruff import/loop-binding
diagnostics were fixed mechanically without weakening assertions.

Final fixed snapshot: `.desktop-venv/Scripts/python.exe -B -m unittest discover
-s tests -v` exited 0: 1514 total = 1424 passed + 90 skipped, 53.341s;
`build/assistant-deepseek-wire-fixed-final.log` has GATE_EXIT_CODE=0. Ruff passed;
default and `--platform linux` mypy both passed, 102 source files. Preliminary
passing log is retained and is not substituted for this post-fix run.
90 skipped = Tk 47 + optional SDK 31 + native Linux 4 + symlink 8.

Unchanged frontend evidence is reused: 561 passed/2 todo, lint/typecheck/build
passed with the existing large-chunk warning. No new native/source-wheel-exe
acceptance, API request, source upload, dependencies or commit/push. Production
factory remains OFF. Next: 2026-10-22-deepseek-connection.md Task1, using Goodall's
completed architecture review; AGENTS.md now authorizes routine autonomous
decisions and continuation, not safety/privacy/payment exceptions.
