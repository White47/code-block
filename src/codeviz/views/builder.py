"""Bounded semantic views; never return the full analysis graph to the UI."""

from collections import deque
from dataclasses import dataclass

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    EdgeType,
    ViewKind,
    VisibleAggregation,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)

from .data import critical_data_slice
from .layout import layout_positions
from .query import GraphQuery
from .state import ViewState

_ARCHITECTURE_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.DATA_FLOW})
_EXECUTION_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.CONTROL_FLOW,
                              EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE,
                              EdgeType.LOOP_BACK, EdgeType.DATA_FLOW})
_LEGEND: dict[EdgeType, tuple[str, str]] = {
    EdgeType.CONTAINS: ("Contains", "structural-dotted"),
    EdgeType.DATA_FLOW: ("Data Flow", "data-solid"),
    EdgeType.CONTROL_FLOW: ("Control Flow", "control-solid"),
    EdgeType.BRANCH_TRUE: ("True Branch", "branch-true"),
    EdgeType.BRANCH_FALSE: ("False Branch", "branch-false"),
    EdgeType.LOOP_BACK: ("Loop", "loop-curved"),
}


@dataclass(frozen=True, slots=True)
class ViewRequest:
    view_kind: ViewKind
    root_id: str | None = None
    expanded_ids: tuple[str, ...] = ()
    selected_id: str | None = None
    hops: int = 1
    max_nodes: int = 100
    max_edges: int = 300

    def __post_init__(self) -> None:
        if (type(self.max_nodes) is not int or type(self.max_edges) is not int
                or type(self.hops) is not int or self.max_nodes < 1
                or self.max_edges < 1 or self.hops < 0 or self.hops > 5):
            raise ValueError("view budgets must be positive and hops must be between 0 and 5")
        if self.root_id == "" or self.selected_id == "" or any(
            not item for item in self.expanded_ids
        ):
            raise ValueError("view IDs cannot be empty")


def _visible_node(query: GraphQuery, node_id: str) -> VisibleNode:
    component = query.components[node_id]
    repeat = component.metadata.get("repeat_count")
    display_name = (f"{component.display_name} ×{repeat}"
                    if isinstance(repeat, int) and repeat > 1 else component.display_name)
    return VisibleNode(
        node_id, display_name, semantic_component_ids=(node_id,),
        code_node_ids=component.code_symbol_ids, source_anchors=component.source_anchors,
        code_name=component.code_name, role=component.role,
        confidence=component.confidence,
        metadata={**component.metadata, "has_children": bool(query.children(node_id))},
    )


