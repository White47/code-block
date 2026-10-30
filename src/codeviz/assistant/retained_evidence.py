"""Conversation-owned B5 evidence with revocable, independently budgeted leases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import cast

from .budget import BudgetLedger, BudgetReservation
from .contracts import AgentError, RequestScope
from .evidence import EvidenceRegistry
from .response import (
    ChosenTarget,
    ResolvedEvidence,
    _resolve_existing_evidence,
)
from .snapshot import AnalysisSnapshot
from .source_excerpts import SourceReadState
from .source_policy import SourceConsent
from .tool_contracts import EntityRef, RelationKey
from .tools import ToolSession


def _scope_fingerprint(consent: SourceConsent) -> str:
    encoded = json.dumps(consent.allowed_files, ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _target_copy(value: ChosenTarget) -> ChosenTarget:
    if value is None:
        return None
    if type(value) is EntityRef:
        value.__post_init__()
        return EntityRef(value.kind, value.id)
    if type(value) is RelationKey:
        value.__post_init__()
        return RelationKey(value.collection_kind, value.original_index)
    raise AgentError("evidence_unavailable")


def _target_json(value: ChosenTarget) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, EntityRef):
        return {"kind": value.kind, "id": value.id}
    return {"collection_kind": value.collection_kind,
            "original_index": value.original_index}


@dataclass(frozen=True, slots=True)
class RetainedEvidenceIdentity:
    project_epoch: str
    analysis_generation: int
    config_revision: int
    grant_revision: int
    conversation_revision: int
    origin: str
    normalized_scope: str
    consent: SourceConsent

    def __post_init__(self) -> None:
        scope = RequestScope(self.project_epoch, self.analysis_generation, "retained")
        if (type(self.config_revision) is not int or self.config_revision < 0
                or type(self.grant_revision) is not int or self.grant_revision < 0
                or type(self.conversation_revision) is not int
                or self.conversation_revision < 0
                or type(self.origin) is not str or not self.origin
                or type(self.normalized_scope) is not str
                or type(self.consent) is not SourceConsent):
            raise AgentError("evidence_unavailable")
        self.consent.__post_init__()
        if (self.consent.binding.project_epoch != scope.project_epoch
                or self.consent.binding.analysis_generation != scope.analysis_generation
                or self.consent.provider_origin != self.origin
                or _scope_fingerprint(self.consent) != self.normalized_scope):
            raise AgentError("evidence_unavailable")

    def logical(self) -> dict[str, object]:
        binding = self.consent.binding
        return {
            "project_epoch": self.project_epoch,
            "analysis_generation": self.analysis_generation,
            "config_revision": self.config_revision,
            "grant_revision": self.grant_revision,
            "conversation_revision": self.conversation_revision,
            "origin": self.origin,
            "normalized_scope": self.normalized_scope,
            "consent": {
                "binding": {
                    "project_root": binding.project_root,
                    "ignore_paths": binding.ignore_paths,
                    "project_epoch": binding.project_epoch,
                    "analysis_generation": binding.analysis_generation,
                },
                "consent_id": self.consent.consent_id,
                "provider_origin": self.consent.provider_origin,
                "allowed_files": self.consent.allowed_files,
            },
        }


class RetainedEvidenceContext:
    """Original frozen B5 owner; only leases may verify retained bindings."""

    def __init__(self, session: ToolSession, registry: EvidenceRegistry,
                 snapshot: AnalysisSnapshot, scope: RequestScope,
                 provided: Mapping[str, ChosenTarget],
                 identity: RetainedEvidenceIdentity, *,
                 logical_json: bytes, evidence_ids: frozenset[str]) -> None:
        self._session = session
        self._registry = registry
        self._snapshot = snapshot
        self._scope = scope
        self._provided = MappingProxyType(dict(provided))
        self._identity = identity
        self._logical_json = logical_json
        self._evidence_ids = evidence_ids
        self._lock = Lock()
        self._leases: dict[object, BudgetLedger] = {}
        self._revoked = False
        self._session_closed = False
        self._close_callback: Callable[[RetainedEvidenceContext], None] | None = None

    @classmethod
    def create(
        cls, session: ToolSession, registry: EvidenceRegistry,
        snapshot: AnalysisSnapshot, scope: RequestScope,
        provided: Mapping[str, ChosenTarget], identity: RetainedEvidenceIdentity,
    ) -> RetainedEvidenceContext:
        if (type(session) is not ToolSession or type(registry) is not EvidenceRegistry
                or type(snapshot) is not AnalysisSnapshot or type(scope) is not RequestScope
                or type(identity) is not RetainedEvidenceIdentity
                or not isinstance(provided, Mapping)
                or session._registry is not registry or session._snapshot is not snapshot
                or session._scope != scope or not snapshot.matches_scope(scope)
                or identity.project_epoch != scope.project_epoch
                or identity.analysis_generation != scope.analysis_generation):
            raise AgentError("evidence_unavailable")
        identity.__post_init__()
        copied: dict[str, ChosenTarget] = {}
        for evidence_id, target in provided.items():
            if type(evidence_id) is not str or not evidence_id or evidence_id in copied:
                raise AgentError("evidence_unavailable")
            chosen = _target_copy(target)
            proof = session.lookup_binding(evidence_id, chosen)
            _resolve_existing_evidence(registry, snapshot, scope,
                                       evidence_id, chosen, proof)
            copied[evidence_id] = chosen
        if not copied:
            raise AgentError("evidence_unavailable")
        manifest = session._freeze_for_retention()
        logical = {
            "authority": identity.logical(),
            "provided": {evidence_id: _target_json(target)
                         for evidence_id, target in sorted(copied.items())},
            "session": json.loads(manifest.logical_json),
        }
        logical_json = json.dumps(logical, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8")
        return cls(session, registry, snapshot, scope, copied, identity,
                   logical_json=logical_json,
                   evidence_ids=manifest.evidence_ids | frozenset(copied))

    @property
    def identity(self) -> RetainedEvidenceIdentity:
        return self._identity

    @property
    def evidence_ids(self) -> frozenset[str]:
        return self._evidence_ids

    def logical_payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self._logical_json))

    def borrow(self, current: RetainedEvidenceIdentity) -> RetainedEvidenceLease:
        if type(current) is not RetainedEvidenceIdentity:
            raise AgentError("evidence_unavailable")
        current.__post_init__()
        ledger = BudgetLedger()
        token = object()
        with self._lock:
            if self._revoked or self._session_closed or current != self._identity:
                raise AgentError("evidence_unavailable")
            self._leases[token] = ledger
        return RetainedEvidenceLease(self, token, ledger)

    def _current_state(self, token: object) -> SourceReadState:
        with self._lock:
            active = (not self._revoked and not self._session_closed
                      and token in self._leases)
        return SourceReadState(self._scope, self._identity.consent if active else None,
                               not active)

    def _resolve(self, token: object, ledger: BudgetLedger,
                 evidence_id: str) -> ResolvedEvidence:
        with self._lock:
            if (self._revoked or self._session_closed
                    or self._leases.get(token) is not ledger):
                raise AgentError("evidence_unavailable")
            chosen = self._provided.get(evidence_id)
            if evidence_id not in self._provided:
                raise AgentError("evidence_unavailable")
            session = self._session
        def checkpoint() -> None:
            ledger.reserve(BudgetReservation())

        proof = session._verify_retained_binding(
            evidence_id, chosen,
            current_state=lambda: self._current_state(token),
            checkpoint=checkpoint,
        )
        return _resolve_existing_evidence(
            self._registry, self._snapshot, self._scope,
            evidence_id, chosen, proof,
        )

    def _set_close_callback(
        self, callback: Callable[[RetainedEvidenceContext], None],
    ) -> None:
        if not callable(callback):
            raise AgentError("evidence_unavailable")
        with self._lock:
            if self._close_callback is not None or self._revoked:
                raise AgentError("evidence_unavailable")
            self._close_callback = callback

    def _begin_revoke(self) -> None:
        with self._lock:
            if self._revoked:
                return
            self._revoked = True
            ledgers = tuple(self._leases.values())
        for ledger in ledgers:
            ledger.cancel()

    def _close_if_ready(self) -> bool:
        with self._lock:
            if (not self._revoked or self._leases or self._session_closed):
                return False
            self._session_closed = True
            session = self._session
            callback = self._close_callback
        session.close()
        if callback is not None:
            callback(self)
        return True

    def _release(self, token: object, ledger: BudgetLedger) -> None:
        with self._lock:
            if self._leases.get(token) is not ledger:
                return
            del self._leases[token]
            should_close = self._revoked and not self._leases and not self._session_closed
        if should_close:
            self._close_if_ready()

    def revoke(self) -> None:
        self._begin_revoke()
        self._close_if_ready()


class RetainedEvidenceLease:
    """One revocable reference to a retained context."""

    def __init__(self, context: RetainedEvidenceContext, token: object,
                 ledger: BudgetLedger) -> None:
        self._context = context
        self._token = token
        self._ledger = ledger
        self._released = False

    def resolve_evidence(self, evidence_id: str) -> ResolvedEvidence:
        if self._released:
            raise AgentError("evidence_unavailable")
        return self._context._resolve(self._token, self._ledger, evidence_id)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._context._release(self._token, self._ledger)

    def __enter__(self) -> RetainedEvidenceLease:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()
