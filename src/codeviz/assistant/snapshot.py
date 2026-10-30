"""Bounded, detached projections of trusted analysis facts; no source access."""

import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import NoReturn, TypeVar

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import (
    Confidence,
    EdgeType,
    EntityRole,
    NodeKind,
    NodeOrigin,
    SourceAnchor,
)

from .contracts import AgentError, RequestScope

_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HASH = re.compile(r"[0-9a-f]{64}")
MAX_SEQUENCE_ENTRIES = 200_000


def _invalid() -> NoReturn:
    raise AgentError("invalid_input") from None


def _text(value: str) -> None:
    if type(value) is not str:
        _invalid()
    for char in value:
        if 0xD800 <= ord(char) <= 0xDFFF:
            _invalid()


def _id(value: str) -> None:
    _text(value)
    if not value or any(unicodedata.category(c) == "Cc" for c in value):
        _invalid()


def _integer(value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        _invalid()


def _scope(epoch: str, generation: int) -> None:
    _text(epoch)
    if _OPAQUE.fullmatch(epoch) is None:
        _invalid()
    _integer(generation)
    if generation > 2**63 - 1:
        _invalid()


def _path(value: str) -> str:
    _text(value)
    if not value or "\x00" in value or PureWindowsPath(value).drive:
        _invalid()
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        _invalid()
    return path.as_posix()


def _tuple(value: tuple[object, ...]) -> None:
    if type(value) is not tuple:
        _invalid()


def _texts(value: tuple[str, ...], *, ids: bool = False) -> None:
    _tuple(value)
    if len(value) > MAX_SEQUENCE_ENTRIES:
        raise AgentError("budget_exceeded") from None
    for item in value:
        if ids:
            _id(item)
        else:
            _text(item)


def _optional_text(value: str | None, *, identifier: bool = False) -> None:
    if value is not None:
        if identifier:
            _id(value)
        else:
            _text(value)


def _enum(value: object, enum: type[object]) -> None:
    if type(value) is not enum:
        _invalid()


@dataclass(frozen=True, slots=True)
class SnapshotBinding:
    project_root: str
    ignore_paths: tuple[str, ...]
    project_epoch: str
    analysis_generation: int

    def __post_init__(self) -> None:
        _text(self.project_root)
        root = (PureWindowsPath(self.project_root) if os.name == "nt"
                else PurePosixPath(self.project_root))
        if "\x00" in self.project_root or not root.is_absolute():
            _invalid()
        _texts(self.ignore_paths)
        for path in self.ignore_paths:
            if _path(path) != path or any(c in path for c in "*?[]"):
                _invalid()
        if len(set(self.ignore_paths)) != len(self.ignore_paths):
            _invalid()
        _scope(self.project_epoch, self.analysis_generation)


@dataclass(frozen=True, slots=True)
class SnapshotLimits:
    max_records: int = 200000
    max_anchors: int = 200000
    max_text_bytes: int = 16777216

    def __post_init__(self) -> None:
        _integer(self.max_records)
        _integer(self.max_anchors)
        _integer(self.max_text_bytes)


@dataclass(frozen=True, slots=True)
class FileRecord:
    relative_path: str
    module_name: str
    line_count: int
    was_parsed: bool
    content_hash: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _path(self.relative_path))
        _text(self.module_name)
        if type(self.was_parsed) is not bool:
            _invalid()
        _integer(self.line_count, 1 if self.was_parsed else 0)
        _optional_text(self.content_hash)
        if self.content_hash is not None and _HASH.fullmatch(self.content_hash) is None:
            _invalid()


@dataclass(frozen=True, slots=True)
class AnchorRecord:
    file_path: str
    start_line: int
    start_column: int | None
    end_line: int
    end_column: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", _path(self.file_path))
        _integer(self.start_line, 1)
        _integer(self.end_line, self.start_line)
        for col in (self.start_column, self.end_column):
            if col is not None:
                _integer(col)
        if (self.start_line == self.end_line and self.start_column is not None
                and self.end_column is not None and self.end_column < self.start_column):
            _invalid()


