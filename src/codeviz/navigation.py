"""Choose an honest graph target for a search result."""

from dataclasses import dataclass

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import ViewKind, VisibleGraph
from codeviz.views.query import GraphQuery

from .search import SearchResult


@dataclass(frozen=True, slots=True)
class RevealTarget:
    view_kind: ViewKind | None
    root_id: str | None
    selected_id: str | None
    reason: str | None = None


def _preferred_reveal(analysis: ProjectAnalysis, result: SearchResult) -> RevealTarget:
    if result.source_kind == "semantic":
        graph = analysis.semantic.graph if analysis.semantic is not None else None
        components = {item.id: item for item in graph.components} if graph else {}
        component = components.get(result.id)
        if component is None:
            return RevealTarget(None, None, None, "Semantic component is no longer available")
        if component.role == "ModelDefinition":
            return RevealTarget(ViewKind.ARCHITECTURE, result.id, result.id)
        entry_id = component.metadata.get("entry_point_id")
        if component.role == "EntryPoint":
            entry_id = result.id
        if isinstance(entry_id, str):
            return RevealTarget(ViewKind.EXECUTION, entry_id, result.id)
        if graph is not None:
            query = GraphQuery(graph)
            model_roots = frozenset(root for root in graph.roots
                                    if query.components[root].role == "ModelDefinition")
            path = query.path_to_root(result.id, model_roots)
            if path:
                return RevealTarget(ViewKind.ARCHITECTURE, path[0], result.id)
        return RevealTarget(None, None, None, "No supported graph view for this semantic component")
    entry = analysis.symbol_index.entries.get(result.id)
    if entry is None:
        return RevealTarget(None, None, None, "Symbol definition is no longer available")
    if analysis.model is not None and any(
        item.symbol_id == result.id for item in analysis.model.definitions
    ):
        return RevealTarget(ViewKind.ARCHITECTURE, result.id, result.id)
    if analysis.training is not None and any(
        item.id == result.id for item in analysis.training.entry_points
    ):
        return RevealTarget(ViewKind.EXECUTION, result.id, result.id)
    if analysis.flow is None or result.id not in analysis.flow.graph.nodes:
        return RevealTarget(None, None, None, "Code graph has no node for this symbol")
    for node in analysis.flow.graph.nodes.values():
        if node.metadata.get("symbol_id") == result.id:
            return RevealTarget(ViewKind.DATA_FLOW, node.id, node.id)
    return RevealTarget(ViewKind.SYMBOL, result.id, result.id)


def plan_reveal(analysis: ProjectAnalysis, result: SearchResult,
                current_view: VisibleGraph | None = None) -> RevealTarget:
    """Keep a target in place only when the current view has its required semantics."""
    preferred = _preferred_reveal(analysis, result)
    if (current_view is not None and current_view.view_kind == preferred.view_kind
            and any(node.id == preferred.selected_id for node in current_view.nodes)):
        root = current_view.query_context.get("root_id")
        return RevealTarget(current_view.view_kind, root if isinstance(root, str) else None,
                            preferred.selected_id)
    return preferred
