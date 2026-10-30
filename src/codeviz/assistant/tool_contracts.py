"""Strict local tool values. Evidence schemas alone confer no authority."""

import unicodedata
from dataclasses import dataclass
from typing import Literal

from codeviz.core import Confidence, EdgeType, NodeOrigin

from .contracts import AgentError, RequestScope
from .snapshot import (
    AnchorRecord,
    CodeEdgeRecord,
    CodeNodeRecord,
    ComponentRecord,
    RelationRecord,
    SnapshotBinding,
    SymbolRecord,
)
from .source_policy import validate_source_path


def _text(value: str, *, minimum: int = 0, maximum: int | None = None) -> None:
    if type(value) is not str or len(value) < minimum:
        raise AgentError()
    if maximum is not None and len(value) > maximum:
        raise AgentError()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AgentError() from None
    if any(unicodedata.category(c) == "Cc" for c in value):
        raise AgentError()


def _int(value: int, minimum: int = 0, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise AgentError()


def _source_body(value: str) -> None:
    """Bound original Unicode source, preserving controls and literal escapes."""
    if type(value) is not str or len(value) > 4000:
        raise AgentError()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AgentError() from None


def _value(value: object, cls: type) -> None:
    if type(value) is not cls:
        raise AgentError()
    validator = getattr(value, "__post_init__", None)
    if validator is not None:
        validator()


def _items(values: tuple[object, ...], cls: type) -> None:
    if type(values) is not tuple:
        raise AgentError()
    for value in values:
        _value(value, cls)


def _literal(value: str, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        raise AgentError()


@dataclass(frozen=True, slots=True)
class EntityRef:
    kind: Literal["symbol", "code", "semantic"]
    id: str

    def __post_init__(self) -> None:
        _literal(self.kind, ("symbol", "code", "semantic"))
        _text(self.id, minimum=1, maximum=512)


@dataclass(frozen=True, slots=True)
class RelationKey:
    collection_kind: Literal["code", "semantic"]
    original_index: int

    def __post_init__(self) -> None:
        _literal(self.collection_kind, ("code", "semantic"))
        _int(self.original_index)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    limit: int = 20

    def __post_init__(self) -> None:
        _text(self.query, maximum=2000)
        _int(self.limit, 1, 20)


@dataclass(frozen=True, slots=True)
class RelationsRequest:
    seed: EntityRef
    hops: int = 2
    max_nodes: int = 50
    max_edges: int = 100

    def __post_init__(self) -> None:
        _value(self.seed, EntityRef)
        if self.seed.kind == "symbol":
            raise AgentError()
        _int(self.hops, 0, 2)
        _int(self.max_nodes, 1, 50)
        _int(self.max_edges, 1, 100)


@dataclass(frozen=True, slots=True)
class SymbolHit:
    ref: EntityRef
    name: str
    qualified_name: str
    kind: str
    scope: str
    anchor: AnchorRecord
    confidence: Confidence
    rank: int

    def __post_init__(self) -> None:
        _value(self.ref, EntityRef)
        for text in (self.name, self.qualified_name, self.kind, self.scope):
            _text(text)
        _value(self.anchor, AnchorRecord)
        _value(self.confidence, Confidence)
        _int(self.rank, 0, 2)


@dataclass(frozen=True, slots=True)
class SymbolSearchResult:
    results: tuple[SymbolHit, ...]
    total_count: int
    truncated: bool

    def __post_init__(self) -> None:
        _items(self.results, SymbolHit)
        _int(self.total_count, len(self.results))
        _value(self.truncated, bool)


@dataclass(frozen=True, slots=True)
class LocalNode:
    ref: EntityRef
    name: str
    kind: str
    origin: NodeOrigin | None
    confidence: Confidence
    anchors: tuple[AnchorRecord, ...]
    distance: int

    def __post_init__(self) -> None:
        _value(self.ref, EntityRef)
        _text(self.name)
        _text(self.kind)
        if self.origin is not None:
            _value(self.origin, NodeOrigin)
        _value(self.confidence, Confidence)
        _items(self.anchors, AnchorRecord)
        _int(self.distance, 0, 2)


@dataclass(frozen=True, slots=True)
class LocalRelation:
    key: RelationKey
    source: EntityRef
    target: EntityRef
    type: EdgeType
    confidence: Confidence
    anchors: tuple[AnchorRecord, ...]

    def __post_init__(self) -> None:
        _value(self.key, RelationKey)
        _value(self.source, EntityRef)
        _value(self.target, EntityRef)
        _value(self.type, EdgeType)
        _value(self.confidence, Confidence)
        _items(self.anchors, AnchorRecord)


@dataclass(frozen=True, slots=True)
class LocalRelationsResult:
    status: Literal["ok", "unavailable"]
    nodes: tuple[LocalNode, ...]
    relations: tuple[LocalRelation, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _literal(self.status, ("ok", "unavailable"))
        _items(self.nodes, LocalNode)
        _items(self.relations, LocalRelation)
        _value(self.truncated, bool)


@dataclass(frozen=True, slots=True)
class ExcerptToolResult:
    evidence_id: str
    relative_path: str
    text: str
    returned_anchor: AnchorRecord
    truncated: bool

    def __post_init__(self) -> None:
        _text(self.evidence_id, minimum=1, maximum=512)
        validate_source_path(self.relative_path)
        _value(self.returned_anchor, AnchorRecord)
        if self.returned_anchor.end_line - self.returned_anchor.start_line + 1 > 200:
            raise AgentError()
        _source_body(self.text)
        _value(self.truncated, bool)
        if self.relative_path != self.returned_anchor.file_path:
            raise AgentError()


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    evidence_id: str
    scope: RequestScope
    snapshot_binding: SnapshotBinding
    target: EntityRef | RelationKey | None
    provenance: SymbolRecord | CodeNodeRecord | ComponentRecord | CodeEdgeRecord | RelationRecord | None
    returned_anchor: AnchorRecord
    content_hash: str
    confidence: Confidence
    truncated: bool
    status: Literal["bound", "source_only"]

    def __post_init__(self) -> None:
        # Only a future session verifier can prove membership and issued receipts.
        _text(self.evidence_id, minimum=1, maximum=512)
        _value(self.scope, RequestScope)
        _text(self.scope.project_epoch, minimum=1)
        _text(self.scope.request_id, minimum=1)
        _value(self.snapshot_binding, SnapshotBinding)
        _value(self.returned_anchor, AnchorRecord)
        _text(self.content_hash)
        if len(self.content_hash) != 64 or any(c not in "0123456789abcdef" for c in self.content_hash):
            raise AgentError()
        _value(self.confidence, Confidence)
        _value(self.truncated, bool)
        _literal(self.status, ("bound", "source_only"))
        if self.status == "source_only":
            if self.target is not None or self.provenance is not None or self.confidence is not Confidence.EXACT:
                raise AgentError()
        else:
            if type(self.target) not in (EntityRef, RelationKey):
                raise AgentError()
            if type(self.provenance) not in (SymbolRecord, CodeNodeRecord, ComponentRecord,
                                              CodeEdgeRecord, RelationRecord):
                raise AgentError()
            _value(self.target, type(self.target))
            _value(self.provenance, type(self.provenance))
