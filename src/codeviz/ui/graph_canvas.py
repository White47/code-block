"""Tk adapter for a bounded VisibleGraph."""

from functools import partial
from typing import Any, Callable

from codeviz.core import EdgeType, ViewKind, VisibleGraph

from . import theme
from .canvas_scene import (
    EdgePaint,
    GraphScene,
    SceneEdge,
    SceneNode,
    edge_paint,
    graph_scene,
)

__all__ = ["GraphCanvas", "EdgePaint", "GraphScene", "SceneEdge", "SceneNode",
           "edge_paint", "graph_scene"]


class GraphCanvas:
    """Thin Tk adapter; importing this module does not create a window."""

    def __init__(self, parent: Any, *, on_select: Callable[[str], None],
                 on_drill_down: Callable[[str], None],
                 on_move: Callable[[str, tuple[float, float]], None],
                 translate: Callable[[str], str] | None = None) -> None:
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
        self._on_drill_down = on_drill_down
        self._on_move = on_move
        self._translate = translate or (lambda phrase: phrase)
        self._view: VisibleGraph | None = None
        self._selected_id: str | None = None
        self._drag_id: str | None = None
        self._drag_point: tuple[float, float] | None = None
        self._drag_total = (0.0, 0.0)
        self.canvas.bind("<Return>", self._enter)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Next>", self._on_page_key)
        self.canvas.bind("<Prior>", self._on_page_key)
        self.canvas.bind("<Configure>", self._on_resize)

    def render(self, view: VisibleGraph, selected_id: str | None = None) -> None:
        self._view = view
        self._selected_id = selected_id
        scene = graph_scene(view, selected_id,
                            viewport_width=max(float(self.canvas.winfo_width()), 400.0))
        self.canvas.delete("all")
        if not scene.nodes:
            message = ("No model components detected · inspect Analysis Issues"
                       if view.view_kind == ViewKind.ARCHITECTURE else
                       "No execution entry points detected · inspect Analysis Issues"
                       if view.view_kind == ViewKind.EXECUTION else
                       "No local code context available · open source instead")
            self.canvas.create_text(80, 110, text=self._translate(message), anchor="w",
                                    fill=theme.MUTED,
                                    font=(theme.UI_FONT, 12), tags=("empty-state",))
        for edge in scene.edges:
            paint = edge_paint(edge.style)
            self.canvas.create_line(list(edge.points), fill=paint.color,
                                    dash=paint.dash or (), width=paint.width,
                                    arrow="last", smooth=edge.type == EdgeType.LOOP_BACK)
        for node in scene.nodes:
            tag = f"node:{node.id}"
            fill = theme.SURFACE_SELECTED if node.selected else theme.SURFACE
            outline = theme.ACCENT if node.selected or node.neighbor else theme.BORDER
            self.canvas.create_rectangle(node.x, node.y,
                                         node.x + node.width, node.y + node.height,
                                         fill=fill, outline=outline, width=2,
                                         dash=(5, 3) if node.member_count else (),
                                         tags=(tag,))
            self.canvas.create_text(node.x + 12, node.y + 20, text=node.label,
                                    anchor="w", fill=theme.TEXT,
                                    width=node.width - 24,
                                    font=(theme.UI_FONT, 11, "bold"), tags=(tag,))
            self.canvas.create_text(node.x + 12, node.y + 47, text=node.subtitle,
                                    anchor="w", fill=theme.MUTED,
                                    width=node.width - 24,
                                    font=(theme.CODE_FONT, 9), tags=(tag,))
            self.canvas.tag_bind(tag, "<ButtonPress-1>",
                                 partial(self._press, node_id=node.id))
            self.canvas.tag_bind(tag, "<B1-Motion>", self._drag)
            self.canvas.tag_bind(tag, "<ButtonRelease-1>", self._release)
            self.canvas.tag_bind(tag, "<Double-Button-1>",
                                 partial(self._double_click, node_id=node.id))
        legend = "  ·  ".join(self._translate(item.label) for item in view.legend)
        self.canvas.create_text(12, 16, text=legend, anchor="w", fill=theme.MUTED,
                                font=(theme.UI_FONT, 9))
        self.canvas.configure(scrollregion=(0, 0, max(scene.width, 400.0),
                                            max(scene.height, 250.0)))

    def _on_mousewheel(self, event: Any) -> str:
        direction = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(direction, "units")
        return "break"

    def _on_page_key(self, event: Any) -> str:
        self.canvas.yview_scroll(-1 if event.keysym == "Prior" else 1, "pages")
        return "break"

    def _on_resize(self, _event: Any) -> None:
        if self._view is not None:
            first = self.canvas.yview()[0]
            self.render(self._view, self._selected_id)
            self.canvas.yview_moveto(first)

    def _press(self, event: Any, node_id: str) -> None:
        self.canvas.focus_set()
        self._drag_id = node_id
        self._drag_point = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self._drag_total = (0.0, 0.0)
        self._selected_id = node_id
        self._on_select(node_id)

    def _drag(self, event: Any) -> None:
        if self._drag_id is None or self._drag_point is None:
            return
        point = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        dx, dy = point[0] - self._drag_point[0], point[1] - self._drag_point[1]
        self.canvas.move(f"node:{self._drag_id}", dx, dy)
        self._drag_total = (self._drag_total[0] + dx, self._drag_total[1] + dy)
        self._drag_point = point

    def _release(self, _event: Any) -> None:
        if self._drag_id is not None and self._view is not None and self._drag_total != (0.0, 0.0):
            old = self._view.layout_hints[self._drag_id]
            self._on_move(self._drag_id,
                          (old[0] + self._drag_total[0], old[1] + self._drag_total[1]))
        elif self._view is not None:
            self.render(self._view, self._selected_id)
        self._drag_id = None
        self._drag_point = None

    def _enter(self, _event: Any) -> None:
        if self._selected_id is not None:
            self._on_drill_down(self._selected_id)

    def _double_click(self, _event: Any, *, node_id: str) -> None:
        self._on_drill_down(node_id)
