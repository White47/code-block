"""Consent-gated local source excerpts with metadata-only evidence.

State and native reads are observations, not atomic revocation or continuity
guarantees. Dispatch counts tools; service reserves every actual source send.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codeviz.core import Confidence, SourceAnchor
from codeviz.source_handles import SourceReadError, read_source_bytes

from .budget import BudgetLedger, BudgetReservation
from .contracts import AgentError, RequestScope
from .evidence import EvidenceRecord, EvidenceRegistry
from .freshness import _hash, _native_digest, _root_observation, _scope
from .snapshot import AnalysisSnapshot, AnchorRecord
from .source_policy import (
    SourceConsent,
    SourceErrorCode,
    _binding,
    _fail,
    _text,
    is_protected_source_path,
    validate_source_path,
)
from .source_text import (
    ExcerptLimits,
    MonacoRange,
    _anchor,
    contains_suspected_secret,
    decode_python_source,
    select_source_text,
)

_READ_ERRORS: dict[str, SourceErrorCode] = {
    "SOURCE_CHANGED": "source_changed",
    "FILE_TOO_LARGE": "source_unavailable",
    "READ_ERROR": "source_unavailable",
    "NOT_REGULAR_FILE": "source_unavailable",
    "UNSUPPORTED_PLATFORM": "verification_failed",
    "SOURCE_VERIFICATION_FAILED": "verification_failed",
    "SYMLINK_UNSUPPORTED": "verification_failed",
    "INVALID_SOURCE_PATH": "invalid_input",
}


@dataclass(frozen=True, slots=True)
class SourceReadState:
    scope: RequestScope
    consent: SourceConsent | None
    cancelled: bool

    def __post_init__(self) -> None:
        _scope(self.scope)
        if self.consent is not None:
            if type(self.consent) is not SourceConsent:
                _fail()
            self.consent.__post_init__()
        if type(self.cancelled) is not bool:
            _fail()


@dataclass(frozen=True, slots=True)
class ExcerptRequest:
    anchor: AnchorRecord

    def __post_init__(self) -> None:
        _anchor(self.anchor)


@dataclass(frozen=True, slots=True)
class SourceExcerpt:
    evidence_id: str
    scope: RequestScope
    relative_path: str
    content_hash: str
    text: str
    requested_anchor: AnchorRecord
    returned_anchor: AnchorRecord
    monaco_range: MonacoRange
    truncated: bool

    def __post_init__(self) -> None:
        _text(self.evidence_id)
        if not self.evidence_id:
            _fail()
        _scope(self.scope)
        validate_source_path(self.relative_path)
        _hash(self.content_hash)
        _text(self.text)
        _anchor(self.requested_anchor)
        _anchor(self.returned_anchor)
        if type(self.monaco_range) is not MonacoRange or type(self.truncated) is not bool:
            _fail()
        self.monaco_range.__post_init__()


_DEFAULT_LIMITS = ExcerptLimits()


class SourceExcerptReader:
    def __init__(
        self, snapshot: AnalysisSnapshot, *, scope: RequestScope,
        consent: SourceConsent, ledger: BudgetLedger, registry: EvidenceRegistry,
        current_state: Callable[[], SourceReadState], limits: ExcerptLimits = _DEFAULT_LIMITS,
    ) -> None:
        if (type(snapshot) is not AnalysisSnapshot or type(consent) is not SourceConsent
                or not isinstance(ledger, BudgetLedger) or not isinstance(registry, EvidenceRegistry)
                or type(limits) is not ExcerptLimits or not callable(current_state)):
            _fail()
        _scope(scope)
        _binding(snapshot.binding)
        consent.__post_init__()
        limits.__post_init__()
        if not snapshot.matches_scope(scope) or consent.binding != snapshot.binding:
            _fail("scope_mismatch")
        # Reject permission aliases without using case folding to grant access.
        allowed = {path.casefold(): path for path in consent.allowed_files}
        if any(file.relative_path.casefold() in allowed
               and allowed[file.relative_path.casefold()] != file.relative_path
               for file in snapshot.file_inventory):
            _fail()
        self._snapshot = snapshot
        self._scope = scope
        self._consent = consent
        self._ledger = ledger
        self._registry = registry
        self._current_state = current_state
        self._limits = limits

    def _check_state(self) -> None:
        try:
            state = self._current_state()
            if type(state) is not SourceReadState:
                _fail("verification_failed")
            state.__post_init__()
        except AgentError as error:
            if error.code in ("cancelled", "timeout"):
                raise
            _fail("verification_failed")
        except Exception:
            _fail("verification_failed")
        if state.scope != self._scope:
            _fail("scope_mismatch")
        if state.consent != self._consent:
            _fail("consent_required")
        if state.cancelled:
            raise AgentError("cancelled") from None

    def _gate(self) -> None:
        self._check_state()
        # Empty reservations enforce cancellation/deadlines, never charge reads.
        self._ledger.reserve(BudgetReservation())
        self._check_state()

    def _read_verified(self, path: str, expected: str) -> bytes:
        try:
            read = read_source_bytes(Path(self._snapshot.binding.project_root), path,
                                     max_bytes=self._limits.max_file_bytes)
        except SourceReadError as error:
            _fail(_READ_ERRORS.get(error.code, "verification_failed"))
        except OSError:
            _fail("source_unavailable")
        digest = _native_digest(read, self._limits.max_file_bytes)
        if digest != read.content_hash or digest != expected:
            _fail("source_changed")
        return read.raw_bytes

    def read_excerpt(self, request: ExcerptRequest) -> SourceExcerpt:
        if type(request) is not ExcerptRequest:
            _fail()
        request.__post_init__()
        anchor = request.anchor
        path = validate_source_path(anchor.file_path)
        if (not self._snapshot.matches_scope(self._scope)
                or self._consent.binding != self._snapshot.binding):
            _fail("scope_mismatch")
        self._gate()
        file = self._snapshot.get_file(path)
        if is_protected_source_path(path):
            _fail("path_denied")
        if path not in self._consent.allowed_files:
            _fail("consent_required")
        if (file is None or not path.casefold().endswith(".py")
                or any(path == ignored or path.startswith(ignored + "/")
                                for ignored in self._snapshot.binding.ignore_paths)
                or not file.was_parsed or file.content_hash is None):
            _fail("source_untrusted")
        _hash(file.content_hash)
        if file.line_count < 1 or anchor.end_line > file.line_count:
            _fail("invalid_range")
        root_state = _root_observation(self._snapshot.binding)
        raw = self._read_verified(path, file.content_hash)
        source = decode_python_source(raw)
        line_count = max(1, source.count("\n") + int(not source.endswith("\n")))
        if line_count != file.line_count:
            _fail("source_changed")
        if contains_suspected_secret(source):
            _fail("secret_blocked")
        selection = select_source_text(source, anchor, limits=self._limits)
        self._gate()
        self._read_verified(path, file.content_hash)
        if _root_observation(self._snapshot.binding) != root_state:
            _fail("source_changed")
        self._gate()
        returned = selection.returned_anchor
        record = EvidenceRecord(
            scope=self._scope,
            anchor=SourceAnchor(path, returned.start_line, returned.start_column,
                                returned.end_line, returned.end_column),
            content_hash=file.content_hash, confidence=Confidence.EXACT,
            truncated=selection.truncated,
        )
        evidence_id = self._registry.register(record)
        try:
            self._gate()
            return SourceExcerpt(evidence_id, self._scope, path, file.content_hash,
                                 selection.text, anchor, returned, selection.monaco_range,
                                 selection.truncated)
        except BaseException:
            # Even an interrupt after issuance must revoke retained metadata.
            self._registry.clear()
            raise
