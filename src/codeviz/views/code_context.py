"""Bounded local contexts over the existing full code graph."""

from collections import deque
from dataclasses import replace
from typing import TYPE_CHECKING

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    CodeEdge,
    CodeGraph,
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


def _definition_edges(graph: CodeGraph, focus: str) -> tuple[CodeEdge, ...]:
    """Project static occurrences without reopening killed binding histories.

    Only definitions of the selected binding are folded into its proxy. Other
    bindings retain their occurrence identity, including when the AST is absent.
    """
    binding = graph.nodes[focus].metadata.get("symbol_id")
    selected_definitions = {node.id for node in graph.nodes.values()
                            if binding is not None and node.metadata.get("binding_id") == binding}
    projected: list[CodeEdge] = []
    seen: set[tuple[str, str, EdgeType, object]] = set()
    for edge in graph.edges:
        if edge.type not in _DATA_EDGES:
            continue
        if not edge.metadata.get("generic_data"):
            # Legacy critical summaries remain in the full graph and ML views;
            # the local def-use projection uses the occurrence layer instead.
            continue
        if edge.metadata.get("def_use_projection") == "binding_summary":
            continue
        targets = [edge.target]
        if edge.type == EdgeType.READS:
            definitions = edge.metadata.get("reaching_definition_ids")
            if isinstance(definitions, list) and definitions:
                targets = [item for item in definitions if isinstance(item, str)]
        elif edge.type == EdgeType.WRITES:
            definition = edge.metadata.get("definition_id")
            if isinstance(definition, str):
                targets = [definition]
        for target in targets:
            source = focus if edge.source in selected_definitions else edge.source
            target = focus if target in selected_definitions else target
            key = source, target, edge.type, edge.source_anchor
            if key not in seen:
                seen.add(key)
                projected.append(replace(edge, source=source, target=target))
    return tuple(projected)


def build_code_context(analysis: ProjectAnalysis, request: "ViewRequest",
                       state: ViewState) -> VisibleGraph:
    if analysis.flow is None:
        raise ValueError("code flow analysis is required")
    graph = analysis.flow.graph
    focus = request.selected_id or request.root_id
    if focus is None or focus not in graph.nodes:
        raise ValueError("selected code node is not available")
    if request.view_kind == ViewKind.DATA_FLOW and graph.nodes[focus].kind.value != "Data":
        proxy = next((node.id for node in graph.nodes.values()
                      if node.kind.value == "Data" and node.metadata.get("symbol_id") == focus), None)
        if proxy is not None:
            focus = proxy
    allowed = _DATA_EDGES if request.view_kind == ViewKind.DATA_FLOW else _SYMBOL_EDGES
    if request.view_kind == ViewKind.DATA_FLOW and graph.nodes[focus].kind.value != "Data":
        raise ValueError("data context must focus a data node or mapped lexical binding")
    context_edges = (_definition_edges(graph, focus)
                     if request.view_kind == ViewKind.DATA_FLOW else tuple(graph.edges))
    edges = tuple(edge for edge in context_edges if edge.type in allowed
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
    hidden_edge_count = len(eligible) - len(shown)
    budget_truncated = bool(hidden_ids or hidden_edge_count)
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
        hidden_counts={"nodes": len(hidden_ids), "edges": hidden_edge_count},
        query_context={"root_id": request.root_id, "selected_id": focus,
                       "hops": request.hops, "direction": request.direction,
                       "has_more": budget_truncated, "budget_truncated": budget_truncated},
        max_nodes=request.max_nodes, max_edges=request.max_edges,
        issues=tuple(layout_issues),
    )
