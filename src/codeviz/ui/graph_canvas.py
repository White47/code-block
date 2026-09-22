"""Tk adapter for a bounded, source-linked interactive graph canvas."""

from __future__ import annotations

from functools import partial
from inspect import signature
from typing import Any, Callable

from codeviz.core import Confidence, EdgeType, ViewKind, VisibleGraph
from codeviz.views import DetailIslandState

from . import theme
from .canvas_scene import (
    DetailIslandScene,
    EdgePaint,
    GraphScene,
    Point,
    Rect,
    SceneEdge,
    SceneNode,
    Viewport,
    edge_paint,
    graph_scene,
    place_detail_island,
    route_edge,
    scene_to_screen,
    screen_to_scene,
    zoom_about,
)

__all__ = ["GraphCanvas", "EdgePaint", "GraphScene", "SceneEdge", "SceneNode",
           "edge_paint", "graph_scene"]

_DETAIL_MIN_WIDTH = 440.0
_DETAIL_MIN_HEIGHT = 300.0
_DETAIL_HEADER_HEIGHT = 64.0
_SCENE_MARGIN = 24.0
_DEFAULT_VIEWPORT = Viewport()


def _noop(*_args: object) -> None:
    pass


class GraphCanvas:
    """Retained Tk canvas adapter for one main graph and local detail islands."""

    def __init__(
        self,
        parent: Any,
        *,
        on_select: Callable[..., None],
        on_drill_down: Callable[[str], None] | None = None,
        on_move: Callable[[str, tuple[float, float]], None] = _noop,
        on_open_detail: Callable[[str], None] | None = None,
        on_move_island: Callable[[str, tuple[float, float]], None] | None = None,
        on_close_island: Callable[[str], None] | None = None,
        on_open_source: Callable[[str, str | None], None] | None = None,
        on_viewport: Callable[[float, tuple[float, float]], None] | None = None,
        translate: Callable[[str], str] | None = None,
    ) -> None:
        import tkinter as tk

        self.widget = tk.Frame(parent, bg=theme.BACKGROUND)
        self.canvas = tk.Canvas(self.widget, bg=theme.BACKGROUND, highlightthickness=0)
        horizontal = tk.Scrollbar(self.widget, orient="horizontal", command=self.canvas.xview)
        vertical = tk.Scrollbar(self.widget, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.widget.rowconfigure(0, weight=1)
        self.widget.columnconfigure(0, weight=1)

        self._on_select = on_select
        self._on_open_detail = on_open_detail or on_drill_down or _noop
        self._on_move = on_move
        self._on_move_island = on_move_island or _noop
        self._on_close_island = on_close_island or _noop
        self._on_open_source = on_open_source or _noop
        self._on_viewport = on_viewport or _noop
        self._translate = translate or (lambda phrase: phrase)

        self.viewport = Viewport()
        self._main_view: VisibleGraph | None = None
        self._main_scene = GraphScene((), (), 0.0, 0.0)
        self._detail_views: dict[str, VisibleGraph] = {}
        self._detail_states: dict[str, DetailIslandState] = {}
        self._detail_scenes: dict[str, DetailIslandScene] = {}
        self._selected_id: str | None = None
        self._selected_context: str | None = None
        self._drag_mode: str | None = None
        self._drag_id: str | None = None
        self._drag_context: str | None = None
        self._drag_point: Point | None = None
        self._drag_total = Point(0.0, 0.0)

        self.canvas.bind("<Return>", self._enter)
        self.canvas.bind("<Escape>", self._escape)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_zoom)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Next>", self._on_page_key)
        self.canvas.bind("<Prior>", self._on_page_key)
        self.canvas.bind("<ButtonPress-1>", self._blank_pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._release)
        self.canvas.bind("<Configure>", self._on_resize)

    @property
    def main_view(self) -> VisibleGraph | None:
        return self._main_view

    @property
    def main_scene(self) -> GraphScene:
        return self._main_scene

    @property
    def detail_scenes(self) -> tuple[DetailIslandScene, ...]:
        return tuple(self._detail_scenes.values())

    @property
    def selected_context(self) -> str | None:
        return self._selected_context

    @property
    def drag_mode(self) -> str | None:
        return self._drag_mode

    def detail_view(self, island_id: str) -> VisibleGraph:
        return self._detail_views[island_id]

    def render(
        self,
        view: VisibleGraph,
        selected_id: str | None = None,
        *,
        viewport: Viewport = _DEFAULT_VIEWPORT,
        details: tuple[tuple[DetailIslandState, VisibleGraph], ...] = (),
    ) -> None:
        """Retain and render one main graph plus caller-approved detail islands."""
        self._main_view = view
        self._selected_id = selected_id
        self.viewport = viewport
        self._detail_views = {state.id: detail for state, detail in details}
        self._detail_states = {state.id: state for state, _detail in details}
        if self._selected_context not in self._detail_views:
            self._selected_context = None

        self._main_scene = graph_scene(view, selected_id, viewport_width=400.0)
        self._detail_scenes = self._compose_details(details)
        self._draw_all()

    def _compose_details(
        self, details: tuple[tuple[DetailIslandState, VisibleGraph], ...]
    ) -> dict[str, DetailIslandScene]:
        if self._main_view is None:
            return {}
        occupied = [self._node_rect(node) for node in self._main_scene.nodes]
        main_nodes = {node.id: node for node in self._main_scene.nodes}
        result: dict[str, DetailIslandScene] = {}
        for state, view in details:
            source = main_nodes.get(state.source_node_id)
            if source is None:
                continue
            selected = self._selected_id if self._selected_context == state.id else None
            local = graph_scene(view, selected, viewport_width=360.0)
            size = (max(_DETAIL_MIN_WIDTH, local.width + 40.0),
                    max(_DETAIL_MIN_HEIGHT, local.height + _DETAIL_HEADER_HEIGHT + 20.0))
            if state.position is None:
                bounds = place_detail_island(self._node_rect(source), size, tuple(occupied))
            else:
                bounds = Rect(state.position[0], state.position[1], *size)
            source_view_node = next((node for node in self._main_view.nodes
                                     if node.id == state.source_node_id), None)
            title = source_view_node.display_name if source_view_node else state.source_node_id
            island = DetailIslandScene(state.id, state.source_node_id, title, bounds, local)
            result[state.id] = island
            occupied.append(bounds)
        return result

    def _draw_all(self) -> None:
        self.canvas.delete("all")
        if self._main_view is None:
            return
        if not self._main_scene.nodes:
            message = ("No model components detected · inspect Analysis Issues"
                       if self._main_view.view_kind == ViewKind.ARCHITECTURE else
                       "No execution entry points detected · inspect Analysis Issues"
                       if self._main_view.view_kind == ViewKind.EXECUTION else
                       "No local code context available · open source instead")
            point = scene_to_screen(Point(80.0, 110.0), self.viewport)
            self.canvas.create_text(point.x, point.y, text=self._translate(message), anchor="w",
                                    fill=theme.MUTED, font=(theme.UI_FONT, 12),
                                    tags=("empty-state", "context:main"))

        for edge in self._main_scene.edges:
            self._draw_edge(edge, Point(0.0, 0.0), ("context:main",))
        for node in self._main_scene.nodes:
            self._draw_node(node, Point(0.0, 0.0), None, allow_expand=True)

        obstacles = tuple(self._node_rect(node) for node in self._main_scene.nodes) + tuple(
            island.bounds for island in self._detail_scenes.values())
        main_nodes = {node.id: node for node in self._main_scene.nodes}
        for island in self._detail_scenes.values():
            source = main_nodes.get(island.source_node_id)
            if source is None:
                continue
            connector = route_edge(self._node_rect(source), island.bounds, obstacles)
            self.canvas.create_line(self._screen_coords(connector), fill=theme.MUTED,
                                    dash=(6, 4), width=2, arrow="last",
                                    tags=("detail-connector", "context:main"))
            self._draw_island(island)

        legend = "  ·  ".join(self._translate(item.label)
                              for item in self._main_view.legend)
        legend_point = scene_to_screen(Point(12.0, 16.0), self.viewport)
        self.canvas.create_text(legend_point.x, legend_point.y, text=legend, anchor="w",
                                fill=theme.MUTED, font=(theme.UI_FONT, 9),
                                tags=("legend", "context:main"))
        bounds = self._content_bounds()
        self.canvas.configure(scrollregion=(bounds.x - _SCENE_MARGIN,
                                            bounds.y - _SCENE_MARGIN,
                                            bounds.x + bounds.width + _SCENE_MARGIN,
                                            bounds.y + bounds.height + _SCENE_MARGIN))

    def _draw_island(self, island: DetailIslandScene) -> None:
        bounds = self._screen_rect(island.bounds)
        island_tag = f"island:{island.id}"
        context_tag = f"context:{island.id}"
        hit_tag = f"island-hit:{island.id}"
        common = (island_tag, context_tag)
        self.canvas.create_rectangle(bounds.x, bounds.y, bounds.x + bounds.width,
                                     bounds.y + bounds.height, fill=theme.PANEL,
                                     outline=theme.ACCENT, width=2,
                                     tags=(*common, hit_tag))
        title_point = scene_to_screen(Point(island.bounds.x + 16.0,
                                            island.bounds.y + 22.0), self.viewport)
        self.canvas.create_text(title_point.x, title_point.y, text=island.title, anchor="w",
                                fill=theme.TEXT, font=(theme.UI_FONT, 11, "bold"),
                                tags=(*common, hit_tag))
        close_point = scene_to_screen(Point(island.bounds.x + island.bounds.width - 18.0,
                                            island.bounds.y + 20.0), self.viewport)
        close_tag = f"close:{island.id}"
        self.canvas.create_text(close_point.x, close_point.y, text="×", fill=theme.TEXT,
                                font=(theme.UI_FONT, 13, "bold"),
                                tags=(*common, close_tag))
        source_point = scene_to_screen(Point(island.bounds.x + 16.0,
                                             island.bounds.y + 46.0), self.viewport)
        source_tag = f"source:{island.source_node_id}"
        source_hit = f"source-hit:{island.id}"
        self.canvas.create_text(source_point.x, source_point.y,
                                text=self._translate("Open Source"), anchor="w",
                                fill=theme.ACCENT, font=(theme.UI_FONT, 9, "underline"),
                                tags=(*common, source_tag, source_hit))

        origin = self._detail_origin(island)
        for edge in island.graph.edges:
            self._draw_edge(edge, origin, common)
        for node in island.graph.nodes:
            self._draw_node(node, origin, island.id, allow_expand=False)

        self.canvas.tag_bind(hit_tag, "<ButtonPress-1>",
                             partial(self._island_press, island_id=island.id))
        self.canvas.tag_bind(hit_tag, "<B1-Motion>", self._drag)
        self.canvas.tag_bind(hit_tag, "<ButtonRelease-1>", self._release)
        self.canvas.tag_bind(close_tag, "<Button-1>",
                             partial(self._close_detail, island_id=island.id))
        self.canvas.tag_bind(source_hit, "<Button-1>",
                             partial(self._source_detail, island_id=island.id,
                                     source_node_id=island.source_node_id))

    def _draw_node(self, node: SceneNode, origin: Point, context_id: str | None,
                   *, allow_expand: bool) -> None:
        context = context_id or "main"
        context_tag = f"context:{context}"
        node_tag = f"node:{node.id}"
        hit_tag = f"node-hit:{context}:{node.id}"
        island_tags = (f"island:{context_id}",) if context_id is not None else ()
        tags = (node_tag, context_tag, hit_tag, *island_tags)
        rect = Rect(node.x + origin.x, node.y + origin.y, node.width, node.height)
        screen = self._screen_rect(rect)
        fill = theme.SURFACE_SELECTED if node.selected else theme.SURFACE
        outline = theme.ACCENT if node.selected or node.neighbor else theme.BORDER
        confidence_dash = ((6, 3) if node.confidence == Confidence.PROBABLE else
                           (2, 3) if node.confidence == Confidence.UNRESOLVED else ())
        self.canvas.create_rectangle(screen.x, screen.y, screen.x + screen.width,
                                     screen.y + screen.height, fill=fill, outline=outline,
                                     width=3 if node.selected else 2,
                                     dash=(5, 3) if node.member_count else confidence_dash,
                                     tags=tags)
        title = scene_to_screen(Point(rect.x + 12.0, rect.y + 20.0), self.viewport)
        subtitle = scene_to_screen(Point(rect.x + 12.0, rect.y + 47.0), self.viewport)
        self.canvas.create_text(title.x, title.y, text=node.label, anchor="w",
                                fill=theme.TEXT, width=max(screen.width - 24.0, 20.0),
                                font=(theme.UI_FONT, 11, "bold"), tags=tags)
        self.canvas.create_text(subtitle.x, subtitle.y, text=node.subtitle, anchor="w",
                                fill=theme.MUTED, width=max(screen.width - 24.0, 20.0),
                                font=(theme.CODE_FONT, 9), tags=tags)
        if node.selected:
            marker = scene_to_screen(Point(rect.x + 7.0, rect.y + 7.0), self.viewport)
            self.canvas.create_text(marker.x, marker.y, text="●", fill=theme.ACCENT,
                                    font=(theme.UI_FONT, 7, "bold"),
                                    tags=(*tags, "selection-marker"))
        self.canvas.tag_bind(hit_tag, "<ButtonPress-1>",
                             partial(self._press, node_id=node.id, context_id=context_id))
        if context_id is None:
            self.canvas.tag_bind(hit_tag, "<B1-Motion>", self._drag)
            self.canvas.tag_bind(hit_tag, "<ButtonRelease-1>", self._release)
        if allow_expand and node.expandable:
            expand_tag = f"expand:{node.id}"
            plus = scene_to_screen(Point(rect.x + rect.width - 13.0, rect.y + 13.0),
                                   self.viewport)
            self.canvas.create_text(plus.x, plus.y, text="+", fill=theme.ACCENT,
                                    font=(theme.UI_FONT, 12, "bold"),
                                    tags=(node_tag, context_tag, expand_tag))
            self.canvas.tag_bind(expand_tag, "<Button-1>",
                                 partial(self._open_detail, node_id=node.id))
            self.canvas.tag_bind(hit_tag, "<Double-Button-1>",
                                 partial(self._double_click, node_id=node.id))

    def _draw_edge(self, edge: SceneEdge, origin: Point, tags: tuple[str, ...]) -> None:
        points = tuple(Point(edge.points[index] + origin.x,
                             edge.points[index + 1] + origin.y)
                       for index in range(0, len(edge.points), 2))
        paint = edge_paint(edge.style)
        self.canvas.create_line(self._screen_coords(points), fill=paint.color,
                                dash=paint.dash or (), width=paint.width, arrow="last",
                                smooth=edge.type == EdgeType.LOOP_BACK,
                                tags=(*tags, f"relation:{edge.type.value}"))

    def suggest_island_position(self, source_node_id: str) -> tuple[float, float]:
        source = next((node for node in self._main_scene.nodes
                       if node.id == source_node_id), None)
        if source is None:
            raise ValueError("node is not visible on the canvas")
        occupied = tuple(self._node_rect(node) for node in self._main_scene.nodes) + tuple(
            island.bounds for island in self._detail_scenes.values())
        placed = place_detail_island(self._node_rect(source),
                                     (_DETAIL_MIN_WIDTH, _DETAIL_MIN_HEIGHT), occupied)
        return placed.x, placed.y

    @staticmethod
    def _node_rect(node: SceneNode) -> Rect:
        return Rect(node.x, node.y, node.width, node.height)

    def _screen_rect(self, rect: Rect) -> Rect:
        top_left = scene_to_screen(Point(rect.x, rect.y), self.viewport)
        bottom_right = scene_to_screen(Point(rect.x + rect.width, rect.y + rect.height),
                                       self.viewport)
        return Rect(top_left.x, top_left.y,
                    bottom_right.x - top_left.x, bottom_right.y - top_left.y)

    def _screen_coords(self, points: tuple[Point, ...]) -> list[float]:
        transformed = [scene_to_screen(point, self.viewport) for point in points]
        return [coordinate for point in transformed for coordinate in (point.x, point.y)]

    @staticmethod
    def _detail_origin(island: DetailIslandScene) -> Point:
        min_x = min((node.x for node in island.graph.nodes), default=0.0)
        min_y = min((node.y for node in island.graph.nodes), default=0.0)
        return Point(island.bounds.x + 20.0 - min_x,
                     island.bounds.y + _DETAIL_HEADER_HEIGHT - min_y)

    def _content_bounds(self) -> Rect:
        return self._screen_rect(self._scene_content_bounds())

    def _scene_content_bounds(self) -> Rect:
        scene_rects = [self._node_rect(node) for node in self._main_scene.nodes]
        scene_rects.extend(island.bounds for island in self._detail_scenes.values())
        if not scene_rects:
            return Rect(0.0, 0.0, 400.0, 250.0)
        min_x = min(rect.x for rect in scene_rects)
        min_y = min(rect.y for rect in scene_rects)
        max_x = max(rect.x + rect.width for rect in scene_rects)
        max_y = max(rect.y + rect.height for rect in scene_rects)
        return Rect(min_x, min_y, max_x - min_x, max_y - min_y)

    def _invoke_select(self, node_id: str, context_id: str | None) -> None:
        try:
            signature(self._on_select).bind(node_id, context_id)
        except (TypeError, ValueError):
            self._on_select(node_id)
        else:
            self._on_select(node_id, context_id)

    def _press(self, event: Any, *, node_id: str, context_id: str | None = None) -> str:
        self.canvas.focus_set()
        self._selected_id = node_id
        self._selected_context = context_id
        self._drag_mode = "node" if context_id is None else None
        self._drag_id = node_id
        self._drag_context = context_id
        self._drag_point = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self._drag_total = Point(0.0, 0.0)
        self._invoke_select(node_id, context_id)
        return "break"

    def _island_press(self, event: Any, *, island_id: str) -> str:
        self.canvas.focus_set()
        self._selected_context = island_id
        self._drag_mode = "island"
        self._drag_id = island_id
        self._drag_context = island_id
        self._drag_point = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self._drag_total = Point(0.0, 0.0)
        return "break"

    def _open_detail(self, _event: Any, *, node_id: str) -> str:
        self._on_open_detail(node_id)
        return "break"

    def _double_click(self, _event: Any, *, node_id: str) -> str:
        if any(node.id == node_id and node.expandable for node in self._main_scene.nodes):
            self._on_open_detail(node_id)
        return "break"

    def _close_detail(self, _event: Any, *, island_id: str) -> str:
        self._on_close_island(island_id)
        return "break"

    def _source_detail(self, _event: Any, *, island_id: str,
                       source_node_id: str) -> str:
        node_id = source_node_id
        if self._selected_context == island_id and self._selected_id is not None:
            detail = self._detail_views.get(island_id)
            if detail is not None and any(node.id == self._selected_id for node in detail.nodes):
                node_id = self._selected_id
        self._on_open_source(node_id, island_id)
        return "break"

    def _enter(self, event: Any) -> str:
        if self._selected_context is None and self._selected_id is not None:
            self._double_click(event, node_id=self._selected_id)
        return "break"

    def _escape(self, _event: Any) -> str:
        if self._selected_context is not None:
            self._on_close_island(self._selected_context)
        return "break"

    def _drag(self, event: Any) -> None:
        if self._drag_mode not in {"node", "island"} or self._drag_point is None:
            return
        point = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        dx, dy = point.x - self._drag_point.x, point.y - self._drag_point.y
        if self._drag_mode == "node" and self._drag_id is not None:
            self._move_context_items(f"node:{self._drag_id}", "context:main", dx, dy)
        elif self._drag_mode == "island" and self._drag_id is not None:
            self.canvas.move(f"island:{self._drag_id}", dx, dy)
        self._drag_total = Point(self._drag_total.x + dx, self._drag_total.y + dy)
        self._drag_point = point

    def _move_context_items(self, item_tag: str, context_tag: str,
                            dx: float, dy: float) -> None:
        for item in self.canvas.find_withtag(item_tag):
            if context_tag in self.canvas.gettags(item):
                self.canvas.move(item, dx, dy)

    def _release(self, _event: Any) -> None:
        moved = self._drag_total != Point(0.0, 0.0)
        scene_delta = Point(self._drag_total.x / self.viewport.zoom,
                            self._drag_total.y / self.viewport.zoom)
        if moved and self._drag_mode == "node" and self._drag_id is not None:
            old = ((0.0, 0.0) if self._main_view is None else
                   self._main_view.layout_hints.get(self._drag_id, (0.0, 0.0)))
            self._on_move(self._drag_id, (old[0] + scene_delta.x,
                                          old[1] + scene_delta.y))
        elif moved and self._drag_mode == "island" and self._drag_id is not None:
            island = self._detail_scenes.get(self._drag_id)
            if island is not None:
                self._on_move_island(self._drag_id,
                                     (island.bounds.x + scene_delta.x,
                                      island.bounds.y + scene_delta.y))
        elif moved and self._drag_mode == "pan":
            self._notify_viewport()
        elif not moved and self._drag_id is not None:
            self._refresh_selection_scenes()
        self._drag_mode = None
        self._drag_id = None
        self._drag_context = None
        self._drag_point = None
        self._drag_total = Point(0.0, 0.0)

    def _refresh_selection_scenes(self) -> None:
        if self._main_view is None:
            return
        selected = self._selected_id if self._selected_context is None else None
        self._main_scene = graph_scene(self._main_view, selected, viewport_width=400.0)
        details = tuple((state, self._detail_views[island_id])
                        for island_id, state in self._detail_states.items()
                        if island_id in self._detail_views)
        self._detail_scenes = self._compose_details(details)
        self._draw_all()

    def _pan_start(self, event: Any) -> str:
        self.canvas.focus_set()
        self._drag_mode = "pan"
        self._drag_id = None
        self._drag_context = None
        self._drag_point = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self._drag_total = Point(0.0, 0.0)
        return "break"

    def _blank_pan_start(self, event: Any) -> str | None:
        if not self.canvas.find_withtag("current"):
            return self._pan_start(event)
        return None

    def _pan_move(self, event: Any) -> None:
        if self._drag_mode != "pan" or self._drag_point is None:
            return
        point = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        dx, dy = point.x - self._drag_point.x, point.y - self._drag_point.y
        self.viewport = Viewport(self.viewport.zoom,
                                 Point(self.viewport.pan.x + dx,
                                       self.viewport.pan.y + dy))
        self.canvas.move("all", dx, dy)
        self._drag_total = Point(self._drag_total.x + dx, self._drag_total.y + dy)
        self._drag_point = point

    def _on_zoom(self, event: Any) -> str:
        anchor = Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        zoom_factor = 1.1 if (getattr(event, "num", 0) == 4 or
                              getattr(event, "delta", 0) > 0) else 1 / 1.1
        self.viewport = zoom_about(self.viewport, anchor,
                                   self.viewport.zoom * zoom_factor)
        self._draw_all()
        self._notify_viewport()
        return "break"

    def _on_mousewheel(self, event: Any) -> str:
        if getattr(event, "state", 0) & 0x4:
            return self._on_zoom(event)
        direction = (-1 if getattr(event, "num", 0) == 4
                     or getattr(event, "delta", 0) > 0 else 1)
        self.canvas.yview_scroll(direction, "units")
        return "break"

    def _on_page_key(self, event: Any) -> str:
        self.canvas.yview_scroll(-1 if event.keysym == "Prior" else 1, "pages")
        return "break"

    def _on_resize(self, _event: Any) -> None:
        if self._main_view is not None:
            self._draw_all()

    def fit_to_window(self) -> None:
        bounds = self._scene_content_bounds()
        width = max(float(self.canvas.winfo_width()), 1.0)
        height = max(float(self.canvas.winfo_height()), 1.0)
        available_width = max(width - 2 * _SCENE_MARGIN, 1.0)
        available_height = max(height - 2 * _SCENE_MARGIN, 1.0)
        requested = min(available_width / max(bounds.width, 1.0),
                        available_height / max(bounds.height, 1.0))
        fitted = zoom_about(Viewport(), Point(0.0, 0.0), requested).zoom
        pan = Point((width - bounds.width * fitted) / 2.0 - bounds.x * fitted,
                    (height - bounds.height * fitted) / 2.0 - bounds.y * fitted)
        self.viewport = Viewport(fitted, pan)
        self._draw_all()
        self._notify_viewport()

    def reset_zoom(self) -> None:
        center = Point(float(self.canvas.winfo_width()) / 2.0,
                       float(self.canvas.winfo_height()) / 2.0)
        scene_center = screen_to_scene(center, self.viewport)
        self.viewport = Viewport(1.0, Point(center.x - scene_center.x,
                                            center.y - scene_center.y))
        self._draw_all()
        self._notify_viewport()

    def _notify_viewport(self) -> None:
        self._on_viewport(self.viewport.zoom,
                          (self.viewport.pan.x, self.viewport.pan.y))
