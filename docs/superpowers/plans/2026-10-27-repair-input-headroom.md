# Configured repair-input headroom plan

## Decision

Architecture approval: implement the bounded change below. It applies only when
`JobRequest.budget is not None`. The legacy `budget=None` route remains byte-for-byte
behavior-compatible in its planning decisions.

This is internal scheduling, not a new budget, provider, public diagnostic, or
completion contract. Keep the configured limits (100000 input, 8192 output,
12000 source characters, 8 files, and existing request/tool limits) unchanged.

## Evidence and cause

- `InputBudgetPlanner.fits` already accepts multiple drafts and sums each logical
  body plus the largest observed wire overhead.
- `service.py:fit_projections` currently plans one final input while source
  headroom counts the repeated final occurrence twice.
- The ordinary gate currently plans `current + final` input, while source
  headroom plans `current + final + repeated final`.
- `ToolLoopTranscript.repair` appends only a validation-code prompt to the
  final-mode transcript; it does not include the failed final response.
- Offline receipt: 44000 ordinary + 29146 final reserves 73146; a 29291 repair
  exceeds the remaining 26854 and is not dispatched. The smaller-final control
  admits repair. The focused service test fails with `AI_BUDGET_EXCEEDED`.
- These facts reproduce missing repair headroom. They do not identify the live
  failed-final validation code or exclude an independent live timeout.

## Minimal production changes

1. In `tool_loop.py`, define one canonical private tuple containing each of the
   six service-emittable repair codes exactly once. Keep `repair()` accepting its
   existing legal inputs; use the canonical set only for worst-unique preview.
2. Add side-effect-free preview operations for:
   - current transcript -> final draft + worst-unique repair draft;
   - current transcript + candidate tool results -> final draft +
     worst-unique repair draft.
3. Build both previews on a clone. Apply final mode once, capture the final draft,
   then append the repair prompt and capture the repair draft. Never append or
   estimate the failed model response.
4. In the configured ordinary gate, require input fit for
   `current + final_preview + repair_preview`.
5. In configured `fit_projections`, require input fit for
   `final_preview + repair_preview`; retain the existing projection-floor and
   binary-search behavior.
6. Leave the legacy `budget=None` branches on their present single-final input
   planning path.

## Non-negotiable invariants

- Do not change `_SendPolicy`, `send_policy.admit`, frozen prepared fields,
  reservation atomicity, or C5's actual prepared-wire cap. Preview is routing
  evidence, never admission authority or a claimed upper bound.
- Do not change source-headroom calls or arithmetic. Final and repair repeat the
  same tagged source occurrences because repair contains no failed response.
- Do not change evidence receipts, provenance, chosen-target/ID binding,
  consent/freshness checks, output allocation, or request/tool counters.
- Do not add public fields, diagnostic codes, provider methods, dependencies, or
  live/API behavior.
- A phase-specific wire overhead larger than the observed planning overhead may
  still fail at strict prepare/admit. That failure remains incomplete/failed,
  dispatches no denied repair, and must never become a successful completion.
- Preview construction failure follows the existing pre-send failure mapping and
  cannot mutate the real transcript.

## Focused tests

- Make `test_configured_retrieval_preserves_input_for_one_repair` green: fewer
  than four ordinary dispatches, one final plus one repair, completed once.
- Pure-preview tests: original transcript/call IDs unchanged; final and repair
  output limits preserved; repair contains all six unique codes once and no
  failed-response content.
- Configured projection-pressure test proves fitting accounts for final+repair
  and retains complete omission/provenance semantics.
- Over-reservation controls: sufficient headroom still allows the existing four
  ordinary rounds; preview does not model 61 duplicate codes.
- Legacy `budget=None` regression proves the prior dispatch sequence/planning
  result is unchanged.
- Phase-dependent overhead test proves strict admission can reject safely with no
  repair dispatch and no forged completion.
- Existing source-occurrence, C5 frozen-wire, evidence-binding, and output-budget
  tests remain unchanged and passing.

No tests are run in this architecture-only task.

Implementation test correction: `JobSnapshot` carries lifecycle fields only
(`jobs.py:128`), not budget usage. The initial new regression reached completion
after the fix but its `snapshot.usage` assertion raised AttributeError. Preserve
that failed log and check the sum of this fake provider's actual prepared input
bounds instead, matching the existing service-budget tests. No assertion or
budget coverage is removed.
