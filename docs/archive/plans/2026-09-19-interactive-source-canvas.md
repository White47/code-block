# Interactive Source Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing Tk graph view into a source-linked, zoomable vertical canvas whose nodes can open up to three movable local-detail islands without replacing the main graph.

**Architecture:** Keep Code Graph, Semantic Graph, View Builder, and `VisibleGraph` unchanged as analysis boundaries. Add a pure UI `CanvasScene` projection for geometry, routing, viewport transforms, and detail-island placement; keep Tk event handling in `GraphCanvas`, graph queries and persistence in `AppController`, and tab/Inspector coordination in `CodeVizApp`.

**Tech Stack:** Python 3.11+ standard library, Tkinter/ttk/Canvas, frozen dataclasses, existing JSON View State store, `unittest`, Ruff, mypy, wheel build.

---

## File map

- Create `src/codeviz/ui/canvas_scene.py`: UI-neutral scene types, coordinate transforms, edge routing, island placement, and scene composition.
- Modify `src/codeviz/views/layout.py`: deterministic layered vertical placement with stable sibling ordering and branch columns.
- Modify `src/codeviz/views/state.py`: validated per-view viewport and detail-island persistence records.
- Modify `src/codeviz/views/__init__.py`: export the new state records.
- Modify `src/codeviz/ui/controller.py`: bounded detail queries plus viewport/island state commands.
- Modify `src/codeviz/ui/graph_canvas.py`: Tk rendering, hit targets, zoom/pan, node/island drag, and island close/open controls.
- Modify `src/codeviz/ui/tkapp.py`: connect detail queries, Inspector context, source navigation, toolbar controls, status messages, and reset behavior.
- Modify `src/codeviz/ui/i18n.py`: Chinese labels for the new visible controls and messages.
- Modify `tests/test_ui_graph.py`: pure scene and live-Tk input tests.
- Modify `tests/test_views.py`: layered-layout branch, merge, cycle, and stable-order tests.
- Modify `tests/test_persistence_store.py`: View State round-trip and malformed-state coverage.
- Modify `tests/test_ui_controller.py`: bounded detail query and state-command tests.
- Modify `tests/test_ui_tkapp.py`: integrated A+B interaction, Inspector, source, limit, and restore tests.
- Modify `docs/UX_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/CACHE_SPEC.md`, `docs/KNOWN_LIMITATIONS.md`, `docs/ROADMAP.md`: align normative documentation after behavior exists.

## Task 1: Extract a pure canvas-scene model and viewport math

**Files:**
- Create: `src/codeviz/ui/canvas_scene.py`
- Modify: `src/codeviz/ui/graph_canvas.py`
- Test: `tests/test_ui_graph.py`

- [ ] **Step 1: Write failing tests for reversible transforms and pointer-anchored zoom**

Add imports and tests in `tests/test_ui_graph.py`:

```python
from codeviz.ui.canvas_scene import Point, Viewport, scene_to_screen, screen_to_scene, zoom_about

def test_viewport_transform_round_trips_and_zoom_keeps_pointer_anchor(self):
    viewport = Viewport(zoom=1.5, pan=Point(20.0, -10.0))
    scene_point = Point(80.0, 50.0)
    screen_point = scene_to_screen(scene_point, viewport)
    self.assertEqual(screen_to_scene(screen_point, viewport), scene_point)
    changed = zoom_about(viewport, screen_point, 2.0)
    self.assertEqual(scene_to_screen(scene_point, changed), screen_point)
    self.assertEqual(changed.zoom, 2.0)

def test_zoom_is_clamped_to_supported_range(self):
    viewport = Viewport()
    self.assertEqual(zoom_about(viewport, Point(0.0, 0.0), 0.01).zoom, 0.25)
    self.assertEqual(zoom_about(viewport, Point(0.0, 0.0), 9.0).zoom, 2.5)
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `python -B -m unittest tests.test_ui_graph.GraphSceneTests.test_viewport_transform_round_trips_and_zoom_keeps_pointer_anchor tests.test_ui_graph.GraphSceneTests.test_zoom_is_clamped_to_supported_range -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'codeviz.ui.canvas_scene'`.

- [ ] **Step 3: Add immutable geometry and viewport transforms**

Create `src/codeviz/ui/canvas_scene.py` with these public types and functions:

```python
from dataclasses import dataclass

