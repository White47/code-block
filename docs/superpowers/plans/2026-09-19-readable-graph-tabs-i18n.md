# Readable Graph, Closable Tabs, and Language Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Tk desktop app vertically readable, close individual tabs with `×`, and offer persistent Chinese/English UI settings.

**Architecture:** Keep analysis and graph facts untouched. Change only layout hints, Canvas input/scene painting, Tk tab lifecycle, and a separate presentation-only locale store. Existing per-project view state remains the authority for opened tabs and manually moved nodes; a separate app-data preference stores the global language.

**Tech Stack:** Python 3.11+, standard-library Tkinter/ttk, unittest, Ruff, mypy. No runtime dependencies.

---

## File map

- `src/codeviz/views/layout.py`: deterministic vertical coordinates.
- `src/codeviz/ui/graph_canvas.py`: vertical edges, scene bounds, wheel/Page Up/Down.
- `src/codeviz/ui/tkapp.py`: responsive window, closeable Notebook tabs, reset layout, Settings control and live text updates.
- `src/codeviz/ui/i18n.py`: translation catalog and `tr(locale, key)`; no parser imports.
- `src/codeviz/ui/preferences.py`: bounded global settings JSON under `CacheStore.data_dir`, atomic writes.
- `src/codeviz/ui/controller.py`: explicit clear of current project's saved manual positions.
- `tests/test_views.py`, `tests/test_ui_graph.py`, `tests/test_ui_tkapp.py`, `tests/test_ui_preferences.py`: regression tests.
- `README.md`, `docs/UX_SPEC.md`, `docs/KNOWN_LIMITATIONS.md`: describe shipped controls and remaining limits.

### Task 1: Vertical graph facts and drawing

- [ ] **Step 1: Write failing tests.** In `tests/test_views.py`, construct a 3-node Architecture and a 3-node Execution view (the latter with `CONTROL_FLOW` edges). Assert every automatic `x` equals the same reading-lane coordinate and the ordered `y` values strictly increase. In `tests/test_ui_graph.py`, assert a non-hierarchical forward edge starts at the source bottom center and ends at the target top center.
- [ ] **Step 2: Verify red.** Run `python -B -m unittest tests.test_views tests.test_ui_graph -v`; expect the new coordinate/edge assertions to fail, not import errors.
- [ ] **Step 3: Implement.** In `layout_positions`, keep current containment/topological ordering but map ordinal to `y` with a fixed node-height-aware gap and keep `x` constant. In `graph_scene`, route primary forward edges bottom-to-top for every graph kind; retain distinct loop-back paint and points.
- [ ] **Step 4: Verify green.** Run the same tests and confirm all pass, including existing layout/scene contracts. Do not alter `CodeGraph`, semantics, or source anchors.

### Task 2: Vertical navigation and project-layout reset

- [ ] **Step 1: Write failing tests.** In `tests/test_ui_tkapp.py`, with a live Tk fixture, call `GraphCanvas._on_mousewheel`/Page Down on a tall scene and assert `canvas.yview()[0]` increases; verify scrolling a short scene is harmless. Add a controller test that moving a node then clearing positions leaves `expanded_ids`, `open_tabs` and analysis intact.
- [ ] **Step 2: Verify red.** Run `python -B -m unittest tests.test_ui_tkapp tests.test_ui_controller -v`; expect absent wheel/reset methods or unchanged scroll/positions.
- [ ] **Step 3: Implement.** Bind wheel (`<MouseWheel>`, Linux `<Button-4/5>`) and Page Up/Down while Canvas has focus; use `yview_scroll` and confine wheel behavior to the graph widget. Add `AppController.reset_positions()` using `dataclasses.replace` and `_persist_view_state()`; add a graph-toolbar `重置项目布局` action that rebuilds currently open graph views and labels its project-wide scope. Keep old manual coordinates until the user invokes it.
- [ ] **Step 4: Verify green.** Run focused tests. Manually inspect a small and a tall nanoGPT graph at normal font size. The existing horizontal scrollbar remains a fallback for manually moved nodes.