def _anchors(value: tuple[AnchorRecord, ...]) -> None:
    _tuple(value)
    for anchor in value:
        if type(anchor) is not AnchorRecord:
            _invalid()
        anchor.__post_init__()


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    id: str
    name: str
    qualified_name: str
    kind: NodeKind
    scope: str
    file_path: str
    source_anchor: AnchorRecord
    parent_symbol: str | None
    definition: bool

    def __post_init__(self) -> None:
        _id(self.id)
        _text(self.name)
        _text(self.qualified_name)
        _enum(self.kind, NodeKind)
        _text(self.scope)
        object.__setattr__(self, "file_path", _path(self.file_path))
        _anchors((self.source_anchor,))
        if self.source_anchor.file_path != self.file_path:
            _invalid()
        _optional_text(self.parent_symbol, identifier=True)
        if type(self.definition) is not bool:
            _invalid()


@dataclass(frozen=True, slots=True)
class CodeNodeRecord:
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    scope: str
    language: str
    source_anchors: tuple[AnchorRecord, ...]
    origin: NodeOrigin
    role: EntityRole
    confidence: Confidence
    file_path: str | None

    def __post_init__(self) -> None:
        _id(self.id)
        _enum(self.kind, NodeKind)
        for text in (self.name, self.qualified_name, self.scope, self.language):
            _text(text)
        if not self.name or not self.language:
            _invalid()
        _anchors(self.source_anchors)
        _enum(self.origin, NodeOrigin)
        _enum(self.role, EntityRole)
        _enum(self.confidence, Confidence)
        if self.file_path is not None:
            object.__setattr__(self, "file_path", _path(self.file_path))
        if self.origin is NodeOrigin.SOURCE and not self.source_anchors:
            _invalid()
        if any(a.file_path != self.file_path for a in self.source_anchors):
            _invalid()


@dataclass(frozen=True, slots=True)
class CodeEdgeRecord:
    original_index: int
    source: str
    target: str
    type: EdgeType
    confidence: Confidence
    source_anchor: AnchorRecord | None
    possible_targets: tuple[str, ...]

    def __post_init__(self) -> None:
        _integer(self.original_index)
        _id(self.source)
        _id(self.target)
        _enum(self.type, EdgeType)
        _enum(self.confidence, Confidence)
        if self.source_anchor is not None:
            _anchors((self.source_anchor,))
        _texts(self.possible_targets, ids=True)
        if self.possible_targets and (self.type is not EdgeType.CALLS
                                      or self.confidence is Confidence.EXACT):
            _invalid()


@dataclass(frozen=True, slots=True)
class ComponentRecord:
    id: str
    display_name: str
    code_name: str | None
    role: str | None
    confidence: Confidence
    evidence: tuple[str, ...]
    child_component_ids: tuple[str, ...]
    code_symbol_ids: tuple[str, ...]
    source_anchors: tuple[AnchorRecord, ...]

    def __post_init__(self) -> None:
        _id(self.id)
        _text(self.display_name)
        _optional_text(self.code_name)
        _optional_text(self.role)
        _enum(self.confidence, Confidence)
        _texts(self.evidence)
        _texts(self.child_component_ids, ids=True)
        _texts(self.code_symbol_ids, ids=True)
        _anchors(self.source_anchors)
        if not (self.child_component_ids or self.code_symbol_ids or self.source_anchors):
            _invalid()


@dataclass(frozen=True, slots=True)
class RelationRecord:
    original_index: int
    source: str
    target: str
    type: EdgeType
    confidence: Confidence
    evidence: tuple[str, ...]
    source_anchors: tuple[AnchorRecord, ...]

    def __post_init__(self) -> None:
        _integer(self.original_index)
        _id(self.source)
        _id(self.target)
        _enum(self.type, EdgeType)
        _enum(self.confidence, Confidence)
        _texts(self.evidence)
        _anchors(self.source_anchors)
        if not self.evidence or not self.source_anchors:
            _invalid()
        if self.type is EdgeType.GRADIENT_FLOW and self.confidence is Confidence.EXACT:
            _invalid()


def _limit(value: int) -> None:
    _integer(value)
    if value > 1000:
        _invalid()


_T = TypeVar("_T")


def _lookup(records: tuple[_T, ...], key: str, attribute: str) -> _T | None:
    return next((r for r in records if getattr(r, attribute) == key), None)