def build_visible_graph(analysis: ProjectAnalysis, request: ViewRequest,
                        state: ViewState | None = None) -> VisibleGraph:
    """Return only the source-backed local graph permitted by this request's budgets."""
    if analysis.semantic is None:
        raise ValueError("semantic analysis is required before building a visible graph")
    query = GraphQuery(analysis.semantic.graph)
    if request.view_kind == ViewKind.ARCHITECTURE:
        root_role, allowed, layout_kind = "ModelDefinition", _ARCHITECTURE_EDGES, "hierarchical"
    elif request.view_kind == ViewKind.EXECUTION:
        root_role, allowed, layout_kind = "EntryPoint", _EXECUTION_EDGES, "flowchart"
    else:
        raise ValueError(f"view kind {request.view_kind!s} is not implemented in Phase 7")
    available_roots = tuple(root for root in query.roots
                            if query.components[root].role == root_role)
    if request.root_id is not None and request.root_id not in available_roots:
        raise ValueError("requested root is not available in this view")
    roots = (request.root_id,) if request.root_id is not None else available_roots
    data_slice = critical_data_slice(analysis, roots, request.view_kind)
    data_by_id = {node.id: node for node in data_slice.nodes}
    semantic_reachable = query.descendants(roots)
    reachable = semantic_reachable | data_by_id.keys()
    if request.selected_id is not None and request.selected_id not in reachable:
        raise ValueError("selected component is outside the requested view")
    state = state or ViewState()
    expanded = frozenset((*request.expanded_ids, *state.expanded_ids))
    candidates: dict[str, None] = dict.fromkeys(roots)
    if request.selected_id is not None:
        path = ((data_slice.root_by_node[request.selected_id], request.selected_id)
                if request.selected_id in data_by_id else
                query.path_to_root(request.selected_id, frozenset(roots)))
        candidates.update(dict.fromkeys(path))
    frontier = deque(roots)
    walked: set[str] = set()
    while frontier:
        current = frontier.popleft()
        if current in walked:
            continue
        walked.add(current)
        if current not in roots and current not in expanded:
            continue
        for child in query.children(current):
            candidates[child] = None
            if child in expanded:
                frontier.append(child)
    candidates.update(dict.fromkeys(data_by_id))
    selected_context: list[str] = []
    selected_context_edges: set[frozenset[str]] = set()
    if request.selected_id is not None:
        data_neighbors: dict[str, list[str]] = {}
        for edge in data_slice.edges:
            data_neighbors.setdefault(edge.source, []).append(edge.target)
            data_neighbors.setdefault(edge.target, []).append(edge.source)
        local = deque(((request.selected_id, 0),))
        visited = {request.selected_id}
        while local:
            current, distance = local.popleft()
            if distance == request.hops:
                continue
            neighbors = (data_neighbors.get(current, ()) if current in data_by_id else
                         query.neighbors(current, allowed))
            for neighbor in neighbors:
                if neighbor not in reachable:
                    continue
                selected_context_edges.add(frozenset((current, neighbor)))
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                selected_context.append(neighbor)
                candidates[neighbor] = None
                local.append((neighbor, distance + 1))

    if request.selected_id is not None:
        selected_path = ((data_slice.root_by_node[request.selected_id], request.selected_id)
                         if request.selected_id in data_by_id else
                         query.path_to_root(request.selected_id, frozenset(roots)))
        priority_ids = ((request.selected_id, *selected_path, *roots)
                        if request.max_nodes == 1 else
                        (selected_path[0], request.selected_id, *selected_path[1:-1],
                         *selected_context, *roots))
        essential_count = len({*selected_path, *selected_context})
    else:
        priority_ids = roots
        essential_count = 1 if roots else 0
    candidate_ids = tuple(dict.fromkeys((*priority_ids, *candidates)))
    if len(candidate_ids) > request.max_nodes:
        reserved = 1 if request.max_nodes > essential_count else 0
        selected_ids = candidate_ids[:request.max_nodes - reserved]
        hidden_candidate_ids = candidate_ids[request.max_nodes - reserved:]
    else:
        selected_ids = candidate_ids
        hidden_candidate_ids = ()
    nodes = tuple(
        VisibleNode(node_id, data_by_id[node_id].name, code_node_ids=(node_id,),
                    source_anchors=data_by_id[node_id].source_anchors,
                    role="DataNode", confidence=data_by_id[node_id].confidence,
                    metadata=dict(data_by_id[node_id].metadata))
        if node_id in data_by_id else _visible_node(query, node_id)
        for node_id in selected_ids
    )
    aggregations: tuple[VisibleAggregation, ...] = ()
    if hidden_candidate_ids and len(nodes) < request.max_nodes:
        aggregate_id = f"aggregate:{request.view_kind.value}:{request.root_id or 'all'}"
        nodes += (VisibleNode(aggregate_id, f"{len(hidden_candidate_ids)} more",
                              member_ids=hidden_candidate_ids,
                              metadata={"hidden_count": len(hidden_candidate_ids)}),)
        aggregations = (VisibleAggregation(aggregate_id, hidden_candidate_ids),)
    visible_ids = {node.id for node in nodes}
    eligible_relations = tuple(relation for relation in query.relations
                               if relation.type in allowed and relation.source in reachable
                               and relation.target in reachable)
    relations = tuple(relation for relation in eligible_relations
                      if relation.source in visible_ids and relation.target in visible_ids)
    if request.view_kind == ViewKind.EXECUTION:
        priority = {EdgeType.CONTROL_FLOW: 0, EdgeType.BRANCH_TRUE: 0,
                    EdgeType.BRANCH_FALSE: 0, EdgeType.LOOP_BACK: 1,
                    EdgeType.DATA_FLOW: 2, EdgeType.CONTAINS: 3}
        relations = tuple(sorted(relations, key=lambda relation: priority[relation.type]))
    semantic_edges = tuple(VisibleEdge(
        relation.source, relation.target, relation.type,
        semantic_component_ids=(relation.source, relation.target),
        source_anchors=relation.source_anchors, confidence=relation.confidence)
        for relation in relations)
    code_data_edges = tuple(VisibleEdge(
        edge.source, edge.target, EdgeType.DATA_FLOW,
        source_anchors=(edge.source_anchor,) if edge.source_anchor is not None else
        data_by_id[edge.source].source_anchors,
        confidence=edge.confidence)
        for edge in data_slice.edges
        if edge.source in visible_ids and edge.target in visible_ids)
    if request.view_kind == ViewKind.EXECUTION:
        flow_edges = tuple(edge for edge in semantic_edges if edge.type != EdgeType.CONTAINS)
        structural_edges = tuple(edge for edge in semantic_edges if edge.type == EdgeType.CONTAINS)
        edge_candidates = (*flow_edges, *code_data_edges, *structural_edges)
    else:
        edge_candidates = (*semantic_edges, *code_data_edges)
    if selected_context_edges:
        edge_candidates = tuple(sorted(
            edge_candidates,
            key=lambda edge: (frozenset((edge.source, edge.target))
                              not in selected_context_edges),
        ))
    edges = tuple(edge_candidates[:request.max_edges])
    legend = tuple(VisibleLegendItem(kind, *_LEGEND[kind])
                   for kind in _LEGEND if any(edge.type == kind for edge in edges))
    shown_roots = tuple(root for root in roots if root in visible_ids)
    positions = layout_positions(nodes, edges, shown_roots, layout_kind, state)
    return VisibleGraph(
        request.view_kind, nodes, shown_roots, edges,
        aggregations=aggregations, layout_kind=layout_kind, layout_hints=positions,
        legend=legend,
        hidden_counts={"nodes": len(reachable - visible_ids),
                       "edges": len(eligible_relations) + len(data_slice.edges) - len(edges)},
        query_context={"root_id": request.root_id, "selected_id": request.selected_id,
                       "hops": request.hops, "has_more": bool(reachable - visible_ids),
                       "budget_truncated": bool(hidden_candidate_ids)},
        max_nodes=request.max_nodes, max_edges=request.max_edges,
    )
