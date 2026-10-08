# Structure Exploration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Follow repository role routing; use disjoint ownership for backend, frontend, tests and native verification.

**Goal:** Make structural expansion change actual visible contents and make project/model graphs understandable, without sacrificing source traceability.

**Architecture:** Project structure is a bounded source-backed view over the existing Symbol Index. Model expansion is a view-only, instance-context projection through verified definition references. Relationship radius remains separate from structure depth; frontend reports differences from completed graph results.

**Tech Stack:** Existing Python dataclasses / unittest / Ruff / mypy, React 19 / TypeScript / React Flow 12.8.5 / Vitest, pywebview + WebView2, PyInstaller onedir.

Approved specification: `docs/STRUCTURE_EXPLORATION.md`. Architect reviews concrete bridge and persistence integration before implementation. Existing dirty worktree belongs to the user; no commits, migrations, dependency changes or cleanup of user caches.

## Task 1: Failing structure and navigation regressions

Files: create `tests/test_structure_exploration.py`; use existing `tests/fixtures/mvp_project` and `tests/fixtures/general_python_project`. Test writer owns test files only.

- [x] Compare actual source-backed node sets and anchors; observe old behavior failing before production changes.

```python
one = bridge.get_view("Architecture", classifier_id, classifier_id, 1, "both", 1)
two = bridge.get_view("Architecture", classifier_id, classifier_id, 1, "both", 2)
assert one["ok"] and two["ok"]
assert len(two["data"]["nodes"]) > len(one["data"]["nodes"])
assert any(n["codeName"] == "model.Encoder.layers" for n in two["data"]["nodes"])
```

- [x] Test actual depth 3 ModuleList member and depth 4 Block.projection, shrink, deterministic projected identities, two instances, cycles, invalid references, small budgets, projected-node Inspector/source, strict unknown-ID rejection, move/save/reopen and retained annotations.
- [x] Run `.desktop-venv/Scripts/python.exe -B -m unittest tests.test_structure_exploration -v`; record expected behavioral RED, not unrelated environment failures.

## Task 2: Bounded backend projection and source-backed project structure

Likely files: new `src/codeviz/views/architecture_structure.py` / `project_structure.py`; existing `views/builder.py`, `core/model.py`, `ui/controller.py`, `ui/web_bridge.py`, `ui/web_contract.py`, `ui/presenter.py`, `views/layout.py`. Core implementer owns production backend files only.

- [x] Add trailing optional `structure_depth` to graph request / bridge; preserve old positional calls. Validate integer 1–5, reject bool and invalid view/root identities.
- [x] Introduce `ProjectStructure` view and route it to genuine module/class/function ownership facts; structural project container must have explicit provenance.
- [x] Project referenced custom definitions using deterministic occurrence identities, existing source anchors and semantic IDs. Preserve definition/instance distinction, bounded traversal and original graph facts. In the MVP fixture, ModuleList is a real intermediate layer: layers[*] at depth 3 and Block.projection at depth 4, not flattened to satisfy a smaller example depth.
- [x] Provide graph query context describing current structure depth and whether a deeper result exists. Keep relationship hops independently meaningful.
- [x] Resolve projected Inspector and detail navigation through verified IDs. Accept legitimate projected identities in layout/move/cache paths without accepting arbitrary IDs. Retain old cached analysis and positions.
- [x] Run Task 1 tests, existing views/controller/bridge/codec tests, Ruff and mypy; independent backend review before declaring Task 2 complete.

## Task 3: View-aware toolbar, real feedback and readable graph

Likely files: `frontend/src/types.ts`, `app/App.tsx`, `components/QueryToolbar.tsx`, `components/GraphEditor.tsx`, `Overview.tsx`, `Sidebar.tsx`, `EditorTabs.tsx`, `i18n.ts`, `styles.css`; separate feedback / label helpers if useful. Implementer owns frontend production files only. Test writer owns frontend tests.

- [x] Write failing App tests for structural graph response changes, shrink, unchanged response reason, empty model state, real view switching and removal of FunctionFlow controls.
- [x] Type the optional structure-depth API and new view kind; expose project structure from Overview/sidebar.
- [x] Reset depth per root context; request depth independently from hops. On shrink, do not resurrect now-hidden selection or detail islands. Keep stale-response and project-write queue protections.
- [x] Render completed-response feedback with actual node-set changes, status/live announcements, deepest level and truncation reasons; failures remain failures.
- [x] Translate node roles and edge labels without changing machine IDs, source anchors or relationship facts. Explain project versus model structure and static flow.
- [x] Bring newly added structural nodes into view once measured; preserve existing selection/camera race fixes and manual positions.
- [x] Run `npm.cmd --prefix frontend run test`, `lint`, `typecheck`, `build`; independent frontend review.

## Task 4: Final package and actual PC acceptance

Files: create `scripts/verify_structure_exploration_native.py`; reports under `build/structure-exploration-*`; independent package under `dist/structure-exploration`, preserving prior release paths. Main owns build/launch/cleanup, test writer owns attach-only native script.

- [x] Native verifier clicks rendered controls; asserts graph nodes grow/shrink, not only button text. Verify projected source/Inspector, project structure, no invalid FunctionFlow controls, feedback, re-opened cache and language.
- [x] Re-run existing ordinary Python, ML and project-action/canvas regression where applicable; no target-code execution or user-cache mutation.
- [x] Full backend unittest, Ruff, mypy and full frontend checks; build frontend before wheel / PyInstaller.

```powershell
npm.cmd --prefix frontend run build
.desktop-venv/Scripts/python.exe -m pip wheel . --no-deps --no-build-isolation -w dist/structure-exploration
.desktop-venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath dist/structure-exploration --workpath build/structure-exploration-packaging scripts/codeviz.spec
```

- [x] Launch only own test hosts hidden with isolated caches and CDP ports; run final native verifier; close only recorded own process windows.
- [x] Independent final requirement / quality review; update UX / architecture / cache / limitations / acceptance and this specification with actual evidence and final package path. No commit/push unless separately requested.
