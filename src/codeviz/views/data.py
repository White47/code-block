"""Project critical code-data facts into the owner entry or model forward view."""

from dataclasses import dataclass, field

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import CodeEdge, CodeNode, EdgeType, NodeKind, ViewKind


@dataclass(frozen=True, slots=True)
class CriticalDataSlice:
    nodes: tuple[CodeNode, ...] = ()
    edges: tuple[CodeEdge, ...] = ()
    root_by_node: dict[str, str] = field(default_factory=dict)


def critical_data_slice(analysis: ProjectAnalysis, roots: tuple[str, ...],
                        view_kind: ViewKind) -> CriticalDataSlice:
    if analysis.flow is None:
        return CriticalDataSlice()
    if view_kind == ViewKind.ARCHITECTURE:
        definitions = {definition.symbol_id: definition
                       for definition in analysis.model.definitions} if analysis.model else {}
        owner_to_root = {definitions[root].forward_symbol_id: root for root in roots
                         if root in definitions and definitions[root].forward_symbol_id is not None}
    else:
        owner_to_root = {root: root for root in roots}
    graph = analysis.flow.graph
    selected: list[CodeNode] = []
    root_by_node: dict[str, str] = {}
    for node in graph.nodes.values():
        if node.kind != NodeKind.DATA:
            continue
        symbol_id = node.metadata.get("symbol_id")
        symbol = analysis.symbol_index.entries.get(symbol_id) if isinstance(symbol_id, str) else None
        owner = symbol.parent_symbol if symbol is not None else None
        if owner not in owner_to_root:
            continue
        selected.append(node)
        root_by_node[node.id] = owner_to_root[owner]
    selected_ids = set(root_by_node)
    edges = tuple(edge for edge in graph.edges
                  if edge.type == EdgeType.DATA_FLOW and edge.source in selected_ids
                  and edge.target in selected_ids)
    return CriticalDataSlice(tuple(selected), edges, root_by_node)
