"""Serializable user view state, separate from analysis facts and caches."""

from dataclasses import dataclass, field
from math import isfinite
from typing import cast


@dataclass(frozen=True, slots=True)
class ViewState:
    expanded_ids: tuple[str, ...] = ()
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(not node_id for node_id in self.expanded_ids):
            raise ValueError("expanded node IDs cannot be empty")
        for node_id, position in self.positions.items():
            if not node_id or len(position) != 2 or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) for value in position
            ):
                raise ValueError("positions require finite numeric x/y values")

    def to_dict(self) -> dict[str, object]:
        return {"expanded_ids": list(self.expanded_ids),
                "positions": {node_id: list(point) for node_id, point in self.positions.items()}}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ViewState":
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
        return cls(tuple(expanded), restored)
