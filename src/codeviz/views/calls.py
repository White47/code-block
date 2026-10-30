"""Source-backed static call records, bounded independently of selection."""

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    CodeEdge,
    CodeGraph,
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


@dataclass(frozen=True)
class _Call:
    caller: str
    edge: CodeEdge
    ownership: tuple[CodeEdge, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.caller, self.edge.source, self.edge.target)))


def _ownership(graph: CodeGraph, caller: str, site: str,
               parents: dict[str, list[CodeEdge]]) -> tuple[CodeEdge, ...]:
    """Accept a direct owner or exactly one anchored flow/control intermediary."""
    incoming = parents.get(site, [])
    if len(incoming) != 1:
        return ()
    site_edge = incoming[0]
    parent = graph.nodes.get(site_edge.source)
    if (parent is None or parent.id != site_edge.source or parent.id == site
            or not parent.source_anchors or site_edge.source_anchor is None):
        return ()
    if parent.id == caller:
        return (site_edge,)
    if parent.kind not in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK):
        return ()
    owner_edges = parents.get(parent.id, [])
    if len(owner_edges) != 1:
        return ()
    owner_edge = owner_edges[0]
    if owner_edge.source != caller or owner_edge.source_anchor is None:
        return ()
    return owner_edge, site_edge


def _call_edge(record: _Call) -> VisibleEdge:
    edge = record.edge
    assert edge.source_anchor is not None
    return VisibleEdge(edge.source, edge.target, EdgeType.CALLS,
                       source_anchors=(edge.source_anchor,), confidence=edge.confidence)


def _owner_edge(record: _Call) -> VisibleEdge:
    anchors = tuple(edge.source_anchor for edge in record.ownership
                    if edge.source_anchor is not None)
    return VisibleEdge(record.caller, record.edge.source, EdgeType.CONTAINS,
                       source_anchors=anchors, confidence=record.ownership[-1].confidence)


def _edge_key(edge: VisibleEdge) -> tuple[object, ...]:
    return edge.source, edge.target, edge.type, edge.confidence, edge.source_anchors


def _records(graph: CodeGraph) -> tuple[tuple[_Call, ...], int]:
    parents: dict[str, list[CodeEdge]] = {}
    for edge in graph.edges:
        if edge.type is EdgeType.CONTAINS:
            existing = parents.setdefault(edge.target, [])
            if edge not in existing:
                existing.append(edge)
    records: list[_Call] = []
    seen: set[tuple[object, ...]] = set()
    rejected = 0
    for edge in graph.edges:
        if edge.type is not EdgeType.CALLS:
            continue
        site, target = graph.nodes.get(edge.source), graph.nodes.get(edge.target)
        caller_id = site.metadata.get("scope_symbol_id") if site else None
        caller = graph.nodes.get(caller_id) if isinstance(caller_id, str) else None
        if (site is None or site.id != edge.source or target is None or target.id != edge.target
                or caller is None or caller.id != caller_id or site.kind is not NodeKind.CALL_SITE
                or caller.kind not in
                (NodeKind.MODULE, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.LEXICAL_SCOPE)
                or not all(n.source_anchors for n in (site, target, caller))
                or edge.source_anchor is None or site.source_anchors != (edge.source_anchor,)):
            rejected += 1
            continue
        ownership = _ownership(graph, caller.id, site.id, parents)
        if not ownership:
            rejected += 1
            continue
        key = edge.source, edge.target, edge.confidence, edge.source_anchor
        if key not in seen:
            seen.add(key)
            records.append(_Call(caller.id, edge, ownership))
    records.sort(key=lambda r: (graph.nodes[r.edge.source].source_anchors[0].file_path,
                               graph.nodes[r.edge.source].source_anchors[0].start_line,
                               graph.nodes[r.edge.source].source_anchors[0].start_column or 0,
                               r.edge.source, r.edge.target, r.edge.confidence.value))
    return tuple(records), rejected