MIN_ZOOM = 0.25
MAX_ZOOM = 2.5

@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def intersects(self, other: "Rect", margin: float = 0.0) -> bool:
        return not (self.x + self.width + margin <= other.x or
                    other.x + other.width + margin <= self.x or
                    self.y + self.height + margin <= other.y or
                    other.y + other.height + margin <= self.y)

@dataclass(frozen=True, slots=True)
class Viewport:
    zoom: float = 1.0
    pan: Point = Point(0.0, 0.0)

def scene_to_screen(point: Point, viewport: Viewport) -> Point:
    return Point(point.x * viewport.zoom + viewport.pan.x,
                 point.y * viewport.zoom + viewport.pan.y)

def screen_to_scene(point: Point, viewport: Viewport) -> Point:
    return Point((point.x - viewport.pan.x) / viewport.zoom,
                 (point.y - viewport.pan.y) / viewport.zoom)

def zoom_about(viewport: Viewport, screen_anchor: Point, requested_zoom: float) -> Viewport:
    scene_anchor = screen_to_scene(screen_anchor, viewport)
    zoom = min(MAX_ZOOM, max(MIN_ZOOM, requested_zoom))
    return Viewport(zoom, Point(screen_anchor.x - scene_anchor.x * zoom,
                                screen_anchor.y - scene_anchor.y * zoom))
```

- [ ] **Step 4: Move `SceneNode`, `SceneEdge`, `GraphScene`, `EdgePaint`, `edge_paint`, and `graph_scene` into `canvas_scene.py` without behavior changes**

Preserve their existing fields and imports, then re-export them from `graph_canvas.py` so existing imports continue working:

```python
from .canvas_scene import EdgePaint, GraphScene, SceneEdge, SceneNode, edge_paint, graph_scene

__all__ = ["GraphCanvas", "EdgePaint", "GraphScene", "SceneEdge", "SceneNode",
           "edge_paint", "graph_scene"]
```

- [ ] **Step 5: Run scene tests and static checks**

Run: `python -B -m unittest tests.test_ui_graph -v`

Expected: all `test_ui_graph` tests pass; live Tk tests may skip only when no display is available.

Run: `python -m ruff check src/codeviz/ui/canvas_scene.py src/codeviz/ui/graph_canvas.py tests/test_ui_graph.py && python -m mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit the scene extraction**

```bash
git add src/codeviz/ui/canvas_scene.py src/codeviz/ui/graph_canvas.py tests/test_ui_graph.py
git commit -m "Add canvas scene geometry and viewport transforms"
```

## Task 2: Replace the single column with deterministic layered vertical layout

**Files:**
- Modify: `src/codeviz/views/layout.py`
- Test: `tests/test_views.py`

- [ ] **Step 1: Write failing branch, merge, and stability tests**

Add tests that build `root → left/right → merge`, call `layout_positions` twice, and assert:

```python
first = layout_positions(nodes, edges, ("root",), "flowchart", ViewState())
second = layout_positions(nodes, tuple(reversed(edges)), ("root",), "flowchart", ViewState())
self.assertEqual(first, second)
self.assertEqual(first["root"][1], 0.0)
self.assertEqual(first["left"][1], first["right"][1])
self.assertNotEqual(first["left"][0], first["right"][0])
self.assertGreater(first["merge"][1], first["left"][1])
self.assertAlmostEqual(first["merge"][0],
                       (first["left"][0] + first["right"][0]) / 2)
```

Add a cycle test asserting every node is placed exactly once and a manual-position test asserting `ViewState.positions` still wins.

- [ ] **Step 2: Run the new layout tests and verify the branch-column failure**

Run: `python -B -m unittest tests.test_views -v`

Expected: the new test fails because sibling nodes currently share `x == 0.0`.

- [ ] **Step 3: Implement stable rank assignment and centered columns**

Refactor `layout_positions` around these helpers:

```python
_X_GAP = 220.0
_Y_GAP = 112.0

def _forward_edges(edges: tuple[VisibleEdge, ...]) -> tuple[VisibleEdge, ...]:
    allowed = {EdgeType.CONTAINS, EdgeType.CONTROL_FLOW,
               EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE}
    return tuple(edge for edge in edges if edge.type in allowed and edge.source != edge.target)

def _ranks(node_ids: tuple[str, ...], edges: tuple[VisibleEdge, ...],
           roots: tuple[str, ...]) -> dict[str, int]:
    rank = {node_id: 0 for node_id in roots}
    for _ in range(len(node_ids)):
        changed = False
        for edge in edges:
            candidate = rank.get(edge.source, 0) + 1
            if candidate > rank.get(edge.target, 0) and candidate < len(node_ids):
                rank[edge.target] = candidate
                changed = True
        if not changed:
            break
    return {node_id: min(rank.get(node_id, 0), len(node_ids) - 1)
            for node_id in node_ids}
```

