"""Deterministic graph layout hints; UI may refine them without changing facts."""

from collections import defaultdict

from codeviz.core import EdgeType, VisibleEdge, VisibleNode

from .state import ViewState

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


def layout_positions(nodes: tuple[VisibleNode, ...], edges: tuple[VisibleEdge, ...],
                     root_ids: tuple[str, ...], kind: str,
                     state: ViewState) -> dict[str, tuple[float, float]]:
    """Lay out supported graph views in deterministic top-to-bottom layers."""
    del kind  # Both supported views use the same readable layered placement.
    node_ids = tuple(sorted({node.id for node in nodes}))
    if not node_ids:
        return {}

    known_ids = set(node_ids)
    roots = tuple(sorted({node_id for node_id in root_ids if node_id in known_ids}))
    forward = tuple(sorted(
        (edge for edge in _forward_edges(edges)
         if edge.source in known_ids and edge.target in known_ids),
        key=lambda edge: (edge.source, edge.target, edge.type.value),
    ))
    ranks = _ranks(node_ids, forward, roots)
    by_rank: dict[int, list[str]] = defaultdict(list)
    for node_id in node_ids:
        by_rank[ranks[node_id]].append(node_id)

    predecessors: dict[str, list[str]] = defaultdict(list)
    for edge in forward:
        if ranks[edge.source] < ranks[edge.target]:
            predecessors[edge.target].append(edge.source)

    positions: dict[str, tuple[float, float]] = {}
    for rank in sorted(by_rank):
        def sort_key(node_id: str) -> tuple[float, str]:
            previous = [positions[parent][0] for parent in predecessors[node_id]
                        if parent in positions]
            barycenter = sum(previous) / len(previous) if previous else 0.0
            return barycenter, node_id

        layer = sorted(by_rank[rank], key=sort_key)
        offset = (len(layer) - 1) / 2
        for column, node_id in enumerate(layer):
            positions[node_id] = ((column - offset) * _X_GAP, rank * _Y_GAP)

    positions.update({node_id: state.positions[node_id]
                      for node_id in node_ids if node_id in state.positions})
    return positions
