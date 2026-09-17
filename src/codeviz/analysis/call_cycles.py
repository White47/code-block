"""Find exact function-call cycles without expanding recursive paths."""

from collections import defaultdict

from codeviz.core import CodeGraph, Confidence, EdgeType, NodeKind


def find_call_cycles(graph: CodeGraph) -> tuple[tuple[str, ...], ...]:
    callable_ids = {node.id for node in graph.nodes.values()
                    if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD)}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.type != EdgeType.CALLS or edge.confidence != Confidence.EXACT:
            continue
        owner = graph.nodes[edge.source].metadata.get("scope_symbol_id")
        if owner in callable_ids and edge.target in callable_ids:
            adjacency[owner].add(edge.target)

    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)

    visited: set[str] = set()
    finish_order: list[str] = []
    for root in sorted(callable_ids):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(adjacency[root])))]
        while stack:
            current, neighbors = stack[-1]
            next_id = next(neighbors, None)
            if next_id is None:
                finish_order.append(current)
                stack.pop()
            elif next_id not in visited:
                visited.add(next_id)
                stack.append((next_id, iter(sorted(adjacency[next_id]))))

    visited.clear()
    cycles: list[tuple[str, ...]] = []
    for root in reversed(finish_order):
        if root in visited:
            continue
        component: list[str] = []
        pending = [root]
        visited.add(root)
        while pending:
            current = pending.pop()
            component.append(current)
            for predecessor in sorted(reverse[current]):
                if predecessor not in visited:
                    visited.add(predecessor)
                    pending.append(predecessor)
        if len(component) > 1 or root in adjacency[root]:
            cycles.append(tuple(sorted(component)))
    return tuple(sorted(cycles))