Group nodes by rank, sort each rank by stable predecessor barycenter then node ID, and assign columns centered around `x == 0`. Ignore `LOOP_BACK` for rank calculation. Apply `state.positions` last exactly as today.

- [ ] **Step 4: Run layout and graph-scene regression tests**

Run: `python -B -m unittest tests.test_views tests.test_ui_graph -v`

Expected: all tests pass, including cycles and manual overrides.

- [ ] **Step 5: Commit the layered layout**

```bash
git add src/codeviz/views/layout.py tests/test_views.py
git commit -m "Add deterministic layered vertical graph layout"
```

## Task 3: Add detail-island geometry and obstacle-aware routing

**Files:**
- Modify: `src/codeviz/ui/canvas_scene.py`
- Test: `tests/test_ui_graph.py`

- [ ] **Step 1: Write failing tests for empty-space placement and routed edges**

Add tests using explicit rectangles:

```python
from codeviz.ui.canvas_scene import place_detail_island, route_edge

def test_detail_island_prefers_right_then_avoids_occupied_rectangles(self):
    source = Rect(100.0, 100.0, 170.0, 66.0)
    occupied = (source, Rect(310.0, 70.0, 340.0, 300.0))
    placed = place_detail_island(source, (340.0, 300.0), occupied)
    self.assertFalse(any(placed.intersects(item, margin=24.0) for item in occupied))
    self.assertLess(placed.x, source.x)

def test_routed_edge_does_not_cross_blocking_node(self):
    source = Rect(20.0, 20.0, 100.0, 60.0)
    target = Rect(20.0, 240.0, 100.0, 60.0)
    blocker = Rect(0.0, 130.0, 140.0, 50.0)
    points = route_edge(source, target, (blocker,))
    self.assertGreater(max(point.x for point in points), blocker.x + blocker.width)
```

- [ ] **Step 2: Run the focused tests and verify the missing-function failures**

Run: `python -B -m unittest tests.test_ui_graph -v`

Expected: FAIL because `place_detail_island` and `route_edge` do not exist.

- [ ] **Step 3: Add detail-island scene types and deterministic placement**

Add these records and a candidate search to `canvas_scene.py`:

```python
@dataclass(frozen=True, slots=True)
class DetailIslandScene:
    id: str
    source_node_id: str
    title: str
    bounds: Rect
    graph: GraphScene

def place_detail_island(source: Rect, size: tuple[float, float],
                        occupied: tuple[Rect, ...], margin: float = 36.0) -> Rect:
    width, height = size
    candidates = (
        Rect(source.x + source.width + margin, source.y, width, height),
        Rect(source.x - width - margin, source.y, width, height),
        Rect(source.x, source.y + source.height + margin, width, height),
        Rect(source.x, source.y - height - margin, width, height),
    )
    for candidate in candidates:
        if not any(candidate.intersects(item, margin=24.0) for item in occupied):
            return candidate
    bottom = max((item.y + item.height for item in occupied), default=source.y)
    return Rect(source.x, bottom + margin, width, height)
```

- [ ] **Step 4: Add orthogonal routing with a safe fallback**

Implement `route_edge(source, target, obstacles)` to try center-bottom → center-top, then right and left doglegs outside obstacle bounds. Return the first path whose axis-aligned segments do not intersect an obstacle expanded by 12 pixels; if every candidate is blocked, return a direct two-point path. Use the same function for normal edges and a dashed source-to-island connector.

- [ ] **Step 5: Run scene tests and commit**

Run: `python -B -m unittest tests.test_ui_graph -v`

Expected: all scene tests pass.

```bash
git add src/codeviz/ui/canvas_scene.py tests/test_ui_graph.py
git commit -m "Add detail island placement and edge routing"
```

## Task 4: Persist per-view viewport and detail-island state safely

