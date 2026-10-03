# React Flow Desktop Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace the Tkinter presentation layer with a bundled React Flow desktop application while preserving the existing Python analysis, search, navigation, cache, and source-traceability core.

**Architecture:** Python remains the authoritative analysis service. A small pywebview desktop shell opens a local, bundled Vite build from a random-port static server bound only to loopback and exposes a narrow JSON-safe pywebview bridge. The static server serves assets only: bridge calls never use HTTP and no remote interface is bound. React owns tabs, graph interaction, search presentation, settings, and source viewing. Production builds contain no CDN imports.

**Tech Stack:** Python 3.11, existing AppController/CacheStore/presenters, pywebview, React, TypeScript, Vite, @xyflow/react, Vitest, PyInstaller, GitHub Actions.

---

## Task 1: Record the desktop decision and bridge contract

Files:

- Create docs/adr/008-react-flow-desktop-shell.md
- Modify docs/adr/006-tkinter-first-desktop-ui.md
- Modify docs/ARCHITECTURE.md and docs/PROJECT_STRUCTURE.md
- Create src/codeviz/ui/web_contract.py
- Test tests/test_ui_web_contract.py

- [x] Write tests for JSON-safe session, search/reveal, ViewState, preferences, graph, overview, inspector, problem, source, envelope, and error payloads.

    def test_source_anchor_and_graph_payload_are_json_safe():
        payload = graph_payload(sample_visible_graph())
        assert payload["nodes"][0]["sourceAnchors"][0]["filePath"] == "model.py"
        assert payload["nodes"][0]["confidence"] == "Exact"
        json.dumps(payload)

- [x] Run the focused unittest and confirm RED for the missing contract surface.
- [x] Implement pure functions and `SCHEMA_VERSION = 1`:

    graph_payload(graph: VisibleGraph) -> dict[str, object]
    overview_payload(data: OverviewData) -> dict[str, object]
    inspector_payload(data: InspectorData) -> dict[str, object]
    problem_payload(data: ProblemData) -> dict[str, object]
    source_payload(document: SourceDocument) -> dict[str, object]
    session_payload(...), search_response_payload(...), reveal_target_payload(...)
    preferences_payload(...), view_state_payload(...)
    success_payload(data, request_id=None), error_payload(error, request_id=None, *, safe_message=None)

- [x] Convert enums to strings, tuples to lists, and source anchors to filePath/startLine/startColumn/endLine/endColumn. Do not serialize ASTs or arbitrary Python objects; reject non-finite geometry and whitelist metadata/query context.
- [x] Mark ADR-006 as superseded pending desktop acceptance, retain Tkinter as a temporary fallback, and document the bridge/loopback-static-asset boundary.
- [x] Run the focused test. Ruff and mypy are unavailable in the required system Python and are not installed by this task.

## Task 2: Add the Python desktop bridge and native entry point

实施更新（2026-10-03）：Tasks 1–6 已完成当前 Windows 技术交付、最终复审与重建验收，证据见 `docs/DESKTOP_MIGRATION_VALIDATION.md`。窗口默认 1200×700、最小 1000×640，适配 1366×768 桌面。安全复审后改用 `js_api=None` + 固定 26 方法 expose 白名单，并在框架分发前校验方法名、参数数量与回调标识；不直接暴露桥接对象。pywebview 为 `[desktop]` 可选依赖，不改变无 GUI 分析核心依赖。下列未勾选项保留原始规划而非当前任务状态，实施差异与剩余 UX 边界以验收报告为准。

Files:

- Create src/codeviz/ui/web_bridge.py
- Create src/codeviz/ui/desktop.py
- Modify src/codeviz/ui/__init__.py and pyproject.toml
- Test tests/test_ui_web_bridge.py and tests/test_ui_desktop.py

- [ ] Write tests for open_project, analyze, get_overview, get_view, get_detail_view, search, read_source, preferences, layout, and structured errors.
- [ ] Implement DesktopBridge with one AppController, PreferencesStore, and CacheStore. Expose only JSON-safe methods:

    open_project(path)
    analyze()
    get_overview()
    get_view(kind, root_id=None, selected_id=None, hops=1, direction="both")
    get_detail_view(kind, root_id, source_node_id)
    search(query, max_results=100)
    read_source(anchor)
    get_preferences()
    set_preferences(payload)
    save_layout(payload)
    get_problems()

- [ ] Validate project paths, enum values, anchors, and limits at the Python boundary. Never execute user code and never return Python objects directly to JavaScript.
- [ ] Implement desktop.py with lazy pywebview import, a random-port static server bound only to loopback for `frontend/dist`, a native window minimum size of 1200x760, and DesktopBridge as js_api. The bridge must not use HTTP and the server must stop with the desktop process.
- [ ] Keep tkapp.main importable while changing the codeviz script to codeviz.ui.desktop:main.
- [ ] Run python -B -m unittest tests.test_ui_web_bridge tests.test_ui_desktop -v.

