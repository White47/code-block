"""Bounded, read-only search over definitions and source-backed semantics."""

from dataclasses import dataclass

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import Confidence, SourceAnchor

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "BackwardPass": ("反向传播", "backward", "backward pass", "backpropagation"),
    "ParameterUpdate": ("参数更新", "parameter update", "optimizer step", "update parameters"),
    "ForwardPass": ("前向传播", "forward pass"),
    "LossCalculation": ("损失计算", "loss calculation"),
    "ClearGradients": ("清空梯度", "zero grad"),
    "EpochLoop": ("轮次循环", "epoch loop"),
    "BatchLoop": ("批次循环", "batch loop"),
}


@dataclass(frozen=True, slots=True)
class SearchResult:
    id: str
    source_kind: str
    name: str
    qualified_name: str
    kind: str
    scope: str
    source_anchor: SourceAnchor
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: tuple[SearchResult, ...]
    total_count: int
    truncated: bool


def _rank(query: str, terms: tuple[str, ...]) -> int | None:
    normalized = tuple(term.casefold() for term in terms)
    if any(term == query for term in normalized):
        return 0
    if any(term.startswith(query) for term in normalized):
        return 1
    if any(query in term for term in normalized):
        return 2
    return None


def search_project(analysis: ProjectAnalysis, query: str,
                   *, max_results: int = 100) -> SearchResponse:
    """Search definitions; semantic terms resolve to anchored component IDs."""
    if type(max_results) is not int or max_results < 1:
        raise ValueError("max_results must be positive")
    needle = query.strip().casefold()
    matches: list[tuple[int, SearchResult]] = []
    for entry in analysis.symbol_index.entries.values():
        if not entry.definition:
            continue
        rank = _rank(needle, (entry.name, entry.qualified_name, entry.file_path)) if needle else 0
        if rank is not None:
            matches.append((rank, SearchResult(
                entry.id, "symbol", entry.name, entry.qualified_name,
                entry.kind.value, entry.scope, entry.source_anchor, Confidence.EXACT)))
    if needle and analysis.semantic is not None:
        for component in analysis.semantic.graph.components:
            if not component.source_anchors:
                continue
            terms = (component.display_name, component.code_name or "",
                     component.role or "", *_ROLE_ALIASES.get(component.role or "", ()))
            rank = _rank(needle, terms)
            if rank is not None:
                matches.append((rank, SearchResult(
                    component.id, "semantic", component.display_name,
                    component.code_name or component.display_name,
                    component.role or "SemanticComponent", "semantic",
                    component.source_anchors[0], component.confidence)))
    matches.sort(key=lambda pair: (pair[0], pair[1].source_kind != "symbol",
                                   pair[1].qualified_name.casefold(),
                                   pair[1].source_anchor.file_path,
                                   pair[1].source_anchor.start_line, pair[1].id))
    total = len(matches)
    return SearchResponse(tuple(item for _, item in matches[:max_results]), total,
                          total > max_results)