_IncidentRecord = TypeVar("_IncidentRecord", CodeEdgeRecord, RelationRecord)


def _incident_index(
    records: tuple[_IncidentRecord, ...],
) -> Mapping[str, tuple[_IncidentRecord, ...]]:
    incident: dict[str, list[_IncidentRecord]] = {}
    for record in records:
        incident.setdefault(record.source, []).append(record)
        if record.target != record.source:
            incident.setdefault(record.target, []).append(record)
    return MappingProxyType({key: tuple(values) for key, values in incident.items()})


@dataclass(frozen=True, slots=True, init=False)
class AnalysisSnapshot:
    binding: SnapshotBinding
    semantic_roots: tuple[str, ...]
    _files: tuple[FileRecord, ...]
    _symbols: tuple[SymbolRecord, ...]
    _nodes: tuple[CodeNodeRecord, ...]
    _edges: tuple[CodeEdgeRecord, ...]
    _components: tuple[ComponentRecord, ...]
    _relations: tuple[RelationRecord, ...]
    _incident_edges: Mapping[str, tuple[CodeEdgeRecord, ...]]
    _incident_relations: Mapping[str, tuple[RelationRecord, ...]]

    def __init__(self) -> None:
        _invalid()

    @property
    def file_inventory(self) -> tuple[FileRecord, ...]:
        """Complete immutable inventory for local verification, without I/O."""
        return self._files

    @property
    def symbol_inventory(self) -> tuple[SymbolRecord, ...]:
        return self._symbols

    @property
    def code_node_inventory(self) -> tuple[CodeNodeRecord, ...]:
        return self._nodes

    @property
    def component_inventory(self) -> tuple[ComponentRecord, ...]:
        return self._components

    @property
    def code_edge_inventory(self) -> tuple[CodeEdgeRecord, ...]:
        return self._edges

    @property
    def relation_inventory(self) -> tuple[RelationRecord, ...]:
        return self._relations

    def incident_code_edges(self, node_id: str) -> tuple[CodeEdgeRecord, ...]:
        _id(node_id)
        if len(node_id) > 512:
            _invalid()
        return self._incident_edges.get(node_id, ())

    def incident_relations(self, component_id: str) -> tuple[RelationRecord, ...]:
        _id(component_id)
        if len(component_id) > 512:
            _invalid()
        return self._incident_relations.get(component_id, ())

    def get_file(self, relative_path: str) -> FileRecord | None:
        return _lookup(self._files, _path(relative_path), "relative_path")

    def get_symbol(self, id: str) -> SymbolRecord | None:
        _id(id)
        return _lookup(self._symbols, id, "id")

    def get_code_node(self, id: str) -> CodeNodeRecord | None:
        _id(id)
        return _lookup(self._nodes, id, "id")

    def get_component(self, id: str) -> ComponentRecord | None:
        _id(id)
        return _lookup(self._components, id, "id")

    def files(self, *, limit: int = 100) -> tuple[FileRecord, ...]:
        _limit(limit)
        return self._files[:limit]

    def symbols(self, *, limit: int = 100) -> tuple[SymbolRecord, ...]:
        _limit(limit)
        return self._symbols[:limit]

    def code_nodes(self, *, limit: int = 100) -> tuple[CodeNodeRecord, ...]:
        _limit(limit)
        return self._nodes[:limit]

    def components(self, *, limit: int = 100) -> tuple[ComponentRecord, ...]:
        _limit(limit)
        return self._components[:limit]

    def code_edges(self, *, node_id: str | None = None,
                   limit: int = 100) -> tuple[CodeEdgeRecord, ...]:
        _limit(limit)
        if node_id is not None:
            _id(node_id)
        return tuple(islice((e for e in self._edges if node_id is None
                             or node_id in (e.source, e.target)), limit))

    def relations(self, *, component_id: str | None = None,
                  limit: int = 100) -> tuple[RelationRecord, ...]:
        _limit(limit)
        if component_id is not None:
            _id(component_id)
        return tuple(islice((r for r in self._relations if component_id is None
                             or component_id in (r.source, r.target)), limit))

    def matches_scope(self, scope: RequestScope) -> bool:
        if type(scope) is not RequestScope:
            _invalid()
        try:
            epoch = scope.project_epoch
            generation = scope.analysis_generation
            request_id = scope.request_id
        except AttributeError:
            _invalid()
        _scope(epoch, generation)
        _text(request_id)
        if _OPAQUE.fullmatch(request_id) is None:
            _invalid()
        return (epoch == self.binding.project_epoch
                and generation == self.binding.analysis_generation)