**Files:**
- Modify: `src/codeviz/views/state.py`
- Modify: `src/codeviz/views/__init__.py`
- Modify: `src/codeviz/ui/controller.py`
- Test: `tests/test_persistence_store.py`
- Test: `tests/test_ui_controller.py`

- [ ] **Step 1: Write failing View State round-trip and validation tests**

Extend the persistence round-trip with:

```python
viewport_by_view={"Architecture": ViewportState(1.4, (25.0, -10.0))},
detail_islands=(DetailIslandState("Architecture:model-a", ViewKind.ARCHITECTURE,
                                  "model-a", (620.0, 180.0)),),
```

Assert exact equality after `save_view_state` / `load_view_state`. Add rejection tests for zoom outside `0.25..2.5`, non-finite island coordinates, duplicate island IDs, unknown view names, and more than three islands for the same view.

- [ ] **Step 2: Run persistence tests and verify constructor/import failures**

Run: `python -B -m unittest tests.test_persistence_store -v`

Expected: FAIL because `ViewportState` and `DetailIslandState` do not exist.

- [ ] **Step 3: Add validated state records and backward-compatible JSON fields**

Add to `state.py`:

```python
@dataclass(frozen=True, slots=True)
class ViewportState:
    zoom: float = 1.0
    pan: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        if (isinstance(self.zoom, bool) or not isinstance(self.zoom, (int, float))
                or not isfinite(self.zoom) or not 0.25 <= self.zoom <= 2.5):
            raise ValueError("viewport zoom must be between 0.25 and 2.5")
        if (len(self.pan) != 2 or any(isinstance(value, bool) or
                not isinstance(value, (int, float)) or not isfinite(value)
                for value in self.pan)):
            raise ValueError("viewport pan requires finite x/y values")

@dataclass(frozen=True, slots=True)
class DetailIslandState:
    id: str
    view_kind: ViewKind
    source_node_id: str
    position: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 4000 or not self.source_node_id:
            raise ValueError("detail island IDs must be nonempty and bounded")
        if self.position is not None and (len(self.position) != 2 or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) for value in self.position)):
            raise ValueError("detail island position requires finite x/y values")
```

Import `isfinite` from `math`. Add `viewport_by_view: dict[str, ViewportState]` and `detail_islands: tuple[DetailIslandState, ...]` to `ViewState`. In `ViewState.__post_init__`, reject unknown view keys, duplicate island IDs, and more than three islands for one `ViewKind`. Serialize the records as plain JSON objects/lists in `to_dict`; in `from_dict`, default both to empty when loading older state. Retain legacy `zoom` and `pan` fields for old cache compatibility during this feature, but write active values into `viewport_by_view`.

- [ ] **Step 4: Add controller commands that copy and persist state**

Add exact APIs to `AppController`:

```python
def save_viewport(self, kind: ViewKind, zoom: float,
                  pan: tuple[float, float]) -> None:
    viewports = {**self.view_state.viewport_by_view,
                 kind.value: ViewportState(zoom, pan)}
    self.view_state = replace(self.view_state, viewport_by_view=viewports,
                              positions=dict(self.view_state.positions))
    self._persist_view_state()

def open_detail_island(self, kind: ViewKind, source_node_id: str,
                       position: tuple[float, float]) -> DetailIslandState:
    island_id = f"{kind.value}:{source_node_id}"
    existing = next((item for item in self.view_state.detail_islands
                     if item.id == island_id), None)
    if existing is not None:
        return existing
    if len(self.detail_islands(kind)) >= 3:
        raise ValueError("close a detail island before opening another")
    island = DetailIslandState(island_id, kind, source_node_id, position)
    self.view_state = replace(
        self.view_state,
        detail_islands=(*self.view_state.detail_islands, island),
        positions=dict(self.view_state.positions),
        viewport_by_view=dict(self.view_state.viewport_by_view),
    )
    self._persist_view_state()
    return island

def move_detail_island(self, island_id: str,
                       position: tuple[float, float]) -> None:
    if not any(item.id == island_id for item in self.view_state.detail_islands):
        raise ValueError("detail island is not open")
    islands = tuple(replace(item, position=position) if item.id == island_id else item
                    for item in self.view_state.detail_islands)
    self.view_state = replace(self.view_state, detail_islands=islands,
                              positions=dict(self.view_state.positions),
                              viewport_by_view=dict(self.view_state.viewport_by_view))
    self._persist_view_state()

def close_detail_island(self, island_id: str) -> None:
    islands = tuple(item for item in self.view_state.detail_islands
                    if item.id != island_id)
    self.view_state = replace(self.view_state, detail_islands=islands,
                              positions=dict(self.view_state.positions),
                              viewport_by_view=dict(self.view_state.viewport_by_view))
    self._persist_view_state()

def detail_islands(self, kind: ViewKind) -> tuple[DetailIslandState, ...]:
    return tuple(item for item in self.view_state.detail_islands
                 if item.view_kind == kind)
```

