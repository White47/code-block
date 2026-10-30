"""Private project sampling and dispatched-wire coverage; no model authority."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from codeviz.core import EdgeType, NodeKind

from .provider import PreparedRequest
from .snapshot import AnalysisSnapshot, AnchorRecord, SymbolRecord
from .source_policy import SourceConsent, is_protected_source_path
from .tool_contracts import LocalRelationsResult, RelationKey

ProjectGap = Literal["representative_sample", "retrieval_gap", "unverified"]


@dataclass(frozen=True, slots=True)
class ProjectCoverage:
    indexed_file_count: int
    authorized_file_count: int
    metadata_files: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()
    gap_codes: tuple[ProjectGap, ...] = ("representative_sample",)

    def with_gap(self, code: ProjectGap) -> "ProjectCoverage":
        return replace(self, gap_codes=tuple(sorted(set(self.gap_codes) | {code})))

    def dispatched(self, prepared: PreparedRequest) -> "ProjectCoverage":
        """Called only after an admitted provider stream completes successfully."""
        return replace(self,
            metadata_files=tuple(sorted(set(self.metadata_files)
                | {item.file for item in prepared.metadata_occurrences})),
            source_files=tuple(sorted(set(self.source_files)
                | {item.file for item in prepared.source_occurrences})))


@dataclass(frozen=True, slots=True)
class ProjectPlan:
    representatives: tuple[SymbolRecord, ...]
    coverage: ProjectCoverage


def plan_project(snapshot: AnalysisSnapshot, consent: SourceConsent,
                 checkpoint: Callable[[], None]) -> ProjectPlan:
    """Choose local candidates only; B5 rediscovery and C5 still gate every byte."""
    if type(snapshot) is not AnalysisSnapshot or type(consent) is not SourceConsent:
        raise ValueError("Invalid project retrieval context.")
    consent.__post_init__()
    if consent.binding != snapshot.binding:
        raise ValueError("Invalid project retrieval context.")
    checkpoint()
    allowed = set(consent.allowed_files)
    files = {file.relative_path for file in snapshot.file_inventory
             if file.was_parsed and file.content_hash is not None}
    authorized = {path for path in files if path in allowed and path.endswith(".py")
                  and not is_protected_source_path(path)
                  and not any(path == ignored or path.startswith(ignored + "/")
                              for ignored in snapshot.binding.ignore_paths)}
    codes = {node.id: node for node in snapshot.code_node_inventory}
    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}
    dependency_lines: dict[str, set[int]] = {}
    for index, edge in enumerate(snapshot.code_edge_inventory):
        if index % 64 == 0:
            checkpoint()
        source, target = codes.get(edge.source), codes.get(edge.target)
        if (source is None or target is None or source.file_path not in authorized
                or target.file_path not in authorized or source.file_path == target.file_path):
            continue
        outgoing[source.file_path] = outgoing.get(source.file_path, 0) + 1
        incoming[target.file_path] = incoming.get(target.file_path, 0) + 1
        anchor = edge.source_anchor
        if anchor is not None and anchor.file_path == source.file_path:
            dependency_lines.setdefault(source.file_path, set()).add(anchor.start_line)
    representatives: dict[str, SymbolRecord] = {}

    def priority(symbol: SymbolRecord) -> tuple[bool, int, str]:
        anchor = symbol.source_anchor
        carries_dependency = any(anchor.start_line <= line <= anchor.end_line
                                 for line in dependency_lines.get(symbol.file_path, ()))
        return (not carries_dependency, anchor.start_line, symbol.id)

    for index, symbol in enumerate(snapshot.symbol_inventory):
        if index % 64 == 0:
            checkpoint()
        if (symbol.file_path not in authorized or not symbol.definition or len(symbol.id) > 512
                or symbol.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)):
            continue
        previous = representatives.get(symbol.file_path)
        if previous is None or priority(symbol) < priority(previous):
            representatives[symbol.file_path] = symbol
    paths = sorted(representatives, key=lambda path: (
        -outgoing.get(path, 0), incoming.get(path, 0), path))[:4]
    checkpoint()
    return ProjectPlan(tuple(representatives[path] for path in paths),
                       ProjectCoverage(len(files), len(authorized)))


def representative_anchor(symbol: SymbolRecord, relations: LocalRelationsResult) -> AnchorRecord:
    """Prefer an actual cross-file edge anchor; do not infer a new relation."""
    nodes = {node.ref: node for node in relations.nodes}
    candidates: list[AnchorRecord] = []
    for edge in relations.relations:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None:
            continue
        source_files = {anchor.file_path for anchor in source.anchors}
        target_files = {anchor.file_path for anchor in target.anchors}
        if not source_files or not target_files or source_files == target_files:
            continue
        candidates.extend(anchor for anchor in edge.anchors
                          if anchor.file_path == symbol.file_path)
    anchor = min(candidates, key=lambda item: (
        item.start_line, item.start_column or 0, item.end_line, item.end_column or 0)) if candidates else symbol.source_anchor
    if anchor.end_line - anchor.start_line > 7:
        return AnchorRecord(anchor.file_path, anchor.start_line, None, anchor.start_line + 7, None)
    return anchor


def cross_file_call_witness(
    relations: LocalRelationsResult, disclosed_spans: tuple[AnchorRecord, ...],
) -> tuple[RelationKey, AnchorRecord] | None:
    """Select one actual returned call and a previously read full caller span."""
    nodes = {node.ref: node for node in relations.nodes}
    for edge in relations.relations:
        if edge.type is not EdgeType.CALLS:
            continue
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None:
            continue
        source_files = {anchor.file_path for anchor in source.anchors}
        target_files = {anchor.file_path for anchor in target.anchors}
        if not source_files or not target_files or not source_files.isdisjoint(target_files):
            continue
        for inner in edge.anchors:
            for outer in disclosed_spans:
                if (outer.file_path == inner.file_path
                        and outer.start_column is not None and outer.end_column is not None
                        and inner.start_column is not None and inner.end_column is not None
                        and (outer.start_line, outer.start_column) <= (inner.start_line, inner.start_column)
                        and (inner.end_line, inner.end_column) <= (outer.end_line, outer.end_column)):
                    return edge.key, outer
    return None