## Task 3: Scaffold a local React Flow frontend

Files:

- Create frontend/package.json, frontend/package-lock.json, frontend/tsconfig.json, frontend/vite.config.ts, and frontend/index.html
- Create frontend/src/main.tsx, frontend/src/api/bridge.ts, frontend/src/types.ts, and frontend/src/styles.css
- Test frontend/src/api/bridge.test.ts

- [ ] Pin React, @xyflow/react, TypeScript, Vite, Vitest, ESLint, and React type packages. Scripts must include dev, build, test, and lint.
- [ ] Set Vite base to `./` so built asset paths work under the packaged random loopback URL.
- [ ] Implement a typed bridge adapter that waits for pywebviewready, calls window.pywebview.api, and returns a clear DESKTOP_UNAVAILABLE error when opened outside the native shell. Do not use fetch for bridge calls or runtime CDN imports.
- [ ] Add the test:

    it("returns a desktop-unavailable error outside pywebview", async () => {
      delete window.pywebview;
      await expect(bridge.openProject("x")).rejects.toMatchObject({
        code: "DESKTOP_UNAVAILABLE"
      });
    });

- [ ] Run npm --prefix frontend ci, npm --prefix frontend test, and npm --prefix frontend run build. Confirm generated JavaScript has no remote import URL.

## Task 4: Implement the PC shell and React Flow views

Files:

- Create frontend/src/app/App.tsx
- Create frontend/src/app/components/ActivityBar.tsx, Sidebar.tsx, EditorTabs.tsx, GraphEditor.tsx, Inspector.tsx, SourceViewer.tsx, SettingsPanel.tsx, and SearchPanel.tsx
- Create frontend/src/app/state.ts
- Modify frontend/src/styles.css
- Test frontend/src/app/state.test.ts

- [ ] Add reducer tests proving Overview, Architecture, Execution, Source tabs and Open to Side state transitions.
- [ ] Implement the VS Code-style layout: Activity Bar, collapsible Sidebar, closeable Editor tabs, central canvas/source area, Inspector, Problems, and native-window-safe scrolling.
- [ ] Map VisibleGraph nodes and edges to custom React Flow nodes and typed edges. Architecture uses vertical layered layout; Execution uses vertical flow layout; selected nodes update Inspector; Enter/double-click opens detail.
- [ ] Render a clicked detail graph as a local in-canvas detail island. Keep source anchors on every visual node and route Open Source/Open to Side through the source tab state.
- [ ] Implement source line numbers/ranges, confidence/evidence, relations/data, Search → Python-planned Reveal in Graph → Inspector → Source, language preferences, separate analysis settings for ignore paths, and cache status/actions.
- [ ] Run npm --prefix frontend test, npm --prefix frontend run lint, and npm --prefix frontend run build.

## Task 5: Connect packaging, command entry point, and CI

Files:

- Create scripts/build-desktop.ps1 and scripts/codeviz.spec
- Modify pyproject.toml, .github/workflows/quality.yml, and README.md
- Test tests/test_packaging_layout.py

- [ ] Add pywebview as a runtime dependency and keep the existing analysis dependencies unchanged.
- [ ] Make the build script run frontend install/build first, then create a PyInstaller onedir build with frontend/dist as bundled data.
- [ ] Add a package-layout test proving frontend/dist/index.html exists and generated assets contain no esm.sh, unpkg, or other remote imports.
- [ ] Extend GitHub Actions with Node install, frontend install/lint/test/build, and a Windows packaging check that does not launch a GUI.
- [ ] Document native-window usage, WebView2 on Windows, frontend development, desktop packaging, and the temporary Tkinter fallback.

## Task 6: End-to-end acceptance and migration cleanup

Files:

- Modify tests/test_mvp_acceptance.py, docs/MVP_ACCEPTANCE.md, docs/KNOWN_LIMITATIONS.md, and docs/IMPLEMENTATION_PLAN.md
- Create tests/test_desktop_acceptance.py

- [ ] Test the three golden paths through the bridge: Open Project → Analyze → Overview → Architecture → detail → Source; Overview → Execution → Forward → Loss → Backward → Optimizer → Source; Search → Reveal in Graph → Inspector → Source.
- [ ] Run:

    python -m ruff check src tests
    python -m mypy
    python -B -m unittest discover -s tests -v
    npm --prefix frontend test
    npm --prefix frontend run lint
    npm --prefix frontend run build
    scripts/build-desktop.ps1 -CheckOnly

- [ ] Record WebView2/PyInstaller platform limits and keep runtime tracing, tensor execution, full autograd graphs, distributed training, and source editing out of scope.
- [ ] Review the diff to ensure no project source is copied into cache, no UI path executes user code, no remote imports remain, and Tkinter remains available until desktop acceptance passes.
