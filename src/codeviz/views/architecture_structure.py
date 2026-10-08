"""Bounded, view-only expansion of real model definitions and occurrences."""

from collections import deque
from dataclasses import replace
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

from .data import critical_data_slice
from .layout import layout_positions
from .state import ViewState
from .structure_identity import StructureResolver

if TYPE_CHECKING:
    from .builder import ViewRequest


def build_architecture_structure(analysis: ProjectAnalysis, request: "ViewRequest",
                                 state: ViewState, detail_source_id: str | None = None) -> VisibleGraph:
    resolver = StructureResolver(analysis, request.project_namespace)
    query = resolver.query
    if query is None:
        raise ValueError("semantic analysis is required")
    roots = resolver.roots()
    if request.root_id is not None and request.root_id not in roots:
        raise ValueError("requested root is not available in this view")
    roots = (request.root_id,) if request.root_id else roots
    if request.selected_id is not None and request.selected_id.startswith("aggregate:"):
        graph = build_architecture_structure(analysis, replace(request, selected_id=None), state,
                                              detail_source_id)
        if request.selected_id not in {item.id for item in graph.aggregations}:
            raise ValueError("unknown structural aggregate")
        return replace(graph, query_context={**graph.query_context, "selected_id": request.selected_id})
    detail = (resolver.resolve(detail_source_id, request.root_id)
              if detail_source_id is not None else None)
    data_slice = critical_data_slice(analysis, roots if detail is None else (), ViewKind.ARCHITECTURE)
    data_ids = {node.id for node in data_slice.nodes}
    selected_path: tuple[str, ...] = ()
    if request.selected_id is not None and request.selected_id not in data_ids:
        selected_path = resolver.resolve(request.selected_id, request.root_id).path
    pending: deque[tuple[str, tuple[str, ...], bool]] = deque(
        (root, (root,), False) for root in roots)
    display_roots = roots
    if detail is not None:
        pending = deque(((detail.component.id, detail.path, detail.projected),))
        display_roots = (resolver.occurrence_id(detail.path, detail.projected),)
    base_level = len(detail.path) - 1 if detail is not None else 0
    data_definitions: dict[str, None] = {}
    definitions_with_data = set(critical_data_slice(
        analysis, resolver.roots(), ViewKind.ARCHITECTURE).root_by_node.values())
    # A selected node inside the requested depth remains prioritized under a tight budget.
    if (detail is None and selected_path and len(selected_path) - 1 <= request.structure_depth
            and len(selected_path) > 1 and (len(selected_path) <= request.max_nodes
                                          or (request.max_nodes == 1 and len(selected_path) == 2))):
        identity = resolver.resolve(request.selected_id or "", request.root_id)
        chosen = (identity.component.id, identity.path, identity.projected)
        selected_root = selected_path[0]
        priority: list[tuple[str, tuple[str, ...], bool]] = [(selected_root, (selected_root,), False)]
        projected_path = False
        for index, child in enumerate(selected_path[1:], 1):
            projected_path = projected_path or child not in query.children(selected_path[index - 1])
            priority.append((child, selected_path[:index + 1], projected_path))
        pending = deque((*priority, *((root, (root,), False) for root in roots if root != selected_root)))
        if request.max_nodes == 1:
            pending = deque((chosen, (selected_root, (selected_root,), False)))
    scheduled = {resolver.occurrence_id(path, projected) for _, path, projected in pending}
    nodes: list[VisibleNode] = []
    edges: list[VisibleEdge] = []
    seen: set[str] = set()
    more = False
    expandable_depth_hidden: set[str] = set()
    global_depth_limited = False
    truncated = False
    hidden: dict[str, None] = {}
    depth_hidden = 0
    eligible_edge_count = 0
    instances = {item.id: item for item in analysis.model.instances} if analysis.model else {}
    while pending:
        current, path, projected = pending.popleft()
        node_id = resolver.occurrence_id(path, projected)
        if node_id in seen:
            continue
        if len(nodes) >= request.max_nodes:
            truncated = True
            hidden[node_id] = None
            hidden.update(dict.fromkeys(resolver.occurrence_id(p, projected_flag)
                                        for _, p, projected_flag in pending))
            break
        seen.add(node_id)
        component = query.components[current]
        definition_id = resolver.definition_id(component)
        if detail is not None:
            if definition_id is not None:
                data_definitions[definition_id] = None
        children = resolver.children(current)
        level = len(path) - 1
        repeat = component.metadata.get("repeat_count")
        name = (f"{component.display_name} ×{repeat}"
                if type(repeat) is int and repeat > 1 else component.display_name)
        code_ids = component.code_symbol_ids
        if not code_ids:
            instance = instances.get(current)
            if instance is not None:
                code_ids = (instance.owner_definition_id,)
        definition_name = next((query.components[item].code_name for item in reversed(path)
                                if query.components[item].role == "ModelDefinition"), "")
        if instances:
            instance = instances.get(current)
            if instance is not None and instance.owner_definition_id in query.components:
                definition_name = query.components[instance.owner_definition_id].code_name
        if definition_id is not None:
            definition_name = query.components[definition_id].code_name
        context = query.components[path[0]].code_name or ""
        previous_name = ""
        for item in path[1:-1]:
            segment = query.components[item].display_name
            if previous_name and segment.startswith(previous_name + "["):
                context = context[:-len(previous_name)] + segment
            else:
                context += "." + segment
            previous_name = segment
        semantic_ids: tuple[str, ...] = (current,)
        anchors = component.source_anchors
        if (detail is not None and path == detail.path
                and definition_id is not None and definition_id != current):
            semantic_ids = (current, definition_id)
            anchors = tuple(dict.fromkeys((*anchors, *query.components[definition_id].source_anchors)))
        nodes.append(VisibleNode(
            node_id, name, semantic_component_ids=semantic_ids, code_node_ids=code_ids,
            source_anchors=anchors, code_name=component.code_name,
            role=component.role, confidence=component.confidence,
            metadata={**component.metadata, "has_children": bool(children),
                      "definition_projection": projected, "definition_code_name": definition_name,
                      "instance_context": context, "structure_level": level,
                      "can_open_detail": (level < 5 and bool(children))
                      or definition_id in definitions_with_data},
        ))
        if level >= 5 or (level - base_level >= request.structure_depth
                          and (detail is not None or current not in request.expanded_ids)):
            global_depth_limited = global_depth_limited or (level >= 5 and bool(children))
            if level < 5 and children:
                expandable_depth_hidden.add(node_id)
            more = more or bool(children)
            depth_hidden += len(children)
            continue
        # Enqueue at most the remaining display budget plus one sentinel.
        children = tuple(sorted(children, key=lambda child: child not in selected_path))
        for child in children:
            eligible_edge_count += 1
            child_projected = projected or child not in query.children(current)
            child_path = (*path, child)
            child_id = resolver.occurrence_id(child_path, child_projected)
            if child_id in scheduled:
                if len(edges) < request.max_edges:
                    edges.append(VisibleEdge(node_id, child_id, EdgeType.CONTAINS,
                                             semantic_component_ids=(current, child),
                                             source_anchors=query.components[child].source_anchors,
                                             confidence=query.components[child].confidence))
                else:
                    truncated = True
                continue
            if len(pending) + len(nodes) >= request.max_nodes + 1:
                truncated = True
                hidden[child_id] = None
                continue
            pending.append((child, child_path, child_projected))
            scheduled.add(child_id)
            if len(edges) < request.max_edges:
                edges.append(VisibleEdge(node_id, child_id, EdgeType.CONTAINS,
                                         semantic_component_ids=(current, child),
                                         source_anchors=query.components[child].source_anchors,
                                         confidence=query.components[child].confidence))
            else:
                truncated = True
    if detail is not None:
        data_slice = critical_data_slice(analysis, tuple(data_definitions), ViewKind.ARCHITECTURE)
        data_ids = {node.id for node in data_slice.nodes}
    for node in data_slice.nodes:
        nodes.append(VisibleNode(node.id, node.name, code_node_ids=(node.id,),
                                 source_anchors=node.source_anchors, code_name=node.qualified_name,
                                 role="DataNode", confidence=node.confidence,
                                 metadata={**node.metadata, "can_open_detail": False,
                                           "definition_projection": False, "structure_level": 1}))
    edges.extend(VisibleEdge(edge.source, edge.target, EdgeType.DATA_FLOW,
                             source_anchors=(edge.source_anchor,) if edge.source_anchor else
                             analysis.flow.graph.nodes[edge.source].source_anchors,
                             confidence=edge.confidence)
                 for edge in data_slice.edges if analysis.flow is not None)
    eligible_edge_count += len(data_slice.edges)
    if request.selected_id in data_ids:
        context_ids = {request.selected_id}
        for edge in data_slice.edges:
            if request.selected_id in (edge.source, edge.target):
                context_ids.update((edge.source, edge.target))
        nodes.sort(key=lambda node: (node.id not in display_roots, node.id not in context_ids))
    elif request.max_nodes == 1 and request.selected_id:
        nodes.sort(key=lambda node: node.id != request.selected_id)
    else:
        nodes.sort(key=lambda node: (node.id not in display_roots, node.id != request.selected_id))
    hidden = {node_id: None for node_id in hidden if node_id not in seen}
    aggregations: tuple[VisibleAggregation, ...] = ()
    if hidden or len(nodes) > request.max_nodes:
        truncated = True
        essential = 1 + int(request.selected_id is not None)
        reserve = int(detail is None and request.max_nodes > essential)
        kept = request.max_nodes - reserve
        hidden.update(dict.fromkeys(node.id for node in nodes[kept:]))
        nodes = nodes[:kept]
        if reserve and hidden:
            aggregate_id = (f"aggregate:Architecture:{resolver.namespace}:{request.root_id or 'all'}:"
                            f"{request.structure_depth}:{request.max_nodes}:{request.max_edges}")
            members = tuple(hidden)
            nodes.append(VisibleNode(aggregate_id, f"{len(members)} more", member_ids=members,
                                     metadata={"hidden_count": len(members), "can_open_detail": False}))
            aggregations = (VisibleAggregation(aggregate_id, members),)
    visible = {node.id for node in nodes}
    closed_edges = tuple(edge for edge in edges if edge.source in visible and edge.target in visible)
    truncated = truncated or len(closed_edges) > request.max_edges
    shown_edges = closed_edges[:request.max_edges]
    selected = request.selected_id if request.selected_id in visible else None
    shown_roots = tuple(root for root in display_roots if root in visible)
    issues: list[ViewIssue] = []
    positions = layout_positions(tuple(nodes), shown_edges, shown_roots, "hierarchical", state,
                                 view_kind=ViewKind.ARCHITECTURE, issues=issues)
    return VisibleGraph(
        ViewKind.ARCHITECTURE, tuple(nodes), shown_roots, shown_edges, aggregations=aggregations,
        layout_kind="hierarchical", layout_hints=positions,
        legend=tuple(VisibleLegendItem(kind, label, style) for kind, label, style in (
            (EdgeType.CONTAINS, "Contains", "structural-dotted"),
            (EdgeType.DATA_FLOW, "Data Flow", "data-solid"))
            if any(edge.type == kind for edge in shown_edges)),
        hidden_counts={"nodes": len(hidden) + depth_hidden,
                       "edges": max(0, eligible_edge_count - len(shown_edges))},
        query_context={"root_id": request.root_id, "selected_id": selected, "hops": request.hops,
                       "control_mode": "structure", "structure_depth": request.structure_depth,
                       "can_expand": bool(expandable_depth_hidden & visible) and request.structure_depth < 5,
                       "depth_limit": request.structure_depth == 5 or global_depth_limited,
                       "has_more": more or truncated, "budget_truncated": truncated},
        max_nodes=request.max_nodes, max_edges=request.max_edges, issues=tuple(issues),
    )
