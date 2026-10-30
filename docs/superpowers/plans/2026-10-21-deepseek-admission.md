# Task10 DeepSeek admission continuation

> Process: repository AGENTS.md controls execution, routing and verification.
> This is a Task10 addendum, not a replacement for the runtime plan or permission
> to change its public contracts, budget proofs, existing tests or disclosure rules.

**Goal:** expose the already-audited SDK as an explicitly installed optional
dependency, then determine whether DeepSeek can satisfy the production gate.

**Architecture:** dependency availability and permission to dispatch are separate.
The base package keeps zero runtime dependencies. The normal runtime remains
unbound to a provider until the complete profile/proof gate passes. Main agent
implements dependency metadata; architect consults on the cross-module profile.

**Authority:** user selected DeepSeek and replied “继续” to the explicit request
to admit the locked optional SDK. No real API, secret, source upload, budget
relaxation, test change/deletion, commit or push is authorized.

## P1 — Optional dependency declaration

Files: modify `pyproject.toml`; create
`tests/test_assistant_optional_dependencies.py`.

- [x] Failure-first: parse project TOML and audited
  `requirements/assistant-probe-lock.txt`; require the `ai` extra to equal all
  26 exact pins, including `pydantic-ai-slim[openai]==2.55.0`.
- [x] Run `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p test_assistant_optional_dependencies.py -v`;
  require failure due to missing `ai`, not an import/setup error.
- [x] Add only the audited 26 optional pins; leave `dependencies=[]`, desktop,
  dev, packaging, scripts and all runtime code unchanged. Reuse the existing
  hash lock and original notice provenance; do not duplicate or relabel them.
- [x] Run the new test, `test_packaging_layout.py` and
  `test_assistant_packaging.py` as direct consumers. Inspect the scoped diff.

## P2 — Isolated installed-wheel verification

Files/artifacts: task-only `build/assistant-optional-ai-20261021`,
`build/assistant-wheel-ai-20261021`; update the delivery record and guide.

- [x] Build the app wheel offline with
  `.desktop-venv/Scripts/python.exe -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir build/assistant-wheel-ai-20261021`.
- [x] Inspect wheel metadata: only optional `extra == "ai"` requirements, exact
  pins matching the existing hash lock, bundled local UI; no automatic SDK import.
- [x] Create an isolated no-pip venv with the existing interpreter. Install the
  SDK from `build/assistant-provenance-20261012/312-win_amd64` using
  `--no-index --require-hashes -r requirements/assistant-probe-lock.txt`, then
  resolve/install the application wheel's `[ai]` extra from that same cache.
  Never add packages to `.desktop-venv` or download a missing artifact silently.
- [x] With `-I -B`, guard DNS/socket connection, import the installed app and
  instantiate its default controller/runtime. Assert initial disabled/no-key,
  no loaded SDK modules, packaged local UI and installed audited versions.
  SDK metadata availability is not production dispatch, a native run or Linux.
- [x] Record precise pass/fail/skipped and artifact identity; the current frozen
  app is unchanged and still lacks an admitted production SDK profile.

## P3 — DeepSeek profile architecture decision

Files inspected only until architect review: `assistant/sdk_provider.py`,
`assistant/service.py`, `assistant/provider.py`, `assistant/config.py`,
`ui/desktop.py`, existing connection capture tests and official DeepSeek docs.

- [ ] Target `deepseek-flash`, explicit non-thinking; reject unsupported
  destinations/models/modes, no silent fallback. Official docs specify
  `max_tokens`, `json_object`, and separate beta-only strict tool behavior,
  unlike the current generic strict-json-schema/max-completion-tokens profile.
- [ ] Architect must identify an independently supportable conservative input
  framing bound and complete final-wire source attribution before enabling any
  dispatch. Approximate tokenizer ratios, captured byte lengths or observed usage
  are not upper-bound proofs. The service's 20,000 cumulative input reservation,
  provenance/consent checks and unknown-framing rejection remain unchanged.