### Task 3: Close individual Notebook tabs

- [ ] **Step 1: Write failing tests.** In `tests/test_ui_tkapp.py`, open a graph, a main source tab, a side source tab and Problems. Assert Overview cannot close; clicking/calling the close handler removes exactly one target, destroys its frame, updates `view_state.open_tabs`, allows reopening, and removes the split pane after its last tab. Assert closing while analysis is active does not crash.
- [ ] **Step 2: Verify red.** Run `python -B -m unittest tests.test_ui_tkapp -v`; expect the new close-handler tests to fail because no close handler is present.
- [ ] **Step 3: Implement.** Append a visible `  ×` suffix to every non-Overview Notebook title. Bind Notebook mouse clicks; detect clicks within the last ~22 pixels of the tab `bbox`, return `break`, and route to one `_close_tab(notebook, tab_id)` method. Remove all mappings and widgets for the corresponding graph/source/Problems tab, select an adjacent tab, collapse an empty side notebook, then persist `open_tabs`. Bind `Ctrl+W` for the selected closeable tab. Retain native OS window close for app exit.
- [ ] **Step 4: Verify green.** Run focused Tk tests. Check a file can be reopened on both sides and that a closed tab does not return on application restart.

### Task 4: Global language preference and presentation strings

- [ ] **Step 1: Write failing tests.** In `tests/test_ui_preferences.py`, assert a missing settings file defaults to `zh-CN`, `en` round-trips, invalid/oversize JSON falls back safely, and a write failure leaves the in-memory selection usable. Add Tk tests for switching language after opening graph/source tabs without changing source names or requiring re-analysis.
- [ ] **Step 2: Verify red.** Run `python -B -m unittest tests.test_ui_preferences tests.test_ui_tkapp -v`; expect missing locale/store/control failures.
- [ ] **Step 3: Implement.** Add `tr(locale, key)` with `zh-CN` and `en` catalogs, English fallback and stable keys. Add an atomic, size-limited `preferences.json` in the app data directory; never place it under the analyzed project. In Settings add a Language selector and update visible shell/widget text on change. Localize static buttons, tabs, empty states, graph legend, inspector section labels, status and app-generated error dialogs. Leave real source text, symbol names, anchors and issue codes untouched. On persistence errors show a translated warning but keep the newly selected language for this run.
- [ ] **Step 4: Verify green.** Run focused tests. Inspect both locales on Overview, Architecture, Training Flow, Search, Problems, Inspector, source and Settings, including already-open tabs.

### Task 5: Responsive opening size, docs and complete verification

- [ ] **Step 1: Write failing tests.** Add a pure function test for initial geometry under `1366×768` and `1920×1080`: width/height must fit inside the screen with margin, without hard-coded `1440×880` overflow.
- [ ] **Step 2: Verify red.** Run `python -B -m unittest tests.test_ui_tkapp -v`; expect the new geometry helper test to fail.
- [ ] **Step 3: Implement.** Use the tested geometry helper during `CodeVizApp` creation; preserve a practical minsize without exceeding the current screen. Update `README.md`, `docs/UX_SPEC.md`, and `docs/KNOWN_LIMITATIONS.md` to describe tab `×`, vertical scrolling/reset and Language settings. Do not claim every large graph fits one screen.
- [ ] **Step 4: Verify green and regressions.** Run:

  ```powershell
  .\.venv\Scripts\python.exe -m ruff check src tests
  .\.venv\Scripts\python.exe -m mypy
  .\.venv\Scripts\python.exe -m mypy --platform linux
  .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
  .\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
  ```

  Expect zero new lint/type/test/build failures. Compare any skips with the baseline; record visual checks separately because headless CI cannot prove window appearance.
- [ ] **Step 5: Review and commit.** Inspect `git diff --check`, file-by-file diff, and `git status`; commit only files from this plan in focused commits. Do not push without separately confirming remote intent.