Import `ViewportState` and `DetailIslandState` from `codeviz.views`. Use the existing `_persist_view_state()` helper after each mutation.

- [ ] **Step 5: Test controller commands and reset semantics**

Add a controller test opening three islands, rejecting the fourth, moving and closing one, saving a viewport, and asserting `reset_positions()` clears node positions and changes each open island position to `None` while preserving analysis, tabs, roots, and viewport zoom. Implement the reset with:

```python
def reset_positions(self) -> None:
    islands = tuple(replace(item, position=None)
                    for item in self.view_state.detail_islands)
    self.view_state = replace(self.view_state, positions={}, detail_islands=islands,
                              viewport_by_view=dict(self.view_state.viewport_by_view))
    self._persist_view_state()
```

Run: `python -B -m unittest tests.test_persistence_store tests.test_ui_controller -v`

Expected: all tests pass.

- [ ] **Step 6: Commit persisted canvas state**

```bash
git add src/codeviz/views/state.py src/codeviz/views/__init__.py src/codeviz/ui/controller.py tests/test_persistence_store.py tests/test_ui_controller.py
git commit -m "Persist canvas viewport and detail islands"
```

## Task 5: Build bounded, source-backed detail graphs

**Files:**
- Modify: `src/codeviz/ui/controller.py`
- Test: `tests/test_ui_controller.py`

- [ ] **Step 1: Write failing bounded-detail tests with the MVP fixture**

Open and analyze `tests/fixtures/mvp_project`, choose a visible Architecture node with children, then assert:

```python
detail = controller.detail_view(ViewKind.ARCHITECTURE, root_id, node.id)
self.assertLessEqual(len(detail.nodes), 18)
self.assertLessEqual(len(detail.edges), 40)
self.assertTrue(any(item.id == node.id for item in detail.nodes))
self.assertTrue(all(item.source_anchors or item.member_ids for item in detail.nodes))
self.assertEqual(controller.analysis, original_analysis)
```

Add an invalid source-node test and a Symbol/Data Flow detail query test.

- [ ] **Step 2: Run the controller tests and verify the missing method failure**

Run: `python -B -m unittest tests.test_ui_controller -v`

Expected: FAIL with `AttributeError: 'AppController' object has no attribute 'detail_view'`.

- [ ] **Step 3: Implement an independent local query budget**

Add:

```python
def detail_view(self, kind: ViewKind, root_id: str | None,
                source_node_id: str) -> VisibleGraph:
    if self.analysis is None:
        raise RuntimeError("analyze the project before opening node details")
    request = ViewRequest(kind, root_id=root_id, selected_id=source_node_id,
                          expanded_ids=(source_node_id,), hops=1,
                          max_nodes=18, max_edges=40)
    detail_state = ViewState(expanded_ids=(source_node_id,))
    return build_visible_graph(self.analysis, request, detail_state)
```

Do not pass the main view's expanded IDs or positions into this query. Validate that the result contains `source_node_id`; otherwise raise `ValueError("node has no local detail graph")`.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -B -m unittest tests.test_ui_controller tests.test_views -v`

Expected: all tests pass and no analysis facts change.

```bash
git add src/codeviz/ui/controller.py tests/test_ui_controller.py
git commit -m "Add bounded source-backed detail graph queries"
```

## Task 6: Render and manipulate the interactive Tk canvas

**Files:**
- Modify: `src/codeviz/ui/canvas_scene.py`
- Modify: `src/codeviz/ui/graph_canvas.py`
- Test: `tests/test_ui_graph.py`

- [ ] **Step 1: Write live-Tk tests for input separation**

Construct `GraphCanvas` with recording callbacks and assert:

```python
graph._on_zoom(SimpleNamespace(x=200, y=150, delta=120, num=0))
self.assertGreater(graph.viewport.zoom, 1.0)
graph._pan_start(SimpleNamespace(x=100, y=100))
graph._pan_move(SimpleNamespace(x=140, y=125))
self.assertNotEqual(graph.viewport.pan, Point(0.0, 0.0))
graph._double_click(SimpleNamespace(), node_id="model")
self.assertEqual(opened, ["model"])
```

Add assertions that clicking a `+` hit target opens detail, clicking node body only selects, dragging a node does not pan, dragging an island reports its scene position, close invokes only the close callback, and a fourth island is never rendered when the controller rejects it.

- [ ] **Step 2: Run graph tests and verify missing handlers/callbacks**

Run: `python -B -m unittest tests.test_ui_graph -v`

Expected: FAIL because zoom, pan, and island callbacks are absent.

- [ ] **Step 3: Expand `GraphCanvas`'s constructor and retained state**

Use this callback contract:

```python
def __init__(self, parent: Any, *,
             on_select: Callable[[str, str | None], None],
             on_open_detail: Callable[[str], None],
             on_move: Callable[[str, tuple[float, float]], None],
             on_move_island: Callable[[str, tuple[float, float]], None],
             on_close_island: Callable[[str], None],
             on_open_source: Callable[[str, str | None], None],
             on_viewport: Callable[[float, tuple[float, float]], None],
             translate: Callable[[str], str] | None = None) -> None:
```

Retain `self.viewport`, `self._main_view`, `self._detail_views`, `self._detail_states`, drag mode, and selected context ID. Add `detail_view(island_id)` for Inspector lookup.

- [ ] **Step 4: Render one scene in transformed screen coordinates**

Change `render` to accept:

```python
def render(self, view: VisibleGraph, selected_id: str | None = None, *,
           viewport: Viewport = Viewport(),
           details: tuple[tuple[DetailIslandState, VisibleGraph], ...] = ()) -> None:
```

Build main and detail `GraphScene` values in scene coordinates, place islands from persisted positions or `place_detail_island`, then create Tk items after `scene_to_screen`. Tag items with `node:<id>`, `context:<island-id|main>`, `island:<id>`, `expand:<id>`, and `close:<id>`.

Add `suggest_island_position(source_node_id) -> tuple[float, float]`: read the source node rectangle from the retained main scene, collect main-node and current-island rectangles, call `place_detail_island`, and return the candidate's scene `(x, y)`. Raise `ValueError("node is not visible on the canvas")` when the source node is absent.

- [ ] **Step 5: Add zoom, pan, fit, 100%, and drag handlers**

Bind Ctrl+wheel and toolbar callbacks to zoom; ordinary wheel keeps vertical scrolling for accessibility. Use middle-button drag or left-drag on blank canvas for pan. At the end of pan/zoom call `on_viewport`; at the end of a node/island drag convert screen delta by `1 / zoom` before persisting. Provide public `fit_to_window()` and `reset_zoom()` methods. Bind Escape to close `self._selected_context` through `on_close_island` when a detail island is active; otherwise leave the main selection unchanged.

- [ ] **Step 6: Render relation semantics and detail controls**

Use `route_edge` for main edges and dashed connectors. Keep `edge_paint` styles, add `gradient-reverse` when present, and show a small `+` only when `metadata["has_children"]`, `member_ids`, or query context makes local detail available. Draw island title, `×`, an `Open Source` action, source name, and inner graph; the source action calls `on_open_source(node_id, island_id)`. Island nodes do not receive an expand tag. Include text or dash markers as well as color for selection, confidence, and relation types.

- [ ] **Step 7: Run graph tests and static checks**

Run: `python -B -m unittest tests.test_ui_graph -v`

Expected: all tests pass; display skips remain platform-dependent.

Run: `python -m ruff check src/codeviz/ui/canvas_scene.py src/codeviz/ui/graph_canvas.py tests/test_ui_graph.py && python -m mypy`

Expected: both commands exit 0.

- [ ] **Step 8: Commit the interactive canvas adapter**

```bash
git add src/codeviz/ui/canvas_scene.py src/codeviz/ui/graph_canvas.py tests/test_ui_graph.py
git commit -m "Add zoomable canvas and movable detail islands"
```

## Task 7: Integrate detail islands with tabs, Inspector, source navigation, and localization

**Files:**
- Modify: `src/codeviz/ui/tkapp.py`
- Modify: `src/codeviz/ui/i18n.py`
- Test: `tests/test_ui_tkapp.py`