class _Budget:
    """Transient logical counters, never retained by a published snapshot."""

    def __init__(self, limits: SnapshotLimits) -> None:
        self.limits = limits
        self.records = 0
        self.anchors = 0
        self.bytes = 0
        self.sequence_entries = 0

    def record(self) -> None:
        if self.records >= self.limits.max_records:
            raise AgentError("budget_exceeded") from None
        self.records += 1

    def text(self, value: str) -> str:
        if type(value) is not str:
            _invalid()
        for char in value:
            point = ord(char)
            if 0xD800 <= point <= 0xDFFF:
                _invalid()
            size = 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
            if size > self.limits.max_text_bytes - self.bytes:
                raise AgentError("budget_exceeded") from None
            self.bytes += size
        return value

    def optional(self, value: str | None) -> str | None:
        return None if value is None else self.text(value)

    def texts(self, values: tuple[str, ...]) -> tuple[str, ...]:
        _tuple(values)
        # Logical cardinality counts every field appearance, not Python heap usage.
        # Constructors check their field cap without charging this aggregate again.
        if len(values) > MAX_SEQUENCE_ENTRIES - self.sequence_entries:
            raise AgentError("budget_exceeded") from None
        self.sequence_entries += len(values)
        return tuple(self.text(value) for value in values)

    def anchor(self, source: SourceAnchor, inventory: dict[str, FileRecord]) -> AnchorRecord:
        if self.anchors >= self.limits.max_anchors:
            raise AgentError("budget_exceeded") from None
        self.anchors += 1
        if not isinstance(source, SourceAnchor):
            _invalid()
        # SourceAnchor normalizes end_line at construction; do not repair corrupt facts.
        if source.end_line is None:
            _invalid()
        anchor = AnchorRecord(self.text(source.file_path), source.start_line,
                              source.start_column, source.end_line, source.end_column)
        file = inventory.get(anchor.file_path)
        if file is None or not file.was_parsed or anchor.end_line > file.line_count:
            _invalid()
        return anchor

    def anchor_tuple(self, sources: tuple[SourceAnchor, ...],
                     inventory: dict[str, FileRecord]) -> tuple[AnchorRecord, ...]:
        _tuple(sources)
        return tuple(self.anchor(source, inventory) for source in sources)


_DEFAULT_LIMITS = SnapshotLimits()


def build_snapshot(analysis: ProjectAnalysis, *, binding: SnapshotBinding,
                   limits: SnapshotLimits = _DEFAULT_LIMITS) -> AnalysisSnapshot:
    """Copy only whitelisted facts, failing atomically on invalid input or budget."""
    try:
        return _build_snapshot(analysis, binding=binding, limits=limits)
    except AgentError:
        raise
    except Exception:
        raise AgentError("invalid_input") from None


