"""Bounded evidence metadata for trusted Python callers.

This registry validates structure and request ownership, not source authenticity
or freshness. It is not yet a model security enforcement boundary.
"""

import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from threading import Lock
from types import MappingProxyType

from codeviz.core import Confidence, SourceAnchor

from .contracts import AgentError, RequestScope, _integer, _relative_path, _utf8

_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_ENTRIES = 1000
_MAX_BYTES = 2 * 1024 * 1024


def _validate_hash(value: str) -> None:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise AgentError()


def _copy_anchor(anchor: SourceAnchor) -> SourceAnchor:
    if not isinstance(anchor, SourceAnchor):
        raise AgentError()
    path = _relative_path(anchor.file_path)
    _integer(anchor.start_line, minimum=1)
    if anchor.end_line is not None:
        _integer(anchor.end_line, minimum=1)
    for column in (anchor.start_column, anchor.end_column):
        if column is not None:
            _integer(column)
    try:
        return SourceAnchor(path, anchor.start_line, anchor.start_column,
                            anchor.end_line, anchor.end_column)
    except (ValueError, TypeError):
        raise AgentError() from None


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Immutable metadata only; no source text or arbitrary payload fields."""

    scope: RequestScope
    anchor: SourceAnchor
    content_hash: str
    confidence: Confidence
    symbol_id: str | None = None
    code_edge_id: str | None = None
    semantic_id: str | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RequestScope):
            raise AgentError()
        _validate_hash(self.content_hash)
        if not isinstance(self.confidence, Confidence) or type(self.truncated) is not bool:
            raise AgentError()
        for value in (self.symbol_id, self.code_edge_id, self.semantic_id):
            if value is not None:
                if not isinstance(value, str) or not 1 <= len(value) <= 512:
                    raise AgentError()
                _utf8(value)
        object.__setattr__(self, "anchor", _copy_anchor(self.anchor))


def _entry_bytes(evidence_id: str, record: EvidenceRecord) -> int:
    # Include the retained ID and every record/anchor/scope field, including nulls.
    try:
        payload = {"evidence_id": evidence_id, "record": asdict(record)}
        return len(_utf8(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False)))
    except (TypeError, ValueError, OverflowError):
        raise AgentError() from None


class EvidenceRegistry:
    """One request's local capacity; session-wide budgeting belongs to service."""

    def __init__(
        self, scope: RequestScope, *, max_entries: int = _MAX_ENTRIES,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        if not isinstance(scope, RequestScope):
            raise AgentError()
        _integer(max_entries, minimum=1)
        _integer(max_bytes, minimum=1)
        if max_entries > _MAX_ENTRIES or max_bytes > _MAX_BYTES:
            raise AgentError()
        self._scope = scope
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = Lock()
        self._records: dict[str, EvidenceRecord] = {}
        self._byte_count = 0
        self._closed = False

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def byte_count(self) -> int:
        with self._lock:
            return self._byte_count

    def register(self, record: EvidenceRecord) -> str:
        with self._lock:
            if self._closed:
                raise AgentError("registry_closed")
            if not isinstance(record, EvidenceRecord) or record.scope != self._scope:
                raise AgentError()
            if len(self._records) >= self._max_entries:
                raise AgentError("registry_full")
            for _ in range(8):
                try:
                    evidence_id = secrets.token_urlsafe(32)
                except Exception:
                    raise AgentError("id_unavailable") from None
                if evidence_id not in self._records:
                    break
            else:
                raise AgentError("id_unavailable")
            size = _entry_bytes(evidence_id, record)
            if self._byte_count + size > self._max_bytes:
                raise AgentError("registry_full")
            self._records[evidence_id] = record
            self._byte_count += size
            return evidence_id

    def lookup(
        self, evidence_id: str, *, scope: RequestScope, expected_file_hash: str,
    ) -> EvidenceRecord:
        """The caller must obtain the expected hash from a future freshness check."""
        with self._lock:
            if (self._closed or not isinstance(evidence_id, str)
                    or not isinstance(scope, RequestScope) or scope != self._scope):
                raise AgentError("evidence_unavailable")
            _validate_hash(expected_file_hash)
            record = self._records.get(evidence_id)
            if record is None or record.scope != scope or record.content_hash != expected_file_hash:
                raise AgentError("evidence_unavailable")
            return record

    def _snapshot_for_retention(self) -> Mapping[str, EvidenceRecord]:
        """Freeze the existing A records without creating evidence authority."""
        with self._lock:
            if self._closed:
                raise AgentError("registry_closed")
            copied: dict[str, EvidenceRecord] = {}
            for evidence_id, record in self._records.items():
                copied[evidence_id] = EvidenceRecord(
                    RequestScope(record.scope.project_epoch,
                                 record.scope.analysis_generation,
                                 record.scope.request_id),
                    _copy_anchor(record.anchor), record.content_hash,
                    record.confidence, record.symbol_id, record.code_edge_id,
                    record.semantic_id, record.truncated,
                )
            return MappingProxyType(copied)

    def clear(self) -> None:
        with self._lock:
            self._closed = True
            self._records.clear()
            self._byte_count = 0