- [ ] **Step 1: Write an end-to-end Tk test for the A+B interaction**

Using the existing MVP fixture and live-Tk setup, open Architecture and assert this sequence:

```python
app._on_select(expandable_id, None)
self.assertEqual(app._selected_id, expandable_id)
self.assertEqual(app.controller.detail_islands(ViewKind.ARCHITECTURE), ())
app._open_detail_island(expandable_id)
self.assertEqual(len(app.controller.detail_islands(ViewKind.ARCHITECTURE)), 1)
island_id = app.controller.detail_islands(ViewKind.ARCHITECTURE)[0].id
app._on_select(detail_node_id, island_id)
app._open_selected_source(False)
self.assertTrue(app._source_tabs)
app._close_detail_island(island_id)
self.assertEqual(app.controller.detail_islands(ViewKind.ARCHITECTURE), ())
```

Add cases for three-island limit messaging, failed detail query leaving the main graph intact, Outdated warning remaining visible, and restored islands after reopening the project.

- [ ] **Step 2: Run Tk app tests and verify callback/signature failures**

Run: `python -B -m unittest tests.test_ui_tkapp -v`

Expected: FAIL until `CodeVizApp` uses the new `GraphCanvas` contract.

- [ ] **Step 3: Add app-level detail context and graph refresh helpers**

Track `_selected_context: str | None`. Implement:

```python
def _detail_pairs(self, kind: ViewKind) -> tuple[tuple[DetailIslandState, VisibleGraph], ...]:
    pairs = []
    for state in self.controller.detail_islands(kind):
        pairs.append((state, self.controller.detail_view(
            kind, self._roots.get(kind), state.source_node_id)))
    return tuple(pairs)

def _render_active_graph(self, kind: ViewKind) -> None:
    _, graph = self._graph_tabs[kind]
    saved = self.controller.view_state.viewport_by_view.get(kind.value, ViewportState())
    graph.render(self._views[kind], self._selected_id,
                 viewport=Viewport(saved.zoom, Point(*saved.pan)),
                 details=self._detail_pairs(kind))
```

Catch per-island `ValueError` and render/report only that island failure; do not discard `_views[kind]`.

- [ ] **Step 4: Replace drill-down behavior with detail-island opening**

Keep aggregate-member handling. For normal nodes, `_open_detail_island` computes a scene position through `GraphCanvas.suggest_island_position`, calls `controller.open_detail_island`, and refreshes the current canvas. Do not change the main root or main graph expansion history. Enter and double-click call this method.

- [ ] **Step 5: Make Inspector and source actions context-aware**

Change `_on_select(node_id, context_id)` to choose the main `VisibleGraph` when `context_id is None`, otherwise `graph.detail_view(context_id)`. Store that graph as `_inspection_view`; pass it to `build_inspector` and `_open_selected_source`. Add `_open_canvas_source(node_id, context_id)` that calls `_on_select` and then `_open_selected_source(False)`. Closing an island whose node is selected returns selection to the island's source node in the main graph. Escape closes the active island through the same close path.

- [ ] **Step 6: Add toolbar and localized messages**

Add `Fit`, `100%`, and zoom percent controls beside Reset Project Layout. Add translations for `Fit`, `Detail`, `Close a detail view before opening another`, `No local details available`, `Zoom {percent}%`, and the revised hint `Single-click inspects · double-click or + opens details · source opens separately`.

- [ ] **Step 7: Run integrated UI tests and commit**

Run: `python -B -m unittest tests.test_ui_tkapp tests.test_ui_preferences tests.test_ui_graph -v`

Expected: all tests pass with only the existing no-display/symlink platform skips.

```bash
git add src/codeviz/ui/tkapp.py src/codeviz/ui/i18n.py tests/test_ui_tkapp.py
git commit -m "Connect detail islands to inspector and source navigation"
```

## Task 8: Harden restore, failure isolation, and acceptance paths

**Files:**
- Modify: `tests/test_mvp_acceptance.py`
- Modify: `tests/test_persistence_controller.py`
- Modify: `tests/test_ui_tkapp.py`
- Modify: code files only where a new failing test exposes a defect.

- [ ] **Step 1: Add acceptance tests for all three golden paths with canvas state present**