def _build_snapshot(analysis: ProjectAnalysis, *, binding: SnapshotBinding,
                    limits: SnapshotLimits) -> AnalysisSnapshot:
    if (not isinstance(analysis, ProjectAnalysis) or type(binding) is not SnapshotBinding
            or type(limits) is not SnapshotLimits):
        _invalid()
    limits.__post_init__()
    budget = _Budget(limits)
    fresh_binding = SnapshotBinding(budget.text(binding.project_root),
                                    budget.texts(binding.ignore_paths),
                                    budget.text(binding.project_epoch), binding.analysis_generation)
    inventory: dict[str, FileRecord] = {}
    _tuple(analysis.files)
    for source_file in analysis.files:
        budget.record()
        file = FileRecord(budget.text(source_file.relative_path),
                          budget.text(source_file.module_name), source_file.line_count,
                          source_file.was_parsed, budget.optional(source_file.content_hash))
        if file.relative_path in inventory:
            _invalid()
        inventory[file.relative_path] = file

    symbols: dict[str, SymbolRecord] = {}
    for key, symbol in analysis.symbol_index.entries.items():
        budget.record()
        record = SymbolRecord(budget.text(symbol.id), budget.text(symbol.name),
                              budget.text(symbol.qualified_name), symbol.kind,
                              budget.text(symbol.scope), budget.text(symbol.file_path),
                              budget.anchor(symbol.source_anchor, inventory),
                              budget.optional(symbol.parent_symbol), symbol.definition)
        if key != record.id or record.id in symbols:
            _invalid()
        symbols[record.id] = record

    nodes: dict[str, CodeNodeRecord] = {}
    edges: list[CodeEdgeRecord] = []
    if analysis.flow is not None:
        graph = analysis.flow.graph
        for key, node in graph.nodes.items():
            budget.record()
            code_node = CodeNodeRecord(
                budget.text(node.id), node.kind, budget.text(node.name),
                budget.text(node.qualified_name), budget.text(node.scope), budget.text(node.language),
                budget.anchor_tuple(node.source_anchors, inventory), node.origin, node.role,
                node.confidence, budget.optional(node.file_path))
            if key != code_node.id or code_node.id in nodes:
                _invalid()
            nodes[code_node.id] = code_node
        for ordinal, edge in enumerate(graph.edges):
            budget.record()
            code_edge = CodeEdgeRecord(
                ordinal, budget.text(edge.source), budget.text(edge.target), edge.type,
                edge.confidence, None if edge.source_anchor is None
                else budget.anchor(edge.source_anchor, inventory), budget.texts(edge.possible_targets))
            if (code_edge.source not in nodes or code_edge.target not in nodes
                    or any(target not in nodes for target in code_edge.possible_targets)):
                _invalid()
            edges.append(code_edge)

    components: dict[str, ComponentRecord] = {}
    relations: list[RelationRecord] = []
    roots: tuple[str, ...] = ()
    if analysis.semantic is not None:
        semantic = analysis.semantic.graph
        _tuple(semantic.components)
        for component in semantic.components:
            budget.record()
            semantic_component = ComponentRecord(
                budget.text(component.id), budget.text(component.display_name),
                budget.optional(component.code_name), budget.optional(component.role),
                component.confidence, budget.texts(component.evidence),
                budget.texts(component.child_component_ids), budget.texts(component.code_symbol_ids),
                budget.anchor_tuple(component.source_anchors, inventory))
            if semantic_component.id in components:
                _invalid()
            components[semantic_component.id] = semantic_component
        if any(child not in components for c in components.values()
               for child in c.child_component_ids):
            _invalid()
        _tuple(semantic.relations)
        for ordinal, relation in enumerate(semantic.relations):
            budget.record()
            semantic_relation = RelationRecord(
                ordinal, budget.text(relation.source), budget.text(relation.target), relation.type,
                relation.confidence, budget.texts(relation.evidence),
                budget.anchor_tuple(relation.source_anchors, inventory))
            if semantic_relation.source not in components or semantic_relation.target not in components:
                _invalid()
            relations.append(semantic_relation)
        roots = budget.texts(semantic.roots)
        _texts(roots, ids=True)
        if any(root not in components for root in roots):
            _invalid()

    edge_inventory = tuple(edges)
    relation_inventory = tuple(relations)
    incident_edges = _incident_index(edge_inventory)
    incident_relations = _incident_index(relation_inventory)
    snapshot = object.__new__(AnalysisSnapshot)
    object.__setattr__(snapshot, "binding", fresh_binding)
    object.__setattr__(snapshot, "semantic_roots", roots)
    object.__setattr__(snapshot, "_files", tuple(inventory[k] for k in sorted(inventory)))
    object.__setattr__(snapshot, "_symbols", tuple(symbols[k] for k in sorted(symbols)))
    object.__setattr__(snapshot, "_nodes", tuple(nodes[k] for k in sorted(nodes)))
    object.__setattr__(snapshot, "_edges", edge_inventory)
    object.__setattr__(snapshot, "_components", tuple(components[k] for k in sorted(components)))
    object.__setattr__(snapshot, "_relations", relation_inventory)
    object.__setattr__(snapshot, "_incident_edges", incident_edges)
    object.__setattr__(snapshot, "_incident_relations", incident_relations)
    return snapshot
