"""Deterministic graph layout hints; UI may refine them without changing facts."""

from collections import defaultdict, deque

from codeviz.core import EdgeType, VisibleEdge, VisibleNode

from .state import ViewState


def layout_positions(nodes: tuple[VisibleNode, ...], edges: tuple[VisibleEdge, ...],
                     root_ids: tuple[str, ...], kind: str,
                     state: ViewState) -> dict[str, tuple[float, float]]:
    node_ids = {node.id for node in nodes}
    index = {node.id: position for position, node in enumerate(nodes)}
    contains: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.type == EdgeType.CONTAINS:
            contains[edge.source].append(edge.target)
    nesting: dict[str, int] = {root: 0 for root in root_ids}
    frontier = deque(root_ids)
    while frontier:
        parent = frontier.popleft()
        for child in contains.get(parent, ()):
            if child not in nesting:
                nesting[child] = nesting[parent] + 1
                frontier.append(child)

    if kind == "hierarchical":
        rows: dict[int, int] = defaultdict(int)
        positions: dict[str, tuple[float, float]] = {}
        for node in nodes:
            depth = nesting.get(node.id, 1)
            positions[node.id] = (float(rows[depth] * 220), float(depth * 140))
            rows[depth] += 1
    else:
        forward_types = {EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
                         EdgeType.BRANCH_FALSE, EdgeType.CONTAINS}
        successors: dict[str, set[str]] = defaultdict(set)
        indegree = {node.id: 0 for node in nodes}
        for edge in edges:
            if edge.type in forward_types and edge.target not in successors[edge.source]:
                successors[edge.source].add(edge.target)
                indegree[edge.target] += 1
        ready = deque(node.id for node in nodes if indegree[node.id] == 0)
        ordering: list[str] = []
        while ready:
            current = ready.popleft()
            ordering.append(current)
            for child in sorted(successors[current], key=index.__getitem__):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        ordered_ids = set(ordering)
        ordering.extend(node.id for node in nodes if node.id not in ordered_ids)
        positions = {node_id: (float(position * 220), float(nesting.get(node_id, 1) * 150))
                     for position, node_id in enumerate(ordering)}

    positions.update({node_id: state.positions[node_id]
                      for node_id in node_ids if node_id in state.positions})
    return positions