For Architecture, Training Flow, and Search Reveal, save non-default viewport and island state, reopen through `CacheStore`, and assert the selected Stable ID still reaches the same `SourceAnchor`. Include a deleted-node case asserting the stale island is ignored while the project and main graph still open.

- [ ] **Step 2: Add malformed and oversized state tests**

Write cache JSON with four islands for one view, non-finite zoom, a 10,000-character island ID, and a missing source node. Assert load either rejects the bounded state with a `View state:` cache issue or drops invalid references during restore; analysis remains available.

- [ ] **Step 3: Run the acceptance and persistence suites**

Run: `python -B -m unittest tests.test_mvp_acceptance tests.test_persistence_controller tests.test_persistence_store tests.test_ui_tkapp -v`

Expected: all tests pass; invalid view state never removes a valid analysis cache.

- [ ] **Step 4: Run the full test suite and fix only demonstrated regressions**

Run: `python -B -m unittest discover -s tests -v`

Expected: all tests pass; only documented Windows symlink or unavailable-display skips remain.

- [ ] **Step 5: Commit hardening tests and fixes**

```bash
git add tests/test_mvp_acceptance.py tests/test_persistence_controller.py tests/test_persistence_store.py tests/test_ui_tkapp.py src/codeviz
git commit -m "Harden interactive canvas restore and acceptance paths"
```

## Task 9: Align documentation and pass release-quality gates

**Files:**
- Modify: `docs/UX_SPEC.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CACHE_SPEC.md`
- Modify: `docs/KNOWN_LIMITATIONS.md`
- Modify: `docs/ROADMAP.md`
- Create: `docs/INTERACTIVE_CANVAS_REPORT.md`

- [ ] **Step 1: Update normative documentation with implemented behavior**

Document the A+B interaction, Canvas Scene boundary, three-island limit, per-view viewport persistence, failure isolation, draw.io non-dependency, and `.drawio` export as future scope. Remove the obsolete statement that zoom/pan has no UI operation.

- [ ] **Step 2: Write the implementation report**

Create `docs/INTERACTIVE_CANVAS_REPORT.md` with changed files, user-visible behavior, test counts, platform skips, remaining limitations, and the exact manual trial sequence:

```text
Open Project → Analyze → Architecture → single-click node → double-click/+ →
move detail island → select island node → Open Source → close island → reopen project
```

- [ ] **Step 3: Run all repository quality gates**

Run: `python -m ruff check src tests`

Expected: exit 0.

Run: `python -m mypy`

Expected: exit 0.

Run: `python -B -m unittest discover -s tests -v`

Expected: all tests pass with only documented platform skips.

Run: `python -m pip wheel . --no-deps -w dist`

Expected: one wheel is built successfully in `dist`.

- [ ] **Step 4: Perform the desktop visual trial**

Run: `python -m codeviz`

Expected: the main flow is vertical; single-click only inspects; double-click or `+` opens a connected island in free space; three islands can be moved and closed; the fourth is rejected with a localized message; Fit/100% work; both main and island nodes open the correct source lines.

- [ ] **Step 5: Commit documentation and report**

```bash
git add docs/UX_SPEC.md docs/ARCHITECTURE.md docs/CACHE_SPEC.md docs/KNOWN_LIMITATIONS.md docs/ROADMAP.md docs/INTERACTIVE_CANVAS_REPORT.md
git commit -m "Document interactive source canvas delivery"
```

## Task 10: Independent review and final branch verification

**Files:**
- Modify only files required by concrete review findings.

- [ ] **Step 1: Review the complete branch diff against the approved design**

Run: `git diff main...HEAD --check && git diff --stat main...HEAD`

Expected: no whitespace errors; the diff is limited to the documented canvas feature, tests, and docs.

- [ ] **Step 2: Verify security and scope boundaries explicitly**

Confirm by code inspection and tests that no target project code is executed, no WebView/draw.io/network dependency was added, source access still uses `read_source`, Full Graph is never passed directly to Tk, and View State is stored outside the analyzed project.

- [ ] **Step 3: Re-run final gates after review fixes**

Run: `python -m ruff check src tests && python -m mypy && python -B -m unittest discover -s tests -v && python -m pip wheel . --no-deps -w dist`

Expected: every command exits 0 with only documented skips in unittest output.

- [ ] **Step 4: Commit review fixes separately when needed**

```bash
git add src tests docs
git commit -m "Address interactive canvas review findings"
```

If review finds no defects, do not create an empty commit.
