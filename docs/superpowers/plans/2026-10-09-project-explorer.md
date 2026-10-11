# Project Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Architect reviews the contract before core implementation; unit writers use TDD. Stay in the user-selected current workspace. No automatic Git commit/push.

**Goal:** Add a real project tree and safe file management to the existing native PC application, then add internal drag/drop only after base acceptance.

**Architecture:** A project filesystem service owns listing, revisions and prepare/apply mutations. Existing bridge locking serializes it with safe document saves. React Explorer is a presentation layer that calls the existing shared-draft guard before mutating any affected path, and refreshes real session state without resetting unrelated drafts.

**Tech Stack:** Existing Python/unittest/Ruff/mypy, React/TypeScript/Vite/Vitest, Monaco, WebView2 and PyInstaller. No full IDE migration or remote services. Native recycling adapter must provide recycle-only behavior; unavailable platforms fail closed.

## Baseline and workflow

Base commit `df0db6b`; frontend 259 PASS; Python 744 OK (3 skips). Only existing untracked `.codex/` belongs to user configuration and stays untouched. All file mutations use owned temporary fixtures; never write `E:\tmp\nanoGPT-master`. Main owns specification, plan, acceptance tooling and release docs. Review specs before quality; after each completed task inspect actual diffs and tests.

## Task 1 — Architecture and regression contract

Files: `docs/superpowers/specs/2026-10-09-project-explorer-design.md`, this plan; new `tests/test_project_explorer.py`, `tests/test_text_documents.py` (test_writer only).

- [x] Architect approves concrete listing/create/prepare/apply API, project epoch, text policy and native recycle adapter.
- [x] test_writer creates missing-API assertions and runs expected RED, not import errors. Main verified Explorer 37 tests / 37 expected failures, text 17 tests / 14 failures, zero setup/import errors. Frontend navigation first tranche 18 tests / 18 expected missing-tree failures. Cases include no-analysis listing, safe sorting, pages, stale A→B→A, exclusive creation, duplicate names, traversal/ADS/reserved names, protected ancestors, links, subtree size/work limits, content conflicts, single-use token, recycle failure without fallback, text bytes/BOM/EOL and Outdated.

Required bridge contract (schemaVersion envelope retained):

```typescript
interface ExplorerEntry {
  path: string; name: string; kind: 'file' | 'directory' | 'link' | 'other';
  revision: string; readOnlyReason: string | null;
  canOpen: boolean; canCreate: boolean; canRename: boolean; canDelete: boolean;
}
interface ExplorerDirectory {
  projectPath: string; projectEpoch: string; directory: string;
  directoryRevision: string; entries: ExplorerEntry[]; nextCursor: string | null;
}
interface ExplorerPrepared {
  operationToken: string; operation: 'rename' | 'delete';
  path: string; newPath: string | null; affectedPaths: string[];
}
interface ExplorerMutation {
  operation: 'create' | 'rename' | 'delete';
  oldPath: string | null; newPath: string | null; affectedPaths: string[];
}
// list_project_directory({projectPath,directory,projectEpoch?,cursor?}) -> ExplorerDirectory
// Only directory='' may bootstrap an omitted epoch; all mutations require it.
// create_project_entry({projectPath,projectEpoch,parent,parentRevision,name,kind:'file'|'directory'}) -> ExplorerMutation
// prepare_project_entry_operation({projectPath,projectEpoch,path,revision,operation:'rename'|'delete',newName?}) -> ExplorerPrepared
// apply_project_entry_operation({projectPath,projectEpoch,operationToken}) -> ExplorerMutation
```

Test example in a fixture-based unittest class:

```python
method = getattr(self.bridge, "list_project_directory", None)
self.assertTrue(callable(method), "Explorer API is not implemented")
reply = method({"projectPath": str(self.root), "directory": ""})
self.assertTrue(reply["ok"], reply)
self.assertEqual(reply["data"]["directory"], "")
self.assertTrue(reply["data"]["projectEpoch"])
```

Run `.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_project_explorer.py' -v` and analogous `test_text_documents.py`. Record exact failure count and cause before production edits.

## Task 2 — Filesystem service and safe text policy

Create `src/codeviz/ui/project_explorer.py`, `src/codeviz/ui/project_file_policy.py`, `src/codeviz/ui/recycle_bin.py`; modify `source_editing.py`, `web_bridge.py`, `web_contract.py`, `desktop.py` (API whitelist only), and only necessary packaging config if a reviewed recycle adapter requires an explicit dependency. core_implementer owns production Python/config; writer owns tests.

- [x] Implement one-level paged directory reads, signed in-memory revisions and bounded scanning. Never recursively build a full UI tree.
- [x] Bind explorer lifecycle during project open; all mutations use existing bridge `_write_call` lock and reject Analyzing.
- [x] Add exclusive empty file/single folder creation with no overwrite or ancestor scaffolding; implement same-parent no-overwrite rename and prepared recycle-only delete.
- [x] Validate project/parent/entry/subtree identities and content both before prepare and immediately before apply. Token expiry/single use, limits and exact errors must be tested. Never silently trim an affected subtree.
- [x] Isolate text type/encoding policy; preserve existing Python coding-cookie checks. Generic text supports strict UTF-8/BOM and UTF-16 BOM with exact byte roundtrip, rejecting binary/control data. All protected-path saves are read-only.
- [x] Invalidate only affected source revisions, mark existing analysis Outdated without modifying graph/cache/annotations or running analysis. Failure is not reported as success; unknown mutation outcomes force refresh and forbid automatic retry.
- [x] Run focused tests, full core tests, Ruff and Windows/Linux mypy; architect spec review then independent reviewer quality review. Base evidence and final approvals are recorded in `docs/PROJECT_EXPLORER_VALIDATION.md` (2026-10-11).

