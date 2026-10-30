# Evidence navigation guidance

Process: AGENTS.md autonomous execution; ordinary presentation work inline.
This slice follows the existing response contract and the 2026-10-28 offline
navigation findings. No new navigation authority, dependency or API call.

## Goal and decision

`graphAvailable` is candidate eligibility, not a successful navigation check.
The current evidence UI omits that distinction, while `AI_EVIDENCE_INVALID`
incorrectly prescribes another explanation even when the same citation opens
source. Clarify the existing contract at the point of use.

Use a shared bilingual hint for evidence lists containing graph candidates,
and neutral bilingual guidance for the existing error code. Changing the
navigation resolver or guessing a missing graph target is outside this slice.

## Task 1 — presentation

- Edit `frontend/src/app/components/AssistantEvidence.tsx`: show one muted
  candidate explanation when any card has `graphAvailable`; retain each
  source/graph callback, evidence ID, disabled state and confidence unchanged.
- Edit `frontend/src/app/i18n.ts`: add `aiGraphCandidate` in Chinese/English;
  describe required local static-view checks and source fallback. Replace
  `AI_EVIDENCE_INVALID` copy with neutral result/evidence verification language:
  the shared code also covers answer validation, not only navigation. Recommend
  project/consent checks and local viewing only for existing citations. Do not
  assert that the live rejection cause has been identified.
- No new tests for copy alone. Run existing AssistantPanel, AssistantArchitecture,
  AssistantIntegration, assistant/ui, navigation, source-navigation and store-jobs
  consumers. Existing navigation protections must continue to pass.

## Task 2 — delivery boundary

- Update `docs/CODE_AGENT_GUIDE.md` to describe the visible candidate hint.
- Inspect the slice diff against saved local baselines; preserve all prior work.
- One independent review of this slice, with concrete file/symbol evidence.
- Once at the boundary: frontend suite, lint, typecheck/build; Python unittest,
  Ruff and Windows/Linux mypy. Never rerun an unchanged passing gate.
- Record pass/skip/todo separately and append at most five progress lines.
- Keep PID32384 running with its memory-only key. Do not replace its frozen
  resources, restart it, send a request, commit or push. Frontend source build
  is not native acceptance of a newly packaged desktop binary.

## Acceptance

Candidate limitations are visible before a click; the existing navigation
error no longer asks for a model resend. Source-only and disabled controls
retain their existing behavior. Public DTOs, trust checks, quotas, Python
runtime and real-request authorization remain unchanged.
