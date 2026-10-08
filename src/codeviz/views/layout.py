"""Deterministic graph layout hints; UI may refine them without changing facts."""

from collections import defaultdict, deque
from collections.abc import Iterator

from codeviz.core import EdgeType, ViewIssue, ViewKind, VisibleEdge, VisibleNode

from .state import ViewState

_X_GAP = 220.0
_Y_GAP = 112.0
_FLOW_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.CONTROL_FLOW,
                         EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE})
_VIEW_EDGES = {
    ViewKind.ARCHITECTURE: frozenset({EdgeType.CONTAINS}),
    ViewKind.PROJECT_STRUCTURE: frozenset({EdgeType.CONTAINS}),
    ViewKind.EXECUTION: _FLOW_EDGES,
    ViewKind.FUNCTION_FLOW: _FLOW_EDGES,
    ViewKind.DATA_FLOW: frozenset({EdgeType.READS, EdgeType.WRITES,
                                 EdgeType.DATA_FLOW, EdgeType.PASSES_TO,
                                 EdgeType.RETURNS_TO}),
    ViewKind.SYMBOL: frozenset({EdgeType.CONTAINS, EdgeType.IMPORTS,
                              EdgeType.EXPORTS, EdgeType.CALLS,
                              EdgeType.INHERITS, EdgeType.IMPLEMENTS}),
}


def _forward_edges(edges: tuple[VisibleEdge, ...],
                   view_kind: ViewKind | None = None) -> tuple[VisibleEdge, ...]:
    # READS is already source -> use in the visible graph. Never reinterpret
    # code facts or reverse the displayed relationship here.
    allowed = _VIEW_EDGES.get(view_kind, _FLOW_EDGES) if view_kind else _FLOW_EDGES
    return tuple(edge for edge in edges if edge.type in allowed and edge.source != edge.target)


def _ranks(node_ids: tuple[str, ...], edges: tuple[VisibleEdge, ...],
           roots: tuple[str, ...]) -> dict[str, int]:
    """Rank the SCC condensation DAG, never repeatedly deepen a cycle."""
    root_ids = set(roots)
    successors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        # Explicit structural/flow roots remain at the top. This omission is
        # presentation-only; the original visible edges remain untouched.
        if edge.target not in root_ids:
            successors[edge.source].add(edge.target)
            predecessors[edge.target].add(edge.source)

    # Iterative Kosaraju avoids recursion depth limits on long visible chains.
    visited: set[str] = set()
    finished: list[str] = []
    for node_id in node_ids:
        if node_id in visited:
            continue
        visited.add(node_id)
        stack: list[tuple[str, Iterator[str]]] = [
            (node_id, iter(sorted(successors[node_id])))]
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(current)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(successors[child]))))

    components: dict[str, int] = {}
    count = 0
    for node_id in reversed(finished):
        if node_id in components:
            continue
        component = count
        count += 1
        components[node_id] = component
        pending = [node_id]
        while pending:
            current = pending.pop()
            for parent in sorted(predecessors[current]):
                if parent not in components:
                    components[parent] = component
                    pending.append(parent)

    outgoing: list[set[int]] = [set() for _ in range(count)]
    indegrees = [0] * count
    for source in node_ids:
        source_component = components[source]
        for target in successors[source]:
            target_component = components[target]
            if (source_component != target_component
                    and target_component not in outgoing[source_component]):
                outgoing[source_component].add(target_component)
                indegrees[target_component] += 1
    ranks = [0] * count
    ready = deque(index for index, degree in enumerate(indegrees) if degree == 0)
    while ready:
        component = ready.popleft()
        for next_component in sorted(outgoing[component]):
            ranks[next_component] = max(ranks[next_component], ranks[component] + 1)
            indegrees[next_component] -= 1
            if indegrees[next_component] == 0:
                ready.append(next_component)
    return {node_id: ranks[components[node_id]] for node_id in node_ids}


def _layered_positions(
    node_ids: tuple[str, ...], forward: tuple[VisibleEdge, ...],
    roots: tuple[str, ...],
) -> dict[str, tuple[float, float]]:
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
    return positions


def layout_positions(nodes: tuple[VisibleNode, ...], edges: tuple[VisibleEdge, ...],
                     root_ids: tuple[str, ...], kind: str,
                     state: ViewState, *,
                     view_kind: ViewKind | None = None,
                     issues: list[ViewIssue] | None = None) -> dict[str, tuple[float, float]]:
    """Lay out supported graph views in deterministic top-to-bottom layers."""
    del kind  # Presentation policy follows the view, not its renderer label.
    node_ids = tuple(sorted({node.id for node in nodes}))
    if not node_ids:
        return {}

    known_ids = set(node_ids)
    roots = tuple(sorted({node_id for node_id in root_ids if node_id in known_ids}))
    if view_kind in (ViewKind.DATA_FLOW, ViewKind.SYMBOL):
        # These roots are query focus IDs, not graph-entry constraints.
        roots = ()
    forward = tuple(sorted(
        (edge for edge in _forward_edges(edges, view_kind)
         if edge.source in known_ids and edge.target in known_ids),
        key=lambda edge: (edge.source, edge.target, edge.type.value),
    ))
    try:
        positions = _layered_positions(node_ids, forward, roots)
    except Exception as error:
        if view_kind is None or issues is None:
            raise
        issues.append(ViewIssue(
            "LAYOUT_FALLBACK",
            f"Layered layout failed ({type(error).__name__}); "
            "using deterministic vertical placement.",
            view_kind,
        ))
        positions = {node_id: (0.0, index * _Y_GAP)
                     for index, node_id in enumerate(node_ids)}

    manual = state.positions if view_kind is None else state.positions_for(view_kind)
    positions.update({node_id: manual[node_id]
                      for node_id in node_ids if node_id in manual})
    return positions
