"""Project/module/definition ownership directly from the Symbol Index."""

from collections import deque
from typing import TYPE_CHECKING

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    Confidence,
    EdgeType,
    ViewIssue,
    ViewKind,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)

from .layout import layout_positions
from .state import ViewState
from .structure_identity import StructureResolver

if TYPE_CHECKING:
    from .builder import ViewRequest


def build_project_structure(analysis: ProjectAnalysis, request: "ViewRequest",
                            state: ViewState) -> VisibleGraph:
    resolver = StructureResolver(analysis, request.project_namespace)
    parents = resolver.project_entries()
    root = request.root_id or resolver.project_id
    if not resolver.project_valid(root):
        raise ValueError("requested root is not available in this view")
    if request.selected_id is not None and not resolver.project_valid(request.selected_id, root):
        raise ValueError("selected component is outside the requested view")
    children: dict[str, list[str]] = {}
    for node_id, parent in parents.items():
        children.setdefault(parent, []).append(node_id)
    pending = deque(((root, 0),))
    nodes: list[VisibleNode] = []
    edges: list[VisibleEdge] = []
    more = False
    truncated = False
    while pending:
        current, level = pending.popleft()
        if len(nodes) >= request.max_nodes:
            truncated = True
            break
        members = children.get(current, [])
        if current == resolver.project_id:
            modules = tuple(item for item in children.get(current, []))
            if not modules:
                break
            node = VisibleNode(current, "Project", code_node_ids=modules, member_ids=modules,
                               role="Project", confidence=Confidence.EXACT,
                               metadata={"structure_level": level, "can_open_detail": bool(members),
                                         "has_children": bool(members), "definition_projection": False})
        else:
            entry = analysis.symbol_index.entries[current]
            node = VisibleNode(current, entry.name, code_node_ids=(current,),
                               source_anchors=(entry.source_anchor,), code_name=entry.qualified_name,
                               role=entry.kind.value, confidence=Confidence.EXACT,
                               metadata={"structure_level": level, "has_children": bool(members),
                                         "can_open_detail": bool(members), "definition_projection": False})
        nodes.append(node)
        if level == request.structure_depth:
            more = more or bool(members)
            continue
        for child in members:
            if len(pending) + len(nodes) >= request.max_nodes + 1:
                truncated = True
                break
            pending.append((child, level + 1))
            if len(edges) < request.max_edges:
                edges.append(VisibleEdge(current, child, EdgeType.CONTAINS,
                                         source_anchors=(analysis.symbol_index.entries[child].source_anchor,)))
            else:
                truncated = True
    ids = {node.id for node in nodes}
    shown_edges = tuple(edge for edge in edges if edge.source in ids and edge.target in ids)
    roots = (root,) if root in ids else ()
    issues: list[ViewIssue] = []
    positions = layout_positions(tuple(nodes), shown_edges, roots, "hierarchical", state,
                                 view_kind=ViewKind.PROJECT_STRUCTURE, issues=issues)
    return VisibleGraph(
        ViewKind.PROJECT_STRUCTURE, tuple(nodes), roots, shown_edges,
        layout_kind="hierarchical", layout_hints=positions,
        legend=(VisibleLegendItem(EdgeType.CONTAINS, "Contains", "structural-dotted"),)
        if shown_edges else (),
        hidden_counts={"nodes": int(more or truncated), "edges": int(truncated)},
        query_context={"root_id": request.root_id,
                       "selected_id": request.selected_id if request.selected_id in ids else None,
                       "control_mode": "structure", "structure_depth": request.structure_depth,
                       "can_expand": more and request.structure_depth < 5,
                       "depth_limit": request.structure_depth == 5,
                       "has_more": more or truncated, "budget_truncated": truncated},
        max_nodes=request.max_nodes, max_edges=request.max_edges, issues=tuple(issues),
    )
