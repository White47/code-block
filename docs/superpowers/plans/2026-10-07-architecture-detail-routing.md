# Architecture Detail and Routing Implementation Plan

> **For agentic workers:** Use subagent-driven-development, TDD and repository role routing. Keep existing uncommitted changes; do not commit, push, migrate or clear user caches.

**Goal:** Make nanoGPT's architecture informative, details genuinely local, and the canvas readable with dimension-aware placement and obstacle-aware edges.

**Architecture:** Reuse static ModelAnalysis, Semantic Graph and verified occurrence paths. First-open disclosure is explicit and distinguishable from later user depth choices. Details use a selected-source-local traversal, while identities remain globally validated. Layout/routing is presentation-only; saved positions remain pinned.

**Tech Stack:** Existing Python/unittest/Ruff/mypy, React/TypeScript/React Flow/Vitest, pywebview/WebView2 and PyInstaller. No dependency change required by this plan.

## Evidence and approved contract

User approved option B on 2026-10-07 and supplied `E:\tmp\nanoGPT-master`. Static diagnosis found 12 Python files, 525 symbols and five model definitions. GPT has eight recognized instances; its transformer has five recognized members. Current graph counts at depths 1–5 are 6/11/12/16/24. Root GPT detail equals the main depth-1 graph. The container is not missing from parsing.

The initial model view must reveal the major container members and show the actual selected depth. The user can subsequently shrink to depth 1 without automatic re-expansion. Ordinary models without such containers retain their prior first-level defaults. Details are rooted at the selected occurrence with relative depth and bounded node/edge budgets; global identity validation and source anchors stay unchanged. Root details must add internal information, not reproduce the same overview. Leaf nodes explain unavailable internals rather than creating a replica. Detail data must not leak unrelated root branches.

Unknown `range(config.n_layer)` remains one template with unknown repetition, never an asserted 12. A symbolic repetition label is allowed only with an actual source-backed expression; otherwise show an unknown-count explanation. Structure edges are containment, not invented forward execution.

## Task 1: Failing regression coverage

Files: new `tests/test_architecture_details.py`; frontend layout/routing tests in `frontend/src/app/components`; optional inline synthetic fixture owned by the test file.

- [x] Test writer creates nanoGPT-like ModuleDict/ModuleList/custom Block fixtures and observes behavioral RED for local root detail and richer initial disclosure.
- [x] Compare actual node identities and anchors: GPT root detail adds content, a Block occurrence excludes sibling lm_head and unrelated root data; invalid paths and cycles remain bounded.
- [x] Assert symbolic repetitions never assert 12, leaf detail is unavailable, and manual/cache positions and annotations survive.
- [x] Frontend tests verify measured rectangles, edge paths avoiding third-party node interiors, distinct parallel channels, automatic island non-overlap and preserved pinned positions.

Representative backend assertion:

```python
main = controller.view(ViewKind.ARCHITECTURE, gpt_id)
detail = controller.detail_view(ViewKind.ARCHITECTURE, gpt_id, gpt_id)
assert {n.id for n in main.nodes} != {n.id for n in detail.nodes}
assert any("transformer[" in (n.code_name or "") for n in detail.nodes)
```

Run `.desktop-venv/Scripts/python.exe -B -m unittest tests.test_architecture_details -v`; expected failures must be semantic assertions, not import/environment errors.

## Task 2: Source-local details and initial disclosure contract

Core implementer owns `src/codeviz/views/architecture_structure.py`, `structure_identity.py`, `src/codeviz/ui/controller.py`, `presenter.py`, `web_contract.py` and narrowly needed view helpers. Do not rewrite Python Parser/Resolver or import target code.

- [x] Architect reviews optional initial-depth hint integration before implementation; preserve existing positional bridge contracts.
- [x] Implement selected-occurrence-relative detail traversal without modifying underlying facts or accepting forged IDs; retain all budgets and cycle guards.
- [x] Add a source-backed initial-depth hint for model roots whose direct container hides major members; explicit depth requests remain authoritative.
- [x] Preserve inspector/source and valid detail selection, scoped data nodes, layout/cache compatibility and static uncertainty.
- [x] Run new tests, structure/controller/view/contract tests, Ruff and both Windows/Linux mypy. Independent spec review precedes code-quality review.

## Task 3: Measured canvas layout, routing and islands

Implementer owns `frontend/src/app/components/GraphEditor.tsx` and new focused geometry/layout/routing helpers; App initial-depth integration, `types.ts`, i18n or Inspector only if needed. Test writer owns test files. No shared writes with backend worker.

- [x] Feed measured node bounds into vertical placement; preserve pinned coordinates as fixed obstacles and maintain top-to-bottom structure with parallel peers.
- [x] Route orthogonal edges through available space using all visible obstacle rectangles. Distinguish parallel paths and self/loop return lanes; keep original edge direction/type/confidence and labels.
- [x] Treat detail islands as rectangular obstacles for automatic initial placement. Do not relocate saved islands or user positions silently.
- [x] Honor initial-depth hint once per new model context and show that depth on controls; fewer-layer actions must not bounce back.
- [x] Test and preserve measured-node reuse, context/stale-response guards, viewport stability, source tabs, search and persistence.
- [x] Run frontend tests/lint/typecheck/build; independent spec and code-quality review.

Non-planar graphs or user-created overlaps may still have crossings; do not claim universal zero crossings. Prefer no third-party node penetration and fewer shared segments within bounded routing work. Failures must remain visible, not silently omit edges.

Late native acceptance added strict `graphRoots/selectedId` preservation. It exposed an App-only local selection leak despite backend Inspector being transient. Add `frontend/src/app/DetailSelection.test.tsx`, keep island selection in local Inspector/highlight state, preserve persistent main selection, and clear/cancel local context when its island closes or the project/root changes. Do not weaken the native state comparison. Task3's final gate remains open until this fix receives fresh tests and independent review.

## Task 4: Actual nanoGPT and delivery verification

Main owns docs, build and safe test-host lifecycle. Test writer owns a dedicated attach-only verifier under `scripts`.

- [x] Analyze the user's nanoGPT statically in an isolated application cache and verify actual first-open members, richer root detail, local Block detail, source highlighting, shrink, manual positions and nonoverlapping islands.
- [x] Verify actual rendered node and edge geometry; record remaining crossings rather than claiming all paths are clean from endpoint checks alone.
- [x] Run full backend/frontend checks, build frontend, wheel and a separate `dist/architecture-detail-routing/CodeViz` PC package, preserving prior releases.
- [x] Use unique available CDP ports; attach only to verified owned hosts. Test ordinary Python and previous structure/search/source/cache regressions; close only recorded test windows normally.
- [x] Update UX, limitations and final evidence with exact changed files, results and final application path. No analyzed-project writes, source execution or user-cache removal.

Final evidence: `docs/ARCHITECTURE_DETAIL_ROUTING.md`; backend 675 OK (2 skips), frontend 184 PASS, source/wheel/frozen nanoGPT 9 checks each, structure 9, generic 5, MVP 5. All independent reviews APPROVE. Local selection leak and explicit same-root navigation priority were fixed without weakening native assertions. Residual crossings are documented, not claimed eliminated.