- [ ] If the approved contract cannot be met, stop at that real decision with
  concrete evidence and a user-facing alternative; do not fabricate proof fields,
  weaken tests or implement a permissive profile to bypass the gate.

## Boundary

No per-task progress file edits. At an actual blocker append at most five lines.
Do not repeat unchanged passing suites/review. If this slice reaches a phase
boundary, apply AGENTS.md's gate once for its changed snapshot, reusing unchanged
frontend/native evidence with explicit scope. A new production profile needs
its own approved technical plan and affected tests; this dependency-only slice
does not approve it. Real Linux execution and user-authorized API acceptance
remain required, not inferred from Windows or fake transport.

## P3 architect disposition — 2026-10-21

Maxwell's read-only architecture consultation is complete (not a repeated E
diff review). P1/P2 are complete and are dependency-only acceptance. P3 production
dispatch is **blocked**, not an implementation bug: available DeepSeek material
does not establish the complete hidden-framing input bound required by the
current reservation contract. A model's context length, approximate tokenizer
ratios, captured JSON bytes or sample usage cannot substitute for that proof.

The user must select one of these contracts before new runtime work:

1. Keep the existing verified 20,000-input-token reservation requirement and
   leave the production factory unbound. Delivery is optional SDK availability,
   not DeepSeek API support. This is the default without further authorization.
2. Explicitly authorize an **estimated input-budget** contract. This needs a new
   approved architecture plan with a distinct estimated accounting representation,
   not estimated values in `input_token_upper_bound`. Actual usage may exceed
   20,000 on an admitted request; observed overrun stops subsequent dispatch and
   is reported honestly. Unknown usage must not be invented. Source consent,
   complete final-wire attribution, byte/file/tool/request limits, output caps,
   cancellation and zero automatic retry remain strict.

The second option does not authorize a real API call, key or user-source upload,
and does not authorize changing existing tests to pretend the old contract passed.
No service/adapter/factory implementation or production SDK hook is made here.
Stop at this decision per AGENTS.md; no further full suites or review pass on
the unchanged application snapshot are needed to rediscover the same blocker.

