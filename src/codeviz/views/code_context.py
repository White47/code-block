"""Bounded local contexts over the existing full code graph."""

from collections import deque
from typing import TYPE_CHECKING

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    EdgeType,
    ViewIssue,
    ViewKind,
    VisibleAggregation,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)

from .layout import layout_positions
from .state import ViewState

if TYPE_CHECKING:
    from .builder import ViewRequest

_SYMBOL_EDGES = frozenset({EdgeType.CONTAINS, EdgeType.IMPORTS, EdgeType.EXPORTS,
                           EdgeType.CALLS, EdgeType.INHERITS, EdgeType.IMPLEMENTS})
_DATA_EDGES = frozenset({EdgeType.DATA_FLOW, EdgeType.READS, EdgeType.WRITES,
                         EdgeType.PASSES_TO, EdgeType.RETURNS_TO})
_STYLES = {
    EdgeType.CONTAINS: ("Contains", "structural-dotted"),
    EdgeType.CALLS: ("Calls", "calls-dashed"),
    EdgeType.IMPORTS: ("Imports", "calls-dashed"),
    EdgeType.EXPORTS: ("Exports", "calls-dashed"),
    EdgeType.INHERITS: ("Inherits", "structural-dotted"),
    EdgeType.IMPLEMENTS: ("Implements", "structural-dotted"),
    EdgeType.DATA_FLOW: ("Data Flow", "data-solid"),
    EdgeType.READS: ("Reads", "data-dashed"),
    EdgeType.WRITES: ("Writes", "data-dashed"),
    EdgeType.PASSES_TO: ("Passes To", "data-solid"),
    EdgeType.RETURNS_TO: ("Returns To", "data-solid"),
}


def build_code_context(analysis: ProjectAnalysis, request: "ViewRequest",
                       state: ViewState) -> VisibleGraph:
    if analysis.flow is None:
        raise ValueError("code flow analysis is required")
    graph = analysis.flow.graph
    focus = request.selected_id or request.root_id
    if focus is None or focus not in graph.nodes:
        raise ValueError("selected code node is not available")
    allowed = _DATA_EDGES if request.view_kind == ViewKind.DATA_FLOW else _SYMBOL_EDGES
    if request.view_kind == ViewKind.DATA_FLOW and graph.nodes[focus].kind.value != "Data":
        raise ValueError("data context must focus a critical data node")
    edges = tuple(edge for edge in graph.edges if edge.type in allowed
                  and graph.nodes[edge.source].source_anchors
                  and graph.nodes[edge.target].source_anchors)
    outward: dict[str, list[str]] = {}
    inward: dict[str, list[str]] = {}
    for edge in edges:
        source, target = ((edge.target, edge.source) if edge.type == EdgeType.READS
                          else (edge.source, edge.target))
        outward.setdefault(source, []).append(target)
        inward.setdefault(target, []).append(source)
    candidates = [focus]
    seen = {focus}
    frontier = deque(((focus, 0),))
    while frontier:
        current, distance = frontier.popleft()
        if distance >= request.hops:
            continue
        neighbors: list[str] = []
        if request.direction != "upstream":
            neighbors.extend(outward.get(current, ()))
        if request.direction != "downstream":
            neighbors.extend(inward.get(current, ()))
        for neighbor in neighbors:
            if neighbor not in seen:
                seen.add(neighbor)
                candidates.append(neighbor)
                frontier.append((neighbor, distance + 1))
    if len(candidates) > request.max_nodes and request.max_nodes > 1:
        selected_ids = tuple(candidates[:request.max_nodes - 1])
        hidden_ids = tuple(candidates[request.max_nodes - 1:])
    else:
        selected_ids = tuple(candidates[:request.max_nodes])
        hidden_ids = tuple(candidates[request.max_nodes:])
    nodes = tuple(VisibleNode(
        node_id, graph.nodes[node_id].name, code_node_ids=(node_id,),
        source_anchors=graph.nodes[node_id].source_anchors,
        code_name=graph.nodes[node_id].qualified_name,
        role=graph.nodes[node_id].kind.value,
        confidence=graph.nodes[node_id].confidence,
        metadata=dict(graph.nodes[node_id].metadata),
    ) for node_id in selected_ids)
    aggregation: tuple[VisibleAggregation, ...] = ()
    if hidden_ids and len(nodes) < request.max_nodes:
        aggregate_id = f"aggregate:{request.view_kind.value}:{focus}"
        nodes += (VisibleNode(aggregate_id, f"{len(hidden_ids)} more",
                              member_ids=hidden_ids,
                              metadata={"hidden_count": len(hidden_ids)}),)
        aggregation = (VisibleAggregation(aggregate_id, hidden_ids),)
    visible_ids = {node.id for node in nodes}
    eligible = tuple(edge for edge in edges if edge.source in seen and edge.target in seen)
    shown = tuple(edge for edge in eligible if edge.source in visible_ids
                  and edge.target in visible_ids)[:request.max_edges]
    visible_edges = tuple(VisibleEdge(
        edge.target if edge.type == EdgeType.READS else edge.source,
        edge.source if edge.type == EdgeType.READS else edge.target,
        edge.type,
        source_anchors=(edge.source_anchor,) if edge.source_anchor is not None
        else graph.nodes[edge.source].source_anchors,
        confidence=edge.confidence,
    ) for edge in shown)
    legend = tuple(VisibleLegendItem(kind, *_STYLES[kind]) for kind in _STYLES
                   if any(edge.type == kind for edge in shown))
    layout_issues: list[ViewIssue] = []
    layout = layout_positions(
        nodes, visible_edges, (focus,), "directed", state,
        view_kind=request.view_kind, issues=layout_issues)
    return VisibleGraph(
        request.view_kind, nodes, (focus,), visible_edges,
        aggregations=aggregation, layout_kind="directed", layout_hints=layout,
        legend=legend,
        hidden_counts={"nodes": len(hidden_ids), "edges": len(eligible) - len(shown)},
        query_context={"root_id": request.root_id, "selected_id": focus,
                       "hops": request.hops, "direction": request.direction,
                       "has_more": bool(hidden_ids), "budget_truncated": bool(hidden_ids)},
        max_nodes=request.max_nodes, max_edges=request.max_edges,
        issues=tuple(layout_issues),
    )
