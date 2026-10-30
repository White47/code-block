# M: communicate the existing static-claim evidence contract

Execution follows AGENTS.md autonomous mode, specialist routing and boundary
checks. No routine confirmation, automatic commit or extra review per task.

## Evidence and smallest decision

L5's only real attempt returned a validated compact repair, not a full detailed
answer. Its closed final record passed shape/sections and had unprovided_id=0,
but static_requires_ids=3. Repair passed with incomplete coverage. Actual claim
text and IDs were not inspected; the failure code establishes missing citations,
not the truth of those three claims or complete architecture coverage.

response.py:validate_model_answer already rejects basis=static with empty
evidence_ids. tool_loop.py:_ANSWER_GUIDANCE currently states only the unknown
wording requirement; final guidance says only to use provided IDs without
explicitly requiring a nonempty list for every static claim.

Choose explicit existing-contract guidance (A). Do not increase the repair cap
or change partial-answer reconstruction (B), and do not enlarge budgets or add
requests (C). Those alternatives would not directly address the observed omission.
This is a prompt communication correction, not a new evidence/basis contract or
cross-module architecture change. Core routing remains required for traceability.

## M1: failure-first contract and common guidance

Owner: core_implementer. Modify only src/codeviz/assistant/tool_loop.py and
tests/test_assistant_retrieval_budget.py. Preserve all unrelated existing edits.

1. Extend the existing generated-guidance test (or one focused test) to fail on
   the missing explicit static/nonempty/receipt evidence requirement. Verify final
   and repair, including preview/actual parity; keep strict unsupported-static
   rejection coverage. A scripted provider is not evidence of model compliance.
2. Add concise guidance shared by final and repair: static is allowed only with
   nonempty evidenceIds from receiptEvidenceIds supporting that exact claim;
   unsupported content must be explicitly inference/unknown or omitted. Never
   invent IDs or silently present unsupported facts as verified.
3. Preserve the existing unknown-marker requirement and all original ID bytes,
   catalog authority, attribution, schema, validator and budgets. Normal final
   remains multi-claim; the compact one-claim constraint is repair-only.
4. Run only the affected retrieval-budget/loop/response consumers. Record RED,
   GREEN, exact commands and source hash in build/static-claim-guidance-20261027.
   No API, user-source/key read, Git, progress-file update or full suite.

## M2: production SDK and stage boundary

Owner: main. Adapt L's actual pinned-SDK/fake-HTTP harness to verify a normal
multi-claim static final using valid receipt IDs succeeds without repair, empty
excerpt remains a gap, unprovided ID is still rejected before one compact repair,
and repair length still fails. No assertion of real model adherence.

Inspect only M's diff against L baseline. One M boundary review, then run full
unittest, Ruff, Windows/Linux mypy once on the fixed snapshot. Reuse unchanged
frontend evidence after resource hash comparison. No second review for prompt-only
or fixture-only fixes that do not change semantic-core invariants.

Package offline with current pinned environments and existing dependencies;
verify wheel/frozen/source/TLS/resource parity and fake probes. Retain L software
and real receipts unchanged and never reuse its consumed live marker.

New real acceptance must use the new package, current user-entered memory-only
key and confirmed scope. Complete independent work first. Check whether the user
already saved a key or started a request before asking or sending. At most one
normal analysis with a new version-bound marker; no automatic retry. Report
partial/repaired vs normal results honestly; full-project coverage and complete
native acceptance require their own evidence. Append at most five progress lines
at the boundary or genuine blocker.
