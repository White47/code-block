"""Bounded projection of existing CFG and callpoints for one direct scope."""

from typing import TYPE_CHECKING

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    EdgeType,
    NodeKind,
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

_SCOPES = frozenset({NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD})
_STEPS = frozenset({NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK})
_FLOW = frozenset({EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
                   EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK})
_STYLES = {
    EdgeType.CONTROL_FLOW: ("Control Flow", "control-solid"),
    EdgeType.BRANCH_TRUE: ("True Branch", "branch-true"),
    EdgeType.BRANCH_FALSE: ("False Branch", "branch-false"),
    EdgeType.LOOP_BACK: ("Loop", "loop-curved"),
    EdgeType.CONTAINS: ("Callpoint in scope / statement", "structural-dotted"),
}


def scope_node_ids(analysis: ProjectAnalysis, root_id: str) -> tuple[str, ...]:
    """Never traverse arbitrary CONTAINS: nested definitions are separate scopes."""
    if analysis.flow is None:
        return ()
    graph = analysis.flow.graph
    steps = {edge.target for edge in graph.edges
             if edge.source == root_id and edge.type == EdgeType.CONTAINS
             and graph.nodes[edge.target].kind in _STEPS}
    return (root_id, *(node.id for node in graph.nodes.values()
                       if node.id in steps or (node.kind == NodeKind.CALL_SITE
                                              and node.metadata.get("scope_symbol_id") == root_id)))


def build_function_flow(analysis: ProjectAnalysis, request: "ViewRequest",
                        state: ViewState) -> VisibleGraph:
    if analysis.flow is None:
        raise ValueError("code flow analysis is required")
    graph = analysis.flow.graph
    roots = tuple(item.id for item in analysis.symbol_index.entries.values()
                  if item.definition and item.kind in _SCOPES and item.id in graph.nodes)
    root_id = request.root_id
    if root_id is None:
        root_id = next((entry.root_symbol_id for entry in analysis.generic_entries
                        if entry.root_symbol_id in roots), None)
        if root_id is None:
            root_id = next((item for item in roots
                            if graph.nodes[item].kind in (NodeKind.FUNCTION, NodeKind.METHOD)),
                           roots[0] if roots else None)
    if root_id is None:
        return VisibleGraph(request.view_kind, (), max_nodes=request.max_nodes,
                            max_edges=request.max_edges)
    if root_id not in roots:
        raise ValueError("requested root is not available in this view")
    candidates = scope_node_ids(analysis, root_id)
    available = set(candidates)
    aggregate_id = f"aggregate:FunctionFlow:{root_id}"
    aggregate_selected = request.selected_id == aggregate_id and len(candidates) > request.max_nodes
    if (request.selected_id is not None and request.selected_id not in available
            and not aggregate_selected):
        raise ValueError("selected component is outside the requested view")
    priority = ((request.selected_id, root_id)
                if request.selected_id and not aggregate_selected else (root_id,))
    ordered = tuple(dict.fromkeys((*priority, *candidates)))
    reserve = int(aggregate_selected or (
        len(ordered) > request.max_nodes and request.max_nodes > len(set(priority))))
    shown = ordered[:request.max_nodes - reserve]
    hidden = ordered[len(shown):]
    nodes = tuple(VisibleNode(
        node_id, graph.nodes[node_id].name, code_node_ids=(node_id,),
        source_anchors=graph.nodes[node_id].source_anchors,
        code_name=graph.nodes[node_id].qualified_name,
        role=graph.nodes[node_id].kind.value, confidence=graph.nodes[node_id].confidence,
        metadata={**graph.nodes[node_id].metadata, "has_children": False},
    ) for node_id in shown)
    aggregations: tuple[VisibleAggregation, ...] = ()
    if reserve:
        nodes += (VisibleNode(aggregate_id, f"{len(hidden)} more", member_ids=hidden,
                              metadata={"hidden_count": len(hidden)}),)
        aggregations = (VisibleAggregation(aggregate_id, hidden),)
    eligible = tuple(edge for edge in graph.edges
                     if edge.source in available and edge.target in available
                     and (edge.type in _FLOW or
                          (edge.type == EdgeType.CONTAINS
                           and graph.nodes[edge.target].kind == NodeKind.CALL_SITE)))
    visible = set(shown)
    edge_candidates = sorted((edge for edge in eligible if edge.source in visible
                              and edge.target in visible),
                             key=lambda edge: edge.type == EdgeType.CONTAINS)
    edges = tuple(VisibleEdge(
        edge.source, edge.target, edge.type,
        source_anchors=(edge.source_anchor,) if edge.source_anchor else
        graph.nodes[edge.target].source_anchors, confidence=edge.confidence,
    ) for edge in edge_candidates[:request.max_edges])
    issues = [ViewIssue(issue.code, issue.message, request.view_kind)
              for issue in analysis.issues
              if issue.code in ("CFG_PARTIAL", "SCOPE_DECLARATION_CONFLICT")
              and issue.source_anchor is not None
              and any(anchor.file_path == issue.file_path
                      and anchor.start_line <= (issue.source_anchor.end_line
                                                or issue.source_anchor.start_line)
                      and issue.source_anchor.start_line <= (anchor.end_line or anchor.start_line)
                      for anchor in graph.nodes[root_id].source_anchors)]
    shown_roots = (root_id,) if root_id in visible else ()
    positions = layout_positions(nodes, edges, shown_roots, "flowchart", state,
                                 view_kind=request.view_kind, issues=issues)
    return VisibleGraph(
        ViewKind.FUNCTION_FLOW, nodes, shown_roots, edges, aggregations=aggregations,
        layout_kind="flowchart", layout_hints=positions,
        legend=tuple(VisibleLegendItem(kind, *style) for kind, style in _STYLES.items()
                     if any(edge.type == kind for edge in edges)),
        hidden_counts={"nodes": len(hidden), "edges": len(eligible) - len(edges)},
        query_context={"root_id": root_id, "selected_id": request.selected_id,
                       "hops": request.hops, "has_more": bool(hidden),
                       "budget_truncated": bool(hidden) or len(eligible) > len(edges)},
        max_nodes=request.max_nodes, max_edges=request.max_edges, issues=tuple(issues),
    )