def build_calls_view(analysis: ProjectAnalysis, request: "ViewRequest",
                     state: ViewState) -> VisibleGraph:
    if analysis.flow is None:
        raise ValueError("code flow analysis is required")
    if not 1 <= request.hops <= 5:
        raise ValueError("Calls hops must be between 1 and 5")
    graph = analysis.flow.graph
    records, rejected = _records(graph)
    identities = {item for record in records for item in record.ids}
    if request.root_id is not None and request.root_id not in identities:
        raise ValueError("requested root is not available in this view")
    record_paths: dict[int, tuple[int, int | None]] = {}
    if request.root_id is None:
        eligible_indices = tuple(range(len(records)))
    else:
        pending = deque(((request.root_id, 0),))
        predecessors: dict[str, int | None] = {request.root_id: None}
        while pending:
            current, distance = pending.popleft()
            if distance >= request.hops:
                continue
            for index, record in enumerate(records):
                local = current in record.ids and current not in (record.caller, record.edge.target)
                down = request.direction != "upstream" and current == record.caller
                up = request.direction != "downstream" and current == record.edge.target
                if not (local or down or up):
                    continue
                record_paths.setdefault(index, (distance + 1, predecessors[current]))
                neighbors = ((record.caller, record.edge.target) if local else
                             (record.edge.target,) if down else (record.caller,))
                for neighbor in neighbors:
                    if neighbor not in predecessors:
                        predecessors[neighbor] = index
                        pending.append((neighbor, distance + 1))
        eligible_indices = tuple(sorted(record_paths, key=lambda index: (record_paths[index][0], index)))
    eligible = tuple(records[index] for index in eligible_indices)
    candidates = tuple(dict.fromkeys(item for record in eligible for item in record.ids))
    all_edges: dict[tuple[object, ...], VisibleEdge] = {}
    for record in eligible:
        edge = _call_edge(record)
        all_edges[_edge_key(edge)] = edge
    for record in eligible:
        edge = _owner_edge(record)
        all_edges[_edge_key(edge)] = edge
    max_nodes, max_edges = min(request.max_nodes, 100), min(request.max_edges, 300)

    def choose(limit: int) -> tuple[dict[str, None], dict[tuple[object, ...], VisibleEdge]]:
        ids: dict[str, None] = dict.fromkeys((request.root_id,) if request.root_id else ())
        selected: list[_Call] = []
        selected_indices: set[int] = set()
        call_edges: dict[tuple[object, ...], VisibleEdge] = {}
        for index in eligible_indices:
            predecessor = record_paths.get(index, (0, None))[1]
            if predecessor is not None and predecessor not in selected_indices:
                continue
            record = records[index]
            extra = set(record.ids) - ids.keys()
            call_edge = _call_edge(record)
            call_key = _edge_key(call_edge)
            if (len(ids) + len(extra) > limit
                    or len(call_edges.keys() | {call_key}) > max_edges):
                continue
            ids.update(dict.fromkeys(record.ids))
            selected.append(record)
            selected_indices.add(index)
            call_edges[call_key] = call_edge
        edges = dict(call_edges)
        for record in selected:
            owner_edge = _owner_edge(record)
            owner_key = _edge_key(owner_edge)
            if owner_key not in edges and len(edges) < max_edges:
                edges[owner_key] = owner_edge
        return ids, edges

    ids, edges = choose(max_nodes)
    hidden = tuple(item for item in candidates if item not in ids)
    if (hidden or len(all_edges) > len(edges)) and max_nodes > bool(request.root_id):
        ids, edges = choose(max_nodes - 1)
        hidden = tuple(item for item in candidates if item not in ids)
    nodes = tuple(VisibleNode(
        item, graph.nodes[item].name, code_node_ids=(item,),
        source_anchors=graph.nodes[item].source_anchors,
        code_name=graph.nodes[item].qualified_name, role=graph.nodes[item].kind.value,
        confidence=graph.nodes[item].confidence, metadata=dict(graph.nodes[item].metadata),
    ) for item in ids)
    aggregations: tuple[VisibleAggregation, ...] = ()
    omitted_calls = tuple(dict.fromkeys(
        record.edge.source for record in eligible
        if (_edge_key(_call_edge(record)) not in edges
            or _edge_key(_owner_edge(record)) not in edges)
    ))
    if (hidden or omitted_calls) and len(nodes) < max_nodes:
        context = repr((request.root_id, request.direction, request.hops, max_nodes, max_edges))
        aggregate_id = 'aggregate:Calls:' + sha256(context.encode()).hexdigest()[:24]
        sample = tuple(dict.fromkeys(hidden or omitted_calls))[:20]
        hidden_count = len(hidden) if hidden else len(omitted_calls)
        nodes += (VisibleNode(aggregate_id, f"{hidden_count} more", member_ids=sample,
                              metadata={"hidden_count": hidden_count}),)
        aggregations = (VisibleAggregation(aggregate_id, sample),)
    if request.selected_id is not None and request.selected_id not in {n.id for n in nodes}:
        raise ValueError("selected component is outside the requested view")
    visible_edges = tuple(edges.values())
    issues = ([ViewIssue("CALLS_TRACEABILITY_UNAVAILABLE",
                         f"Skipped {rejected} calls without verified static ownership or anchors",
                         ViewKind.CALLS)] if rejected else [])
    roots = (request.root_id,) if request.root_id else ()
    positions = layout_positions(nodes, visible_edges, roots, "directed", state,
                                 view_kind=ViewKind.CALLS, issues=issues)
    truncated = bool(hidden or len(all_edges) > len(edges))
    return VisibleGraph(
        ViewKind.CALLS, nodes, roots, visible_edges, aggregations=aggregations,
        layout_kind="directed", layout_hints=positions,
        legend=tuple(VisibleLegendItem(kind, label, style) for kind, label, style in (
            (EdgeType.CALLS, "Calls", "calls-dashed"),
            (EdgeType.CONTAINS, "Contains", "structural-dotted"))
            if any(edge.type is kind for edge in visible_edges)),
        hidden_counts={"nodes": len(hidden), "edges": len(all_edges) - len(edges)},
        query_context={"root_id": request.root_id, "selected_id": request.selected_id,
                       "hops": request.hops, "direction": request.direction,
                       "control_mode": "relations", "structure_depth": request.structure_depth,
                       "can_expand": False, "depth_limit": False,
                       "has_more": truncated, "budget_truncated": truncated},
        max_nodes=max_nodes, max_edges=max_edges, issues=tuple(issues),
    )
