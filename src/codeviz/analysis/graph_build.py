"""Materialize indexed code symbols into the validated full graph."""

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    EdgeType,
    NodeKind,
    NodeOrigin,
    stable_node_id,
)

from .resolution import ResolutionResult


def build_symbol_graph(resolution: ResolutionResult) -> CodeGraph:
    graph = CodeGraph()
    project_id = stable_node_id(".", "project", NodeKind.PROJECT, "project")
    graph.add_node(CodeNode(
        id=project_id, kind=NodeKind.PROJECT, name="Project",
        qualified_name="project", scope="project", language="python",
        origin=NodeOrigin.SYNTHETIC,
    ))
    for entry in resolution.symbol_index.entries.values():
        graph.add_node(CodeNode(
            id=entry.id, kind=entry.kind, name=entry.name,
            qualified_name=entry.qualified_name, scope=entry.scope,
            language="python", source_anchors=(entry.source_anchor,),
            metadata=dict(entry.metadata),
        ))
    for node in (*resolution.external_symbols, *resolution.unresolved_symbols):
        graph.add_node(node)
    for entry in resolution.symbol_index.entries.values():
        graph.add_edge(CodeEdge(
            source=entry.parent_symbol or project_id, target=entry.id,
            type=EdgeType.CONTAINS, source_anchor=entry.source_anchor,
        ))
    return graph
