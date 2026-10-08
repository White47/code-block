# Monaco Source Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Do not implement source writes before architecture review approves the concrete contract.

**Goal:** Add offline, VSCode-style Python editing with explicit safe save and unsaved-change protection to the existing PC application.

**Architecture:** Keep analysis static and preserve existing graph/source navigation. A bounded backend document service owns encoding, original-byte revisions, project identity, conflict detection and atomic replacement. A frontend project/file document store owns shared drafts; Monaco is a presentation adapter, not the source of persistence truth. Native close requests must use the same save/discard/cancel guard as UI navigation.

**Tech Stack:** Existing Python/unittest/Ruff/mypy, React/TypeScript/Vite/Vitest, pywebview/WebView2 and PyInstaller; add a pinned local Monaco Editor dependency only. No CDN, language server, terminal or runtime execution.

## Scope and workflow

User approved editing on 2026-10-08 and chose the current workspace. Preserve `.codex` and existing work; no automatic commit, push or merge in this task. Baseline frontend: 184 PASS; most recent core baseline: 678 OK (5 skips) in a no-Playwright interpreter. Test source writes only in owned temporary fixtures, never in the user's nanoGPT checkout. Keep prior release directories.

## Task 1: Architecture contract and failing coverage

Files: new `docs/SOURCE_EDITING.md`; new backend and frontend regression files, with disjoint writer ownership. Architect is read-only; main owns documentation.

- [x] Review project-bound read/save requests, exact revisions, encoding/EOL preservation, link rejection, bounded input, conflict errors and native close handshake. Architect Nash approved with project epoch, ACL and single-use native nonce constraints.
- [x] Specify concrete API and errors in SOURCE_EDITING.md before production writes.
- [x] Write regressions for plain save/Outdated, external change, stale project, links/path escape, encoding, permission/replace failure and no source execution.
- [x] Write frontend regressions for shared drafts, Ctrl+S, tab/side/project/exit guards, failed-save retention, stale completion and source navigation.
- [x] Run tests before implementation and record expected RED, not import/setup failures.

Examples of required assertions:

```python
# Example within the owned-temp-fixture SourceEditingTests.
document = self.success(self.open())
# After another process changes the file, saving with the old revision must fail.
self.path.write_bytes(b"# external edit\n")
self.error(self.save(document, "# draft\n"), "SOURCE_CONFLICT")
self.assertEqual(self.path.read_bytes(), b"# external edit\n")
```

```typescript
// A canceled navigation cannot dispose the draft or call the project mutation.
expect(screen.getByRole('dialog')).toBeVisible();
await user.click(screen.getByRole('button', { name: '取消' }));
expect(openProject).not.toHaveBeenCalled();
expect(store.get(documentKey)?.text).toBe(draft);
```

## Task 2: Safe backend document save and desktop boundary

Likely files: new `src/codeviz/ui/source_editing.py`; `controller.py`, `web_bridge.py`, `web_contract.py`, `desktop.py`; backend tests. Core implementer owns production Python only.

- [x] Implement the architect-approved read/save service independently of parser/resolver facts. Preserve old read_source contract for compatibility.
- [x] Reject stale project/revision and unsafe target before replacement; failed saves retain old source and draft. Never create arbitrary new source paths or force-overwrite conflicts.
- [x] Successful save marks existing analysis Outdated; absent analysis stays NotAnalyzed. Preserve layout/annotations and require explicit re-analysis.
- [x] Expose only bounded whitelisted API methods/arity; only same-origin bundled Worker permission is added to CSP.
- [x] Implement native-close approval handshake so OS close is canceled until the frontend save/discard/cancel decision completes.
- [x] Run focused tests, full core unittest, Ruff and Windows/Linux mypy. Architect spec review must pass before independent code-quality review.

Commands:

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_source_editing*.py' -v
.desktop-venv/Scripts/python.exe -m ruff check src tests scripts
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
```

## Task 3: Monaco, shared drafts and navigation guards

Likely files: `frontend/package.json`, lockfile; `types.ts`, `api/bridge.ts`; focused source document store and Monaco adapter; `SourceViewer.tsx`, `EditorTabs.tsx`, targeted App/state integration, i18n/styles and frontend tests. Frontend implementer owns production frontend; writer owns test files.

- [x] Install pinned Monaco locally; use ESM Python support and a Vite-bundled Worker. Verify package APIs from actual installed files, not assumed version compatibility.
- [x] Keep one project/file draft shared across source tabs and split view; switching graph/tab does not discard edits or undo history. Prevent the existing tab cap from silently evicting dirty documents.
- [x] Add dirty indicator, explicit Save and Ctrl+S. Save uses the captured baseline revision; failures do not clear dirty state. Pending saves are project mutations without misleading frontend timeouts.
- [x] Guard closing the last source view, replacing/closing split source, project changes, settings hydration, re-analysis and native exit. Save all requested documents sequentially; cancel or any conflict/error prevents the destructive transition.
- [x] Preserve graph-to-source navigation and anchor highlighting. Dirty/Outdated contexts explicitly warn that graph and source line anchors can be stale.
- [x] Render default dark Python coloring, line numbers, find/replace, undo/redo and folding. No save-on-type, completion server or code execution.
- [x] Run frontend tests/lint/typecheck/build and independent spec then quality review.

Commands:

```powershell
npm.cmd --prefix frontend test -- --run
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

## Task 4: Native acceptance, packaging and documentation

Main owns final evidence/docs/build and test-window lifecycle; dedicated attach-only native verifier uses an owned temporary Python fixture.

- [x] Verify actual Monaco token colors, edit/undo/Ctrl+S, source-tab dirty mark, save/discard/cancel paths, real external conflict and Outdated; unit/navigation regressions cover the explicit re-analysis guard.
- [x] Verify Worker assets and no remote/CDN requests in source, installed wheel and a separate frozen `dist/monaco-source-editing/CodeViz` PC package.
- [x] Migrate relevant native source-reading assertions from plain-text table to actual Monaco content/selection; do not weaken source-anchor checks or treat unit mocks as native proof.
- [x] Update PRD/UX/security/cache/acceptance and limitations to explicitly replace only the old no-editor boundary. Preserve static analysis/no execution/no upload restrictions.
- [x] Run final full tests, lint/typecheck/build and independent final review; close only verified owned test windows normally. Publish exact evidence and executable path; leave Git actions to a separate user request.

## Completion evidence

Implemented and independently reviewed. Final frontend: 259 tests PASS; Python: 744 tests OK (3 environment skips), optional-browser-free CI interpreter: 744 tests OK (6 skips). Actual source/wheel/frozen PC entrypoints each passed 10 source-editing checks; both legacy native suites passed 5 flows each. Final notice-only rebuild has identical runtime JS across Vite/wheel/frozen, and the frozen executable passed all 10 checks again. See [SOURCE_EDITING_VALIDATION.md](../../SOURCE_EDITING_VALIDATION.md) for files, limitations and evidence directories. No Git commit or push performed.

Build commands:

```powershell
.desktop-venv/Scripts/python.exe -m pip wheel . --no-deps --no-build-isolation -w dist/monaco-source-editing
.desktop-venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath dist/monaco-source-editing --workpath build/monaco-source-editing-packaging scripts/codeviz.spec
```

## Out of scope

Terminal, debugger, Python language server, automatic refactoring, new-file/project scaffolding, arbitrary filesystem writes, autosave, source uploads, runtime execution, persistent recovery of drafts after forced process termination. Syntax colors alone do not imply exact semantic resolution.
