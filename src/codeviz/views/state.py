"""Serializable user view state, separate from analysis facts and caches."""

from dataclasses import dataclass, field
from math import isfinite
from typing import cast

from codeviz.core import ViewKind


@dataclass(frozen=True, slots=True)
class ViewState:
    expanded_ids: tuple[str, ...] = ()
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    active_view: ViewKind | None = None
    selected_id: str | None = None
    open_tabs: tuple[str, ...] = ()
    graph_roots: dict[str, str | None] = field(default_factory=dict)
    zoom: float = 1.0
    pan: tuple[float, float] = (0.0, 0.0)
    filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not node_id for node_id in self.expanded_ids):
            raise ValueError("expanded node IDs cannot be empty")
        for node_id, position in self.positions.items():
            if not node_id or len(position) != 2 or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) for value in position
            ):
                raise ValueError("positions require finite numeric x/y values")
        if self.active_view is not None and not isinstance(self.active_view, ViewKind):
            raise ValueError("active view must be a known graph kind")
        if self.selected_id == "":
            raise ValueError("selected node ID cannot be empty")
        if (len(self.open_tabs) > 40 or any(not item or len(item) > 4000
                                            for item in self.open_tabs)):
            raise ValueError("open tabs exceed their limits")
        if any(key not in {kind.value for kind in ViewKind} or
               (value is not None and not value)
               for key, value in self.graph_roots.items()):
            raise ValueError("graph roots must use known view kinds and nonempty IDs")
        if isinstance(self.zoom, bool) or not isfinite(self.zoom) or self.zoom <= 0:
            raise ValueError("zoom must be a positive finite number")
        if (len(self.pan) != 2 or any(isinstance(value, bool) or
                                     not isinstance(value, (int, float)) or
                                     not isfinite(value) for value in self.pan)):
            raise ValueError("pan requires finite numeric x/y values")
        if any(not item for item in self.filters):
            raise ValueError("filters cannot contain empty names")

    def to_dict(self) -> dict[str, object]:
        return {"expanded_ids": list(self.expanded_ids),
                "positions": {node_id: list(point) for node_id, point in self.positions.items()},
                "active_view": self.active_view.value if self.active_view else None,
                "selected_id": self.selected_id,
                "open_tabs": list(self.open_tabs),
                "graph_roots": self.graph_roots,
                "zoom": self.zoom,
                "pan": list(self.pan),
                "filters": list(self.filters)}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ViewState":
        if not {"expanded_ids", "positions"} <= set(payload) <= {
            "expanded_ids", "positions", "active_view", "selected_id", "open_tabs",
            "graph_roots", "zoom", "pan", "filters"
        }:
            raise ValueError("view state has invalid fields")
        expanded = payload.get("expanded_ids", [])
        positions = payload.get("positions", {})
        if not isinstance(expanded, list) or not all(isinstance(item, str) for item in expanded):
            raise ValueError("expanded_ids must be a list of strings")
        if not isinstance(positions, dict):
            raise ValueError("positions must be an object")
        restored: dict[str, tuple[float, float]] = {}
        for node_id, value in positions.items():
            if not isinstance(node_id, str) or not isinstance(value, list) or len(value) != 2:
                raise ValueError("position entries require node ID and two coordinates")
            restored[node_id] = cast(tuple[float, float], tuple(value))
        active = payload.get("active_view")
        selected = payload.get("selected_id")
        tabs = payload.get("open_tabs", [])
        roots = payload.get("graph_roots", {})
        zoom = payload.get("zoom", 1.0)
        pan = payload.get("pan", [0.0, 0.0])
        filters = payload.get("filters", [])
        if ((active is not None and type(active) is not str) or
                (selected is not None and type(selected) is not str) or
                not isinstance(tabs, list) or any(type(item) is not str for item in tabs) or
                not isinstance(roots, dict) or any(type(key) is not str or
                                                    (value is not None and type(value) is not str)
                                                    for key, value in roots.items()) or
                type(zoom) not in (int, float) or not isinstance(pan, list) or
                len(pan) != 2 or any(type(item) not in (int, float) for item in pan) or
                not isinstance(filters, list) or any(type(item) is not str for item in filters)):
            raise ValueError("view state navigation fields are invalid")
        try:
            active_kind = ViewKind(active) if active is not None else None
        except ValueError as error:
            raise ValueError("unknown active view") from error
        return cls(tuple(expanded), restored, active_kind, selected,
                   tuple(tabs), cast(dict[str, str | None], roots),
                   float(cast(float | int, zoom)),
                   cast(tuple[float, float], tuple(pan)), tuple(filters))
