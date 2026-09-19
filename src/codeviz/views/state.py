"""Serializable user view state, separate from analysis facts and caches."""

from dataclasses import dataclass, field
from math import isfinite
from typing import cast

from codeviz.core import ViewKind


def _finite_point(value: object) -> bool:
    return (isinstance(value, tuple) and len(value) == 2 and
            all(not isinstance(item, bool) and isinstance(item, (int, float)) and
                isfinite(item) for item in value))


@dataclass(frozen=True, slots=True)
class ViewportState:
    zoom: float = 1.0
    pan: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        if (isinstance(self.zoom, bool) or not isinstance(self.zoom, (int, float)) or
                not isfinite(self.zoom) or not 0.25 <= self.zoom <= 2.5):
            raise ValueError("viewport zoom must be between 0.25 and 2.5")
        if not _finite_point(self.pan):
            raise ValueError("viewport pan requires finite numeric x/y values")


@dataclass(frozen=True, slots=True)
class DetailIslandState:
    id: str
    view_kind: ViewKind
    source_node_id: str
    position: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.id, str) or not self.id or len(self.id) > 4000 or
                not isinstance(self.source_node_id, str) or not self.source_node_id or
                len(self.source_node_id) > 4000):
            raise ValueError("detail island IDs must be nonempty and at most 4000 characters")
        if not isinstance(self.view_kind, ViewKind):
            raise ValueError("detail island view must be a known graph kind")
        if self.position is not None and not _finite_point(self.position):
            raise ValueError("detail island position requires finite numeric x/y values")


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
    viewport_by_view: dict[str, ViewportState] = field(default_factory=dict)
    detail_islands: tuple[DetailIslandState, ...] = ()

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
        known_views = {kind.value for kind in ViewKind}
        if any(type(key) is not str or key not in known_views or
               not isinstance(value, ViewportState)
               for key, value in self.viewport_by_view.items()):
            raise ValueError("viewports must use known view kinds")
        if any(not isinstance(island, DetailIslandState) for island in self.detail_islands):
            raise ValueError("detail islands contain an invalid record")
        island_ids = [island.id for island in self.detail_islands]
        if len(island_ids) != len(set(island_ids)):
            raise ValueError("detail island IDs must be unique")
        if any(sum(island.view_kind is kind for island in self.detail_islands) > 3
               for kind in ViewKind):
            raise ValueError("at most three detail islands may be open per view")
        object.__setattr__(self, "positions", dict(self.positions))
        object.__setattr__(self, "graph_roots", dict(self.graph_roots))
        object.__setattr__(self, "viewport_by_view", dict(self.viewport_by_view))
        object.__setattr__(self, "detail_islands", tuple(self.detail_islands))

    def to_dict(self) -> dict[str, object]:
        return {"expanded_ids": list(self.expanded_ids),
                "positions": {node_id: list(point) for node_id, point in self.positions.items()},
                "active_view": self.active_view.value if self.active_view else None,
                "selected_id": self.selected_id,
                "open_tabs": list(self.open_tabs),
                "graph_roots": self.graph_roots,
                "zoom": self.zoom,
                "pan": list(self.pan),
                "filters": list(self.filters),
                "viewport_by_view": {
                    kind: {"zoom": self.viewport_by_view[kind].zoom,
                           "pan": list(self.viewport_by_view[kind].pan)}
                    for kind in sorted(self.viewport_by_view)
                },
                "detail_islands": [
                    {"id": island.id, "view_kind": island.view_kind.value,
                     "source_node_id": island.source_node_id,
                     "position": (list(island.position)
                                  if island.position is not None else None)}
                    for island in self.detail_islands
                ]}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ViewState":
        if not {"expanded_ids", "positions"} <= set(payload) <= {
            "expanded_ids", "positions", "active_view", "selected_id", "open_tabs",
            "graph_roots", "zoom", "pan", "filters", "viewport_by_view",
            "detail_islands"
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
        viewports = payload.get("viewport_by_view", {})
        islands = payload.get("detail_islands", [])
        if ((active is not None and type(active) is not str) or
                (selected is not None and type(selected) is not str) or
                not isinstance(tabs, list) or any(type(item) is not str for item in tabs) or
                not isinstance(roots, dict) or any(type(key) is not str or
                                                    (value is not None and type(value) is not str)
                                                    for key, value in roots.items()) or
                type(zoom) not in (int, float) or not isinstance(pan, list) or
                len(pan) != 2 or any(type(item) not in (int, float) for item in pan) or
                not isinstance(filters, list) or any(type(item) is not str for item in filters) or
                not isinstance(viewports, dict) or not isinstance(islands, list)):
            raise ValueError("view state navigation fields are invalid")
        try:
            active_kind = ViewKind(active) if active is not None else None
        except ValueError as error:
            raise ValueError("unknown active view") from error
        restored_viewports: dict[str, ViewportState] = {}
        for kind, value in viewports.items():
            if (type(kind) is not str or not isinstance(value, dict) or
                    set(value) != {"zoom", "pan"} or
                    type(value["zoom"]) not in (int, float) or
                    not isinstance(value["pan"], list) or len(value["pan"]) != 2 or
                    any(type(item) not in (int, float) for item in value["pan"])):
                raise ValueError("viewport entries are invalid")
            restored_viewports[kind] = ViewportState(
                float(value["zoom"]), cast(tuple[float, float], tuple(value["pan"])))
        restored_islands: list[DetailIslandState] = []
        for value in islands:
            if (not isinstance(value, dict) or
                    set(value) != {"id", "view_kind", "source_node_id", "position"} or
                    type(value["id"]) is not str or type(value["view_kind"]) is not str or
                    type(value["source_node_id"]) is not str or
                    (value["position"] is not None and
                     (not isinstance(value["position"], list) or
                      len(value["position"]) != 2 or
                      any(type(item) not in (int, float) for item in value["position"])) )):
                raise ValueError("detail island entries are invalid")
            try:
                island_kind = ViewKind(value["view_kind"])
            except ValueError as error:
                raise ValueError("unknown detail island view") from error
            raw_position = value["position"]
            position = (None if raw_position is None else
                        cast(tuple[float, float], tuple(raw_position)))
            restored_islands.append(DetailIslandState(
                value["id"], island_kind, value["source_node_id"], position))
        return cls(tuple(expanded), restored, active_kind, selected,
                   tuple(tabs), cast(dict[str, str | None], roots),
                   float(cast(float | int, zoom)),
                   cast(tuple[float, float], tuple(pan)), tuple(filters),
                   restored_viewports, tuple(restored_islands))
