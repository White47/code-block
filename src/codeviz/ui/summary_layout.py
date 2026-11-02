"""Persist summary geometry separately from native CodeGraph identities."""

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from math import isfinite

from codeviz.core import ViewKind
from codeviz.views.state import ViewportState, ViewState

_PREFIX = "summary:v1:"
_LIMIT = 1024


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("invalid summary identity")
    return value


def _point(value: object) -> tuple[float, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise ValueError("invalid summary position")
    coordinates = (value["x"], value["y"])
    if any(isinstance(n, bool) or not isinstance(n, (int, float))
           or not isfinite(n) or abs(n) > 1_000_000 for n in coordinates):
        raise ValueError("summary position is outside the allowed range")
    return float(coordinates[0]), float(coordinates[1])


@dataclass(frozen=True, slots=True)
class SummaryGeometry:
    kind: str
    id: str
    node_ids: tuple[str, ...]
    signature: str


class SummaryLayoutIndex:
    def __init__(self, project_scope: str, source_revision: str,
                 contexts: tuple[SummaryGeometry, ...]) -> None:
        self._contexts = {(item.kind, item.id): item for item in contexts}
        self._tokens = {
            key: _digest((project_scope, source_revision, key, item.signature))
            for key, item in self._contexts.items()
        }
        self._keys = {
            key: {node: f"{_PREFIX}{_digest((project_scope, key))}:{_digest(node)}"
                  for node in item.node_ids}
            for key, item in self._contexts.items()
        }
        self._valid_positions = {item for items in self._keys.values() for item in items.values()}

    def _context(self, context: object, token: object = None) -> tuple[str, str]:
        if not isinstance(context, dict) or set(context) != {"kind", "id"}:
            raise ValueError("invalid summary context")
        key = (_identifier(context["kind"]), _identifier(context["id"]))
        if key not in self._contexts:
            raise ValueError("summary context is no longer available")
        if token is not None and token != self._tokens[key]:
            raise ValueError("summary layout changed; reload it before saving")
        return key

    def payload(self, state: ViewState, context: object) -> dict[str, object]:
        key = self._context(context)
        positions = state.positions_by_view.get(ViewKind.OVERVIEW.value, {})
        viewport = state.viewport_by_view.get(ViewKind.OVERVIEW.value, ViewportState())
        return {"context": {"kind": key[0], "id": key[1]},
                "layoutToken": self._tokens[key],
                "viewportSaved": ViewKind.OVERVIEW.value in state.viewport_by_view,
                "positions": {node: list(positions[persisted])
                              for node, persisted in self._keys[key].items()
                              if persisted in positions},
                "viewport": {"zoom": viewport.zoom, "pan": list(viewport.pan)}}

    def valid_position_key(self, key: str) -> bool:
        return key in self._valid_positions

    def _positions(self, state: ViewState, key: tuple[str, str]) -> dict[str, tuple[float, float]]:
        positions = {node: point for node, point in
                     state.positions_by_view.get(ViewKind.OVERVIEW.value, {}).items()
                     if not node.startswith(_PREFIX) or node in self._valid_positions}
        current = set(self._keys[key].values())
        summaries = sorted((node for node in positions if node.startswith(_PREFIX)),
                           key=lambda node: (node not in current, node))
        for removed in summaries[_LIMIT:]:
            del positions[removed]
        return positions

    def move(self, state: ViewState, context: object, token: object,
             node_id: object, position: object) -> ViewState:
        key = self._context(context, _identifier(token))
        node = _identifier(node_id)
        if node not in self._keys[key]:
            raise ValueError("summary node is not in this context")
        point = _point(position)
        positions = self._positions(state, key)
        positions[self._keys[key][node]] = point
        return replace(state, positions_by_view={**state.positions_by_view,
                       ViewKind.OVERVIEW.value: positions})

    def save_viewport(self, state: ViewState, context: object, token: object,
                      viewport: object) -> ViewState:
        key = self._context(context, _identifier(token))
        if not isinstance(viewport, dict) or set(viewport) != {"zoom", "pan"}:
            raise ValueError("invalid summary viewport")
        pan = viewport["pan"]
        if not isinstance(pan, (tuple, list)) or len(pan) != 2:
            raise ValueError("invalid summary viewport pan")
        point = _point({"x": pan[0], "y": pan[1]})
        zoom = viewport["zoom"]
        if isinstance(zoom, bool) or not isinstance(zoom, (float, int)):
            raise ValueError("invalid summary viewport zoom")
        saved = ViewportState(float(zoom), point)
        return replace(state, positions_by_view={**state.positions_by_view,
                       ViewKind.OVERVIEW.value: self._positions(state, key)},
                       viewport_by_view={**state.viewport_by_view,
                       ViewKind.OVERVIEW.value: saved})
