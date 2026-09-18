"""Read-only, indexed traversal over semantic graph facts."""

from collections import defaultdict, deque

from codeviz.core import EdgeType, SemanticComponent, SemanticGraph, SemanticRelation


class GraphQuery:
    def __init__(self, graph: SemanticGraph) -> None:
        self.components: dict[str, SemanticComponent] = {
            component.id: component for component in graph.components
        }
        self.roots = graph.roots
        self.relations = graph.relations
        children: dict[str, list[str]] = defaultdict(list)
        parents: dict[str, list[str]] = defaultdict(list)
        adjacent: dict[str, list[SemanticRelation]] = defaultdict(list)
        for relation in graph.relations:
            adjacent[relation.source].append(relation)
            if relation.target != relation.source:
                adjacent[relation.target].append(relation)
            if relation.type == EdgeType.CONTAINS:
                if relation.target not in children[relation.source]:
                    children[relation.source].append(relation.target)
                if relation.source not in parents[relation.target]:
                    parents[relation.target].append(relation.source)
        self._children = {key: tuple(value) for key, value in children.items()}
        self._parents = {key: tuple(value) for key, value in parents.items()}
        self._adjacent = {key: tuple(value) for key, value in adjacent.items()}

    def children(self, node_id: str) -> tuple[str, ...]:
        return self._children.get(node_id, ())

    def parents(self, node_id: str) -> tuple[str, ...]:
        return self._parents.get(node_id, ())

    def neighbors(self, node_id: str, allowed: frozenset[EdgeType]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for relation in self._adjacent.get(node_id, ()):
            if relation.type not in allowed:
                continue
            neighbor = relation.target if relation.source == node_id else relation.source
            if neighbor != node_id:
                seen[neighbor] = None
        return tuple(seen)

    def descendants(self, roots: tuple[str, ...]) -> frozenset[str]:
        seen: set[str] = set()
        frontier = list(roots)
        while frontier:
            node_id = frontier.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            frontier.extend(self.children(node_id))
        return frozenset(seen)

    def path_to_root(self, node_id: str, roots: frozenset[str]) -> tuple[str, ...]:
        if node_id in roots:
            return (node_id,)
        frontier: deque[tuple[str, tuple[str, ...]]] = deque(((node_id, (node_id,)),))
        seen = {node_id}
        while frontier:
            current, path = frontier.popleft()
            for parent in self.parents(current):
                if parent in seen:
                    continue
                if parent in roots:
                    return (parent, *reversed(path))
                seen.add(parent)
                frontier.append((parent, (*path, parent)))
        return ()