Official evidence consulted: [token usage](https://api-docs.deepseek.com/quick_start/token_usage/),
[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/),
[model list](https://api-docs.deepseek.com/quick_start/pricing/).

## Authorization update — 2026-10-21

The user explicitly selected option 2: “授权估算预算合同”. The former choice
blocker is resolved. The concrete contract and ordered implementation plan are
`../specs/2026-10-21-estimated-input-budget-design.md` and
`2026-10-21-estimated-input-budget.md`. This authorizes the new architecture
plan, not live API use or a claim that the current SDK can send. Old strict
unknown-framing rejection, source attribution and the production gate remain
unchanged until separately implemented and verified under that plan.

## P3 protocol disposition — 2026-10-21 (decision required)

The estimated-accounting slice is closed; its authorization did not change the
connection capability contract. Goodall's read-only architecture consultation
identified a separate public-contract decision before DeepSeek implementation.

Current `SDKProviderAdapter._capture` and `connection._wire_profile` require
remote `response_format=json_schema` with `strict:true`; the SDK model settings
also declare JSON Schema output support. `poll_public.py`, frontend
`assistant/types.ts` and `assistant/poll.ts` expose and enforce
`{toolSupported:true, structuredOutputSupported:true}`. These cannot honestly
report the same capability for the documented standard DeepSeek JSON mode.

Official [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) specifies
`json_object`, with JSON instructions and an example in the prompt. This does
not establish remote JSON Schema enforcement. Official
[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) places strict
tool schemas behind the separate beta endpoint; do not silently select beta.

Recommended decision, **not yet authorized**: preserve the existing strict
profile and its tests; add an explicit DeepSeek capability variant distinguishing
JSON formatting plus local schema validation from remote strict enforcement.
An architect-proposed public shape would keep `structuredOutputSupported:false`
for that variant and add `jsonObjectSupported:true` and
`outputValidation:"local-json-schema"`. Tool arguments remain locally validated;
basic tool support must not imply remote strict-tool enforcement. Exact DTO,
UI labels and acceptance tests must be specified after this decision, not
silently inferred from permission to use estimated input accounting.

P3 stops at this decision under AGENTS.md. No implementation, test relaxation,
dependency change, production registration, real API call or source upload was
made. The production factory remains OFF. Next: user decides whether to accept
this differentiated capability contract; then write the dedicated DeepSeek
implementation plan covering protocol, complete provenance, frozen accounting
fingerprint, offline proof and subsequent source/wheel/exe acceptance.

## Capability authorization — 2026-10-21

The user answered “是” to the explicit decision above: DeepSeek may use JSON
formatting plus application-local strict validation, honestly distinguished
from remote strict schema enforcement. The original strict profile and tests
remain supported. This resolves the capability-contract blocker, not the
remaining SDK capture, complete source attribution, handoff fingerprint or
packaged acceptance work. It does not authorize paid API calls, credentials,
user-source upload, the beta endpoint or an automatic provider fallback.

## Recorded follow-on architecture (not started)

Goodall approved the first capability slice and supplied the next bounded
architecture, not an implementation or release approval. The capability
slice's Task 3 gate must pass before advancing; its current blocker and exact
receipts are in `2026-10-21-deepseek-capabilities.md`.

The next SDK-only slice modifies `assistant/sdk_provider.py` and adds
`assistant/_deepseek_admission.py` plus
`tests/test_assistant_sdk_provider_deepseek.py`. A private
`_create_deepseek_provider(config, key, *, sdk_loader, transport_factory, clock)`
injects a closed frozen `_SDKWireProfile`; the generic factory retains its
unproven `None/False` behavior. No connection/service/desktop wiring in that
slice. The profile binds only normalized
`https://api.deepseek.com:443/v1/chat/completions`, `deepseek-flash`, explicit
non-thinking, `max_tokens` and final `json_object`; no beta or model fallback.

Validate final SDK bytes at the existing suspended capture seam. Derive source
and metadata occurrences from deterministic logical-slot to wire-slot mappings,
including history, transformed copies, schema/scaffold and duplicate fragments;
unproven transformations fail closed. Substring matches cannot grant origin.
Only complete attribution permits estimated accounting. Freeze its four
primitive fields, occurrence primitives, body/URL/headers and original request
identity at handoff. Application-owned private registration constants must not
be inferred from provider claims, settings or request bodies. Real pinned SDK
plus fake transport must prove the offline slice; production stays OFF.

A later separate slice handles bounded DeepSeek terminal single-choice usage
chunks, the source-free JSON prompt/example, local-success capabilities and
connection/service assembly. Missing usage retains unknown/sticky accounting,
not a newly invented unconditional protocol error. Existing disclosure,
freshness, output/request budgets, cancellation and repair bounds stay strict.
SDK transformations and terminal chunk details still require offline proof.
This architecture introduces no further public-contract decision; it does not
waive native Linux or real API acceptance, or enable a paid request.

## Continuation status — 2026-10-22

Capability, SDK wire-profile and bounded connection slices are closed with their
own final receipts. Latest: 2026-10-22-deepseek-connection.md, base1562 with
1433 passed/129 skipped, pinned-SDK targeted92, Ruff and default/Linux mypy
passed; one review's two stream findings fixed, no second review. The closed
DeepSeek probe reallocates 96/32 within the unchanged total128; generic32/96
is unchanged. Proceed autonomously to 2026-10-22-deepseek-application.md, whose
assembly/delivery architecture Goodall reviewed. Production is still OFF until
that implementation; old API/native/Linux/packaged evidence is not inferred.
