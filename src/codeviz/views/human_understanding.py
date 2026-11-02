"""Bounded projection of persisted H1 facts into responsibility cards."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from codeviz.analysis.entries import GenericEntryCandidate
from codeviz.analysis.human_understanding import (
    AuthorStatementFact,
    HumanOperationKind,
    OperationFact,
    has_valid_human_fact_id,
)
from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.symbols import SymbolIndex, SymbolIndexEntry
from codeviz.core import Confidence, EdgeType, NodeKind, SourceAnchor

from .human_summary import FunctionalArchitecture, TaskFlow

ResponsibilityBasis = Literal["source_author_statement", "unknown"]
UnderstandingStatus = Literal["source_backed", "partial", "unknown"]
_OWNER_KINDS = frozenset({
    NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD,
})
_LEXICAL_OWNER_KINDS = _OWNER_KINDS | {NodeKind.LEXICAL_SCOPE}
_CONFIDENCE_STRENGTH = {
    Confidence.UNRESOLVED: 0,
    Confidence.PROBABLE: 1,
    Confidence.EXACT: 2,
}


@dataclass(frozen=True, slots=True)
class OperationFacet:
    kind: HumanOperationKind
    confidence: Confidence
    source_location_count: int
    fact_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HumanOperationKind) or not isinstance(
                self.confidence, Confidence):
            raise ValueError("operation facet requires known kind and confidence")
        if (type(self.source_location_count) is not int or self.source_location_count < 1
                or type(self.fact_ids) is not tuple or not 1 <= len(self.fact_ids) <= 8
                or len(set(self.fact_ids)) != len(self.fact_ids)
                or any(type(fact_id) is not str or not fact_id for fact_id in self.fact_ids)
                or type(self.source_anchors) is not tuple
                or not 1 <= len(self.source_anchors) <= 8
                or len(set(self.source_anchors)) != len(self.source_anchors)
                or self.source_location_count < len(self.source_anchors)):
            raise ValueError("operation facet references must be nonempty and bounded")


@dataclass(frozen=True, slots=True)
class ResponsibilityCard:
    owner_symbol_id: str
    code_name: str
    owner_kind: NodeKind
    responsibility: str | None
    basis: ResponsibilityBasis
    confidence: Confidence
    author_fact_id: str | None
    author_truncated: bool
    operations: tuple[OperationFacet, ...]
    source_anchor: SourceAnchor

    def __post_init__(self) -> None:
        if (type(self.owner_symbol_id) is not str or not self.owner_symbol_id
                or type(self.code_name) is not str or not self.code_name
                or self.owner_kind not in _OWNER_KINDS
                or self.basis not in ("source_author_statement", "unknown")
                or not isinstance(self.confidence, Confidence)
                or type(self.author_truncated) is not bool
                or type(self.operations) is not tuple
                or any(not isinstance(item, OperationFacet) for item in self.operations)
                or len({item.kind for item in self.operations}) != len(self.operations)
                or not isinstance(self.source_anchor, SourceAnchor)):
            raise ValueError("invalid responsibility card")
        if self.basis == "source_author_statement":
            if (type(self.responsibility) is not str or not self.responsibility
                    or self.confidence is not Confidence.PROBABLE
                    or type(self.author_fact_id) is not str or not self.author_fact_id):
                raise ValueError("source author cards require a Probable author statement")
        elif (self.responsibility is not None or self.confidence is not Confidence.UNRESOLVED
              or self.author_fact_id is not None or self.author_truncated):
            raise ValueError("unknown cards cannot invent responsibility")


@dataclass(frozen=True, slots=True)
class HumanUnderstanding:
    status: UnderstandingStatus
    responsibilities: tuple[ResponsibilityCard, ...]
    omitted_responsibility_count: int
    omitted_fact_count: int
    discarded_fact_count: int
    architecture: FunctionalArchitecture | None = None
    flows: tuple[TaskFlow, ...] = ()
    default_flow_id: str | None = None
    omitted_flow_count: int = 0

    def __post_init__(self) -> None:
        if (self.status not in ("source_backed", "partial", "unknown")
                or type(self.responsibilities) is not tuple
                or len(self.responsibilities) > 12
                or any(not isinstance(item, ResponsibilityCard)
                       for item in self.responsibilities)
                or len({item.owner_symbol_id for item in self.responsibilities})
                != len(self.responsibilities)
                or any(type(count) is not int or count < 0 for count in (
                    self.omitted_responsibility_count,
                    self.omitted_fact_count,
                    self.discarded_fact_count,
                    self.omitted_flow_count,
                ))
                or (self.architecture is not None
                    and not isinstance(self.architecture, FunctionalArchitecture))
                or type(self.flows) is not tuple or len(self.flows) > 32
                or any(not isinstance(flow, TaskFlow) for flow in self.flows)
                or len({flow.id for flow in self.flows}) != len(self.flows)
                or (self.default_flow_id is not None
                    and self.default_flow_id not in {flow.id for flow in self.flows})):
            raise ValueError("invalid human-understanding projection")


def _anchor_contains(owner: SourceAnchor, fact: SourceAnchor) -> bool:
    if owner.file_path != fact.file_path or owner.end_line is None or fact.end_line is None:
        return False
    if fact.start_line < owner.start_line or fact.end_line > owner.end_line:
        return False
    if (fact.start_line == owner.start_line and owner.start_column is not None
            and (fact.start_column is None or fact.start_column < owner.start_column)):
        return False
    return not (
        fact.end_line == owner.end_line and owner.end_column is not None
        and (fact.end_column is None or fact.end_column > owner.end_column)
    )


def _is_lexical_ancestor(
    ancestor_id: str,
    descendant: SymbolIndexEntry,
    entries: dict[str, SymbolIndexEntry],
) -> bool:
    current = descendant.parent_symbol
    seen: set[str] = set()
    while current is not None and current in entries and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        current = entries[current].parent_symbol
    return False


def _innermost_lexical_owner(
    index: SymbolIndex,
    anchor: SourceAnchor,
) -> SymbolIndexEntry | None:
    candidates = [
        entry for entry in index.entries.values()
        if entry.kind in _LEXICAL_OWNER_KINDS
        and _anchor_contains(entry.source_anchor, anchor)
    ]
    innermost = [
        candidate for candidate in candidates
        if not any(
            candidate.id != other.id
            and _is_lexical_ancestor(candidate.id, other, index.entries)
            for other in candidates
        )
    ]
    return innermost[0] if len(innermost) == 1 else None


def _valid_owner_fact(
    index: SymbolIndex,
    fact: AuthorStatementFact | OperationFact,
) -> bool:
    owner = index.entries.get(fact.owner_symbol_id)
    if not (
        owner is not None
        and owner.kind in _OWNER_KINDS
        and owner.file_path == fact.source_anchor.file_path
        and _anchor_contains(owner.source_anchor, fact.source_anchor)
        and has_valid_human_fact_id(fact)
    ):
        return False
    if isinstance(fact, OperationFact) and fact.callsite_id is not None:
        references = [
            reference for reference in index.references
            if reference.scope_symbol_id == fact.owner_symbol_id
            and reference.source_anchor.start_line == fact.source_anchor.start_line
            and reference.source_anchor.start_column == fact.source_anchor.start_column
            and _anchor_contains(fact.source_anchor, reference.source_anchor)
        ]
        if len(references) != 1:
            return False
        reference = references[0]
        targets = ((reference.resolved_definition_id,)
                   if reference.resolved_definition_id is not None
                   else reference.possible_target_ids)
        return targets == (fact.target_symbol_id,) and reference.confidence is fact.confidence
    lexical_owner = _innermost_lexical_owner(index, fact.source_anchor)
    return lexical_owner is not None and lexical_owner.id == fact.owner_symbol_id


def _operation_facets(facts: list[OperationFact]) -> tuple[OperationFacet, ...]:
    """Group by kind; mixed evidence exposes its weakest confidence."""
    by_kind: dict[HumanOperationKind, list[OperationFact]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact)
    facets: list[OperationFacet] = []
    for kind, grouped in by_kind.items():
        ordered = sorted(grouped, key=lambda fact: (
            fact.source_anchor.file_path, fact.source_anchor.start_line,
            fact.source_anchor.start_column if fact.source_anchor.start_column is not None else -1,
            fact.id,
        ))
        anchors = tuple(dict.fromkeys(fact.source_anchor for fact in ordered))
        confidence = min(
            (fact.confidence for fact in ordered),
            key=_CONFIDENCE_STRENGTH.__getitem__,
        )
        facets.append(OperationFacet(
            kind, confidence, len(anchors),
            tuple(fact.id for fact in ordered[:8]), anchors[:8],
        ))
    return tuple(sorted(facets, key=lambda facet: facet.kind.value))


def _valid_generic_root(
    analysis: ProjectAnalysis,
    candidate: GenericEntryCandidate,
) -> bool:
    entries = analysis.symbol_index.entries
    root = entries.get(candidate.root_symbol_id)
    if root is None:
        return False
    if candidate.detection_kind == "MainName":
        parent = entries.get(root.parent_symbol or "")
        return (
            root.kind is NodeKind.FUNCTION
            and root.name == "main"
            and parent is not None
            and parent.kind is NodeKind.MODULE
            and candidate.source_anchor == root.source_anchor
        )
    if candidate.detection_kind == "MainModule":
        return (
            root.kind is NodeKind.MODULE
            and PurePosixPath(root.file_path).name == "__main__.py"
            and candidate.source_anchor == root.source_anchor
        )
    if candidate.detection_kind == "MainGuard":
        return _valid_main_guard_root(analysis, candidate, root)
    return False


def _valid_main_guard_root(
    analysis: ProjectAnalysis,
    candidate: GenericEntryCandidate,
    root: SymbolIndexEntry,
) -> bool:
    if analysis.flow is None:
        return False
    modules = [
        entry for entry in analysis.symbol_index.entries.values()
        if entry.kind is NodeKind.MODULE
        and entry.file_path == candidate.source_anchor.file_path
        and _anchor_contains(entry.source_anchor, candidate.source_anchor)
    ]
    if len(modules) != 1:
        return False
    module = modules[0]
    graph = analysis.flow.graph
    guards = [
        node for node in graph.nodes.values()
        if node.kind is NodeKind.CONTROL_BLOCK
        and node.metadata.get("statement_type") == "If"
        and node.metadata.get("condition") in (
            "__name__ == '__main__'", "'__main__' == __name__",
        )
        and node.source_anchors == (candidate.source_anchor,)
        and any(
            edge.source == module.id
            and edge.target == node.id
            and edge.type is EdgeType.CONTAINS
            and edge.source_anchor == candidate.source_anchor
            for edge in graph.edges
        )
    ]
    if len(guards) != 1:
        return False
    if root.id == module.id:
        return True
    if root.kind is not NodeKind.FUNCTION:
        return False
    true_region = {
        edge.target for edge in graph.edges
        if edge.source == guards[0].id and edge.type is EdgeType.BRANCH_TRUE
    }
    traversable = {
        EdgeType.CONTAINS,
        EdgeType.CONTROL_FLOW,
        EdgeType.BRANCH_TRUE,
        EdgeType.BRANCH_FALSE,
        EdgeType.LOOP_BACK,
    }
    successors: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type in traversable:
            successors.setdefault(edge.source, []).append(edge.target)
    region_nodes: set[str] = set()
    pending = list(true_region)
    while pending:
        node_id = pending.pop()
        if node_id in region_nodes:
            continue
        node = graph.nodes.get(node_id)
        if (node is None or not node.source_anchors
                or any(not _anchor_contains(candidate.source_anchor, anchor)
                       for anchor in node.source_anchors)):
            continue
        region_nodes.add(node_id)
        pending.extend(successor for successor in successors.get(node_id, ())
                       if successor not in region_nodes)
    return any(
        node_id in region_nodes
        and graph.nodes.get(node_id) is not None
        and graph.nodes[node_id].kind is NodeKind.CALL_SITE
        and any(
            edge.source == node_id
            and edge.target == root.id
            and edge.type is EdgeType.CALLS
            for edge in graph.edges
        )
        for node_id in region_nodes
    )


def _entry_ranks(analysis: ProjectAnalysis) -> tuple[set[str], set[str]]:
    entries = analysis.symbol_index.entries
    root_ids = {
        candidate.root_symbol_id for candidate in analysis.generic_entries
        if _valid_generic_root(analysis, candidate)
    }
    if analysis.training is not None:
        root_ids.update(
            entry.id for entry in analysis.training.entry_points
            if (owner := entries.get(entry.id)) is not None
            and owner.kind in _OWNER_KINDS
            and entry.source_anchor == owner.source_anchor
        )
    parent_ids: set[str] = set()
    for root_id in root_ids:
        current = entries[root_id].parent_symbol
        seen: set[str] = set()
        while current is not None and current in entries and current not in seen:
            seen.add(current)
            parent = entries[current]
            if parent.kind in (NodeKind.MODULE, NodeKind.CLASS):
                parent_ids.add(parent.id)
            current = parent.parent_symbol
    return root_ids, parent_ids


def _build_human_understanding_base(analysis: ProjectAnalysis) -> HumanUnderstanding:
    """Project persisted facts without reading AST, source, graph edges, or call order."""
    authors_by_owner: dict[str, list[AuthorStatementFact]] = {}
    operations_by_owner: dict[str, list[OperationFact]] = {}
    discarded = 0
    for author_fact in analysis.human_understanding.author_statements:
        if not _valid_owner_fact(analysis.symbol_index, author_fact):
            discarded += 1
            continue
        authors_by_owner.setdefault(author_fact.owner_symbol_id, []).append(author_fact)
    for operation_fact in analysis.human_understanding.operations:
        if not _valid_owner_fact(analysis.symbol_index, operation_fact):
            discarded += 1
            continue
        operations_by_owner.setdefault(operation_fact.owner_symbol_id, []).append(operation_fact)

    selected_authors: dict[str, AuthorStatementFact] = {}
    for owner_id, statements in authors_by_owner.items():
        if len(statements) == 1:
            selected_authors[owner_id] = statements[0]
        else:
            discarded += len(statements)

    cards: list[ResponsibilityCard] = []
    for owner_id in authors_by_owner.keys() | operations_by_owner.keys():
        owner = analysis.symbol_index.entries[owner_id]
        statement = selected_authors.get(owner_id)
        facets = _operation_facets(operations_by_owner.get(owner_id, []))
        cards.append(ResponsibilityCard(
            owner.id, owner.qualified_name, owner.kind,
            statement.text if statement is not None else None,
            "source_author_statement" if statement is not None else "unknown",
            statement.confidence if statement is not None else Confidence.UNRESOLVED,
            statement.id if statement is not None else None,
            statement.truncated if statement is not None else False,
            facets, owner.source_anchor,
        ))
    entry_ids, entry_parent_ids = _entry_ranks(analysis)
    cards.sort(key=lambda card: (
        1 if card.owner_symbol_id in entry_ids else
        2 if card.owner_symbol_id in entry_parent_ids else
        3 if card.owner_symbol_id in selected_authors else 4,
        card.source_anchor.file_path, card.source_anchor.start_line,
        card.source_anchor.start_column if card.source_anchor.start_column is not None else -1,
        card.owner_symbol_id,
    ))
    omitted_responsibilities = max(0, len(cards) - 12)
    useful = any(card.basis == "source_author_statement" or card.operations for card in cards)
    status: UnderstandingStatus = (
        "unknown" if not useful else "partial"
        if (discarded or omitted_responsibilities
            or analysis.human_understanding.omitted_fact_count
            or any(card.basis == "unknown" for card in cards))
        else "source_backed"
    )
    return HumanUnderstanding(
        status, tuple(cards[:12]), omitted_responsibilities,
        analysis.human_understanding.omitted_fact_count, discarded,
    )


def build_human_understanding(analysis: ProjectAnalysis) -> HumanUnderstanding:
    """Build the complete persisted-fact projection, including H3 summaries."""
    from .human_summary import build_human_summary

    return build_human_summary(analysis)