```powershell
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_*explorer*.py' -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -p 'test_text_documents.py' -v
.desktop-venv/Scripts/python.exe -B -m unittest discover -s tests -q
.desktop-venv/Scripts/python.exe -m ruff check src tests scripts
.desktop-venv/Scripts/python.exe -m mypy
.desktop-venv/Scripts/python.exe -m mypy --platform linux
```

## Task 3 — Integrated Explorer and source lifecycle

Create `frontend/src/app/components/ProjectExplorer.tsx`, `frontend/src/app/explorerState.ts`, `frontend/src/app/components/ProjectExplorer.test.tsx`, `frontend/src/app/ExplorerNavigation.test.tsx`; modify `Sidebar.tsx`, `App.tsx`, `state.ts`, `sourceDocuments.ts` only if lifecycle helper needed, `types.ts`, `api/bridge.ts`, `monaco.ts`, `SourceEditor.tsx`, `i18n.ts`, `styles.css`. implementer owns production frontend; test_writer owns new test files.

- [x] Write and run RED tests for root loads without analysis, fold/load/retry/page, file open, keyboard/context actions, create, rename/delete confirmation and cancellation, dirty directory descendants, operation errors, stale responses, protected entries and no draft loss.
- [x] Render an accessible compact tree (`role=tree/treeitem`, levels, expansion, keyboard focus) with existing theme and icons. Toolbar/context menu and naming dialogs need visible loading/error/status feedback; no replacement theme or general refactor.
- [x] Add exact DTO/method types above; mutations have no misleading frontend timeout. Optional API detection keeps old mock fixtures/legacy hosts usable while real native bridge uses tree.
- [x] Integrate source file opens into existing tabs/split and use text-language mapping, not Python coloring for every file. All affected-path mutations wait pending saves, guard relevant dirty documents, prepare freshly after saves, lock navigation during commit, then release/reopen affected source identities and refresh session/tree.
- [x] Failed mutation does not close tabs or erase draft/model undo. Canceled delete does not call apply. Preserve unrelated drafts and graph selection, support side/source restoration and project/settings/native close guards.
- [x] Run frontend tests/lint/typecheck/build; architect spec review then independent reviewer quality review. Base final: 331 frontend tests, approved spec and quality; see validation record.

```typescript
// Integration test behavior, not an assertion on a mocked Explorer element.
await user.click(screen.getByRole('button', { name: '重命名' }));
await user.click(screen.getByRole('button', { name: '取消' }));
expect(applyOperation).not.toHaveBeenCalled();
expect(documentStore.get(fileKey)?.text).toBe(draft);
```

```powershell
npm.cmd --prefix frontend test -- --run
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

## Task 4 — Native acceptance and release

Main owns `scripts/explorer_smoke_host.py`, `scripts/verify_explorer_native.py`, `docs/PROJECT_EXPLORER.md`, `docs/PROJECT_EXPLORER_VALIDATION.md`, README/PRD/UX/security/limitations updates, and separate `dist/project-explorer/CodeViz` build. Existing releases remain intact.

- [x] Real WebView2 fixture verifies fold/open/edit/create/rename file+folder/cancel+delete/readonly/errors/Outdated and actual recycle receipt; only disposable fixtures enter system trash. Final source/wheel/EXE each passed 11 ordinary plus 3 restored-alias checks.
- [x] Re-run prior editable-source and general/MVP navigation checks to detect regression; no weakening source-anchor checks or treating mock Monaco as PC proof. Prior native checks: editable source 10, general navigation 5; final base unit regressions pass.
- [x] Full frontend/core/no-browser CI checks plus builds; inspect git diff and independent review. Close only owned native test windows normally. Final base Python 819 (5 skips), no-browser 819 (8 skips), frontend 331; all base reviews approved.
- [x] Publish changed files, exact tests/skips, limitations, executable path and verification evidence. Do not commit/push without renewed authorization. Base documentation is published locally; Task 5 needs separate release verification below.

## Task 5 — Internal drag/drop after base gates

Only start after Tasks 1–4 pass. Architect reviews extension, test_writer writes RED tests, core_implementer extends prepared operation with destination, implementer adds tree DnD using same draft guard, reviewer independently verifies.

- [x] Define move-to-directory token and reject self/descendant/collision/protected-path/cross-project moves; no external OS import/copy and no automatic import repair. Backend spec/quality approved; 48 focused tests include uncertain native outcomes invalidating both prefixes.
- [x] Test canceled/failed dirty file/folder moves preserve state; native verification confirms disk paths, tabs/model identity and Outdated, without overlapping extra write ownership. Final front move 84 tests; native source/wheel/EXE each 9 checks, including actual hover geometry stability.
- [x] Re-run task 4 release validation before claiming drag/drop complete. Final front 415; core 867 (7 skips), no-browser core 867 (10 skips); source/wheel/EXE each 11 base plus 3 alias checks. Final native editor 10 and general navigation 5; spec and independent quality re-approved after hover-layout fix. See final validation record.

## Out of scope

Git status badges, full IDE Git integration, filesystem watcher, arbitrary binary preview/editing, upload, runtime execution, terminal/debugger/LSP, permanent-delete fallback, automatic import rewriting and external file drag-in. Recovery of forcibly terminated drafts remains out of scope.
