"""Internal C4 admission, dispatch closure, and lifecycle polling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Literal, TypeVar, cast

from codeviz.core import NodeKind, ViewKind
from codeviz.navigation import RevealTarget
from codeviz.source_handles import SourceReadError, read_source_bytes
from codeviz.ui.assistant_context import AssistantContext, PublicationIdentity

from ._connection_values import _copy_connection_capabilities
from ._explanation_context import (
    ContextSelectionError,
    StartContextIndex,
    explanation_draft,
    inject_interpretation,
)
from ._usage_values import InputAccountingSnapshot, ObservedUsage
from ._validation_diagnostics import DiagnosticsSink, observation
from ._wire_accounting import (
    TrustedEstimatedProfile,
    WireAccountingError,
    WireAccountingPolicy,
    wire_reservation,
)
from .answer_public import answer_to_public
from .authority import AuthorityState
from .bridge_requests import (
    CancelRequest,
    ClearRequest,
    ConfigureRequest,
    ConnectionRequest,
    ConsentCandidatesRequest,
    ConsentRequest,
    NodeContext,
    PollRequest,
    ProjectContext,
    ProjectRequest,
    QuestionContext,
    ResolveRequest,
    SourceContext,
    StartRequest,
    UnderstandingContext,
    UnderstandingRef,
    _id,
    _natural,
    parse_cancel,
    parse_clear,
    parse_connection,
    parse_consent,
    parse_consent_candidates,
    parse_poll,
    parse_project,
    parse_resolve,
    parse_start,
)
from .budget import (
    EXPLANATION_BUDGET,
    BudgetLedger,
    BudgetLimits,
    BudgetReservation,
    BudgetUsage,
    ExplanationBudgetConfig,
)
from .config import ProviderConfig, ProviderError, _parse_url
from .connection import ConnectionProtocolError, _ConnectionResources, run_connection
from .connection_profile import (
    TrustedConnectionProfile,
    _copy_connection_profiles,
    _select_connection_profile,
)
from .consent import ConsentStore
from .contracts import AgentError, JobStatus, RequestScope
from .events import EventRing
from .evidence import EvidenceRegistry
from .failed_evidence import FailedEvidenceSnapshot
from .freshness import (
    FreshnessStatus,
    _native_digest,
    _root_observation,
    verify_snapshot_freshness,
)
from .interpretation_origin import AnswerOriginManifest, PriorInterpretationOrigin
from .issues import ProtocolBoundary, ProtocolDiagnostic, issue_for_code
from .jobs import (
    JobRequest,
    JobSnapshot,
    _ConnectionJob,
    _Job,
    _NavigationJob,
    _RetiredConnection,
    _RetiredJob,
    _RetiredNavigation,
)
from .navigation import NavigationError, NavigationTarget, build_navigation_target
from .poll_public import (
    DisplayEvent,
    PollIdentity,
    PollState,
    cancel_receipt_to_public,
    poll_to_public,
    snapshot_to_public,
)
from .project_retrieval import (
    ProjectCoverage,
    cross_file_call_witness,
    plan_project,
    representative_anchor,
)
from .project_understanding import (
    create_reference,
    effective_reference,
    item_evidence_ids,
    item_ids,
    owns_answer,
    validate_reference,
)
from .provider import (
    MemoryKey,
    PreparedRequest,
    ProviderAdapter,
    ProviderDraft,
    ProviderEvent,
)
from .response import (
    ActualEvidenceResolver,
    Answer,
    ChosenTarget,
    ResponseError,
    parse_model_answer,
    validate_model_answer,
)
from .response_session import RootSourceFragment, inject_root, verify_root_wire
from .retained_evidence import (
    RetainedEvidenceContext,
    RetainedEvidenceIdentity,
    RetainedEvidenceLease,
)
from .retrieval_budget import (
    InputBudgetPlanner,
    SourceHeadroomPlanner,
    output_allocation,
)
from .snapshot import AnalysisSnapshot, AnchorRecord, CodeNodeRecord, SymbolRecord
from .source_excerpts import SourceReadState
from .source_policy import (
    SourceConsent,
    SourceExcerptError,
    is_protected_source_path,
)
from .source_text import contains_suspected_secret, decode_python_source
from .tool_contracts import (
    EntityRef,
    ExcerptToolResult,
    LocalRelationsResult,
    RelationKey,
    SymbolSearchResult,
)
from .tool_loop import (
    EvidenceIdEntry,
    ToolCall,
    ToolLoopTranscript,
    ToolProjection,
    TrustedTarget,
    choose_target,
    project_tool_result,
)
from .tools import create_tool_session

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _PendingToolProjection:
    result: SymbolSearchResult | LocalRelationsResult | ExcerptToolResult
    preferred_relation: RelationKey | None = None


def _unavailable_provider() -> ProviderAdapter:
    raise ServiceError("AI_DEPENDENCY_MISSING")


class ServiceError(ValueError):
    """Fixed internal error; never includes source or request content."""

    def __init__(self, code: str, diagnostic: ProtocolDiagnostic | None = None) -> None:
        if (diagnostic is not None
                and (code != "AI_PROTOCOL_INCOMPATIBLE"
                     or type(diagnostic) is not ProtocolDiagnostic)):
            raise ValueError("Invalid service diagnostic.")
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(
            "Conversation capacity reached. Clear an inactive chat or start a new chat."
            if code == "AI_CONVERSATION_CAPACITY" else code)


def _protocol_boundary(operation: Callable[[], _T], boundary: ProtocolBoundary) -> _T:
    """Attach only the closed boundary enum; never retain failing payloads."""
    try:
        return operation()
    except ServiceError as error:
        if error.code != "AI_PROTOCOL_INCOMPATIBLE" or error.diagnostic is not None:
            raise
    except AgentError as error:
        if error.code in ("budget_exceeded", "timeout"):
            raise
    except (ResponseError, TypeError, ValueError, KeyError):
        pass
    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic(boundary)) from None


_SDK_WORKER_ERROR_CODES = frozenset({
    "AI_AUTH_FAILED",
    "AI_BUDGET_EXCEEDED",
    "AI_NETWORK_TIMEOUT",
    "AI_PROTOCOL_INCOMPATIBLE",
    "AI_RATE_LIMITED",
})


def _sdk_delta_rejection(error: Exception) -> object:
    from ._delta_reasons import validated_rejection

    if _sdk_worker_error(error) is None:
        return None
    return validated_rejection(vars(error).get("_delta_rejection"))


def _sdk_worker_error(
    error: Exception,
) -> tuple[str, ProtocolDiagnostic | None] | None:
    """Recognize only the already-loaded adapter's exact fixed error type."""
    module = sys.modules.get(f"{__package__}.sdk_provider")
    adapter_error = None if module is None else vars(module).get("SDKAdapterError")
    if type(adapter_error) is not type or type(error) is not adapter_error:
        return None
    code = vars(error).get("code")
    if type(code) is not str or code not in _SDK_WORKER_ERROR_CODES:
        return None
    diagnostic = vars(error).get("diagnostic")
    trusted = (diagnostic if code == "AI_PROTOCOL_INCOMPATIBLE"
               and type(diagnostic) is ProtocolDiagnostic else None)
    return code, trusted


def _connection_failure(
    error: Exception,
) -> tuple[str, ProtocolDiagnostic | None]:
    if isinstance(error, TimeoutError):
        return "AI_NETWORK_TIMEOUT", None
    if type(error) is AgentError:
        if error.code == "budget_exceeded":
            return "AI_BUDGET_EXCEEDED", None
        if error.code == "timeout":
            return "AI_NETWORK_TIMEOUT", None
        return "AI_PROTOCOL_INCOMPATIBLE", None
    if type(error) is ConnectionProtocolError:
        return "AI_PROTOCOL_INCOMPATIBLE", error.diagnostic
    sdk_failure = _sdk_worker_error(error)
    if sdk_failure is not None:
        return sdk_failure
    if type(error) in (ProviderError, ServiceError):
        code = vars(error).get("code")
        diagnostic = vars(error).get("diagnostic")
        trusted = (diagnostic if code == "AI_PROTOCOL_INCOMPATIBLE"
                   and type(diagnostic) is ProtocolDiagnostic else None)
        return (code, trusted) if type(code) is str else ("AI_PROTOCOL_INCOMPATIBLE", None)
    return "AI_PROTOCOL_INCOMPATIBLE", None


class ConversationStore:
    """Application-wide, bounded logical retention; every mutation is all or nothing."""

    MAX_CONVERSATIONS = 10
    MAX_MESSAGES = 40
    MAX_BYTES = 2 * 1024 * 1024
    MAX_EVIDENCE = 1000
    MAX_TOMBSTONES = 1024

    def __init__(self) -> None:
        self._lock = Lock()
        self._conversations: OrderedDict[tuple[str, str], dict[str, object]] = OrderedDict()
        self._tombstones: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._closed_revisions: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._evicted_ids: tuple[str, ...] = ()
        self._evicted_requests: tuple[tuple[str, str], ...] = ()
        self._retained_contexts: dict[
            tuple[str, str, str], RetainedEvidenceContext] = {}
        self._retired_evidence: OrderedDict[
            tuple[str, str, str], dict[str, object]] = OrderedDict()
        self._pending_retained_closures: set[RetainedEvidenceContext] = set()
        # One bounded watermark survives owner eviction/clear within an epoch.
        # The actual reference lives in its owner record and is capacity charged.
        self._project_understanding_clock: tuple[str, int] | None = None

    @staticmethod
    def _encoded(value: object) -> bytes:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ServiceError("AI_INVALID_INPUT") from None

    @classmethod
    def _detached(cls, value: object) -> object:
        return json.loads(cls._encoded(value))

    @staticmethod
    def _empty(project: str, conversation_id: str, revision: int) -> dict[str, object]:
        return {"project": project, "id": conversation_id, "revision": revision,
                "messages": [], "evidence": {}, "results": {}, "bindings": {},
                "retained": {}, "requests": [], "active": [], "draft": False}

    @staticmethod
    def _state(conversations: OrderedDict[tuple[str, str], dict[str, object]],
               tombstones: OrderedDict[tuple[str, str], str],
               closed: OrderedDict[tuple[str, str], int],
               retired: OrderedDict[tuple[str, str, str],
                                    dict[str, object]] | None = None) -> dict[str, object]:
        return {"conversations": list(conversations.values()),
                "retiredEvidence": list((retired or OrderedDict()).values()),
                "tombstones": [{"epoch": key[0], "requestId": key[1], "digest": digest}
                               for key, digest in tombstones.items()],
                "closed": [{"project": key[0], "id": key[1], "revision": revision}
                           for key, revision in closed.items()]}

    def _retired_for(
        self, candidate: OrderedDict[tuple[str, str], dict[str, object]],
    ) -> OrderedDict[tuple[str, str, str], dict[str, object]]:
        retired = self._retired_evidence.copy()
        for owner, context in self._retained_contexts.items():
            record = candidate.get(owner[:2])
            still_owned = (record is not None
                           and owner[2] in cast(dict[str, object], record["retained"]))
            if still_owned or owner in retired:
                continue
            project, conversation_id, job_id = owner
            retired[owner] = {
                "project": project, "id": conversation_id, "jobId": job_id,
                "ids": sorted(context.evidence_ids),
                "context": context.logical_payload(),
            }
        return retired

    @staticmethod
    def _evidence_ids(
        conversations: OrderedDict[tuple[str, str], dict[str, object]],
        retired: OrderedDict[tuple[str, str, str], dict[str, object]],
    ) -> set[str]:
        identifiers: set[str] = set()
        for record in conversations.values():
            identifiers.update(cast(dict[str, object], record["evidence"]))
            for retained in cast(dict[str, dict[str, object]], record["retained"]).values():
                identifiers.update(cast(list[str], retained["ids"]))
        for retained in retired.values():
            identifiers.update(cast(list[str], retained["ids"]))
        return identifiers

    def _fit(self, candidate: OrderedDict[tuple[str, str], dict[str, object]],
             protected: tuple[str, str] | None = None) -> tuple[str, ...]:
        if any(len(cast(list[object], record["messages"])) > self.MAX_MESSAGES
               for record in candidate.values()):
            raise ServiceError("AI_CONVERSATION_CAPACITY")
        evicted: list[str] = []
        while True:
            retired = self._retired_for(candidate)
            if (len(candidate) <= self.MAX_CONVERSATIONS
                    and len(self._evidence_ids(candidate, retired)) <= self.MAX_EVIDENCE
                    and len(self._encoded(self._state(
                        candidate, self._tombstones, self._closed_revisions, retired)))
                    <= self.MAX_BYTES):
                break
            victim = next((key for key, record in candidate.items()
                           if key != protected and not record["active"]
                           and not record["draft"]), None)
            if victim is None:
                raise ServiceError("AI_CONVERSATION_CAPACITY")
            evicted.append(victim[1])
            del candidate[victim]
        return tuple(evicted)

    def _commit(self, candidate: OrderedDict[tuple[str, str], dict[str, object]],
                protected: tuple[str, str] | None = None, *,
                delivery_job_id: str | None = None) -> tuple[str, ...]:
        evicted = [key[1] for key in self._conversations if key not in candidate]
        evicted.extend(self._fit(candidate, protected))
        while True:
            evicted_keys = tuple(key for key in self._conversations if key not in candidate)
            if delivery_job_id is not None and protected is not None and evicted_keys:
                delivery_owner = candidate[protected]
                delivery = dict(cast(dict[str, list[str]], delivery_owner.get("evictions", {})))
                pending = list(dict.fromkeys((*delivery.get(delivery_job_id, ()),
                                              *(key[1] for key in evicted_keys))))
                # C7 can carry ten IDs. Refuse the whole mutation before losing
                # an undelivered ID, rather than truncate a later poll.
                if len(pending) > 10:
                    raise ServiceError("AI_CONVERSATION_CAPACITY")
                delivery[delivery_job_id] = pending
                candidate[protected] = dict(delivery_owner, evictions=delivery)
            closed = self._closed_revisions.copy()
            tombstones = self._tombstones.copy()
            for key in evicted_keys:
                old = self._conversations[key]
                closed[key] = cast(int, old["revision"])
                for request in cast(list[dict[str, str]], old["requests"]):
                    tombstones[(request["epoch"], request["requestId"])] = request["digest"]
            while len(closed) > self.MAX_TOMBSTONES:
                closed.popitem(last=False)
            while len(tombstones) > self.MAX_TOMBSTONES:
                tombstones.popitem(last=False)
            retired = self._retired_for(candidate)
            while len(self._encoded(self._state(candidate, tombstones, closed,
                                                retired))) > self.MAX_BYTES:
                if not closed:
                    break
                closed.popitem(last=False)
            if len(self._encoded(self._state(candidate, tombstones, closed,
                                            retired))) <= self.MAX_BYTES:
                break
            victim = next((key for key, record in candidate.items()
                           if key != protected and not record["active"]
                           and not record["draft"]), None)
            if victim is None:
                raise ServiceError("AI_CONVERSATION_CAPACITY")
            evicted.append(victim[1])
            del candidate[victim]
        for owner, context in tuple(self._retained_contexts.items()):
            record = candidate.get(owner[:2])
            still_owned = (record is not None
                           and owner[2] in cast(dict[str, object], record["retained"]))
            if not still_owned and owner not in self._retired_evidence:
                context._begin_revoke()
                project, conversation_id, job_id = owner
                self._retired_evidence[owner] = {
                    "project": project, "id": conversation_id, "jobId": job_id,
                    "ids": sorted(context.evidence_ids),
                    "context": context.logical_payload(),
                }
                self._pending_retained_closures.add(context)
        self._evicted_requests = tuple(
            (request["epoch"], request["requestId"])
            for key in evicted_keys
            for request in cast(list[dict[str, str]], self._conversations[key]["requests"]))
        self._closed_revisions = closed
        self._tombstones = tombstones
        self._conversations = candidate
        self._evicted_ids = tuple(evicted)
        return tuple(evicted)

    def register(self, project: str, conversation_id: str, revision: int) -> tuple[str, ...]:
        if (type(project) is not str or not project or type(conversation_id) is not str
                or not conversation_id or type(revision) is not int or revision < 0):
            raise ServiceError("AI_INVALID_INPUT")
        key = (project, conversation_id)
        with self._lock:
            existing = self._conversations.get(key)
            if existing is not None:
                if existing["revision"] != revision:
                    raise ServiceError("AI_CONVERSATION_CLEARED")
                return ()
            if revision <= self._closed_revisions.get(key, -1):
                raise ServiceError("AI_CONVERSATION_CLEARED")
            candidate = self._conversations.copy()
            candidate[key] = self._empty(project, conversation_id, revision)
            return self._commit(candidate, key)

    def evict_inactive(self, project: str, conversation_id: str) -> bool:
        """Remove a whole inactive conversation when its authority slot is reused."""
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if current is None:
                return True
            if current["active"] or current["draft"]:
                return False
            candidate = self._conversations.copy()
            del candidate[key]
            self._commit(candidate)
            return True

    def reserve_append(self, project: str, conversation_id: str, revision: int, *,
                       messages: tuple[object, ...] = (),
                       evidence: dict[str, object] | None = None,
                       results: dict[str, object] | None = None,
                       bindings: dict[str, object] | None = None,
                       active_job_id: str | None = None,
                       request_key: tuple[str, str] | None = None,
                       digest: str | None = None,
                       retained: dict[str, RetainedEvidenceContext] | None = None,
                       _replace_provisional_job_id: str | None = None,
                       _delivery_job_id: str | None = None,
                       _project_understanding: dict[str, object] | None = None,
                       _answer_origin: AnswerOriginManifest | None = None,
                       create: bool = False) -> tuple[str, ...]:
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if current is None and create and revision > self._closed_revisions.get(key, -1):
                current = self._empty(project, conversation_id, revision)
            if current is None or current["revision"] != revision:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            updated = cast(dict[str, object], self._detached(current))
            if _replace_provisional_job_id is not None:
                updated["messages"] = [item for item in cast(list[dict[str, object]], updated["messages"])
                    if not (item.get("jobId") == _replace_provisional_job_id
                            and item.get("provisional") is True)]
            cast(list[object], updated["messages"]).extend(messages)
            for field, values in (("evidence", evidence), ("results", results),
                                  ("bindings", bindings)):
                if values:
                    retained_values = cast(dict[str, object], updated[field])
                    if any(type(name) is not str or not name or name in retained_values
                           for name in values):
                        raise ServiceError("AI_INVALID_INPUT")
                    retained_values.update(values)
            if retained:
                retained_projection = cast(dict[str, object], updated["retained"])
                for job_id, context in retained.items():
                    owner = (project, conversation_id, job_id)
                    if (type(job_id) is not str or not job_id
                            or type(context) is not RetainedEvidenceContext
                            or job_id in retained_projection
                            or owner in self._retained_contexts):
                        raise ServiceError("AI_INVALID_INPUT")
                    retained_projection[job_id] = {
                        "ids": sorted(context.evidence_ids),
                        "context": context.logical_payload(),
                    }
            if active_job_id is not None:
                if type(active_job_id) is not str or not active_job_id:
                    raise ServiceError("AI_INVALID_INPUT")
                cast(list[str], updated["active"]).append(active_job_id)
            if request_key is not None:
                if (type(request_key) is not tuple or len(request_key) != 2
                        or any(type(item) is not str or not item for item in request_key)
                        or type(digest) is not str or not digest):
                    raise ServiceError("AI_INVALID_INPUT")
                cast(list[object], updated["requests"]).append(
                    {"epoch": request_key[0], "requestId": request_key[1], "digest": digest})
            updated = cast(dict[str, object], self._detached(updated))
            if (active_job_id is not None
                    and len(cast(list[object], updated["messages"])) >= self.MAX_MESSAGES):
                raise ServiceError("AI_CONVERSATION_CAPACITY")
            candidate = self._conversations.copy()
            candidate[key] = updated
            if _answer_origin is not None:
                if (type(_answer_origin) is not AnswerOriginManifest or _answer_origin.owner[:2] != key
                        or _answer_origin.conversation_revision != revision):
                    raise ServiceError("AI_EVIDENCE_INVALID")
                origin_job = _answer_origin.owner[2]
                stored_answer = cast(dict[str, dict[str, object]], updated["results"]).get(origin_job, {}).get("answer")
                if type(stored_answer) is not str or not _answer_origin.matches_answer(stored_answer):
                    raise ServiceError("AI_EVIDENCE_INVALID")
                cast(dict[str, object], updated.setdefault("origins", {}))[origin_job] = _answer_origin.logical_payload()
            understanding_clock = self._project_understanding_clock
            if _project_understanding is not None:
                reference = validate_reference(self._detached(_project_understanding))
                epoch = cast(str, reference["projectEpoch"])
                previous_revision = (understanding_clock[1] if understanding_clock is not None
                                     and understanding_clock[0] == epoch else 0)
                try:
                    reference["revision"] = _natural(previous_revision + 1, 1)
                except ValueError:
                    raise ServiceError("AI_CONVERSATION_CAPACITY") from None
                if not owns_answer(reference, updated):
                    raise ServiceError("AI_INVALID_INPUT")
                # Copy only records whose reference changes: a rejected capacity
                # transaction must leave every previous owner untouched.
                for conversation_owner, record in tuple(candidate.items()):
                    if conversation_owner[0] == project and "projectUnderstanding" in record:
                        record = dict(record)
                        del record["projectUnderstanding"]
                        candidate[conversation_owner] = record
                candidate[key] = dict(candidate[key], projectUnderstanding=reference)
                understanding_clock = (epoch, cast(int, reference["revision"]))
            evicted = self._commit(candidate, key,
                                   delivery_job_id=_delivery_job_id or active_job_id)
            self._project_understanding_clock = understanding_clock
            if retained:
                for job_id, context in retained.items():
                    owner = (project, conversation_id, job_id)
                    context._set_close_callback(self._retained_closed)
                    self._retained_contexts[owner] = context
            return evicted

    def _retain_provisional(self, project: str, conversation_id: str, revision: int,
                            job_id: str, text: str) -> tuple[str, ...]:
        """Reserve display-only text in the same atomic store as final results."""
        if (type(text) is not str or len(text.encode("utf-8")) > 64 * 1024
                or len(self._encoded([text, None])) > 160 * 1024 - 8 * 1024 - 2048):
            raise ServiceError("AI_CONVERSATION_CAPACITY")
        with self._lock:
            current = self._conversations.get((project, conversation_id))
            if current is None or current["revision"] != revision:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            question = next((item for item in cast(list[dict[str, object]], current["messages"])
                             if item.get("jobId") == job_id and item.get("role") == "user"), None)
            if question is None or any(item.get("jobId") == job_id and item.get("role") == "assistant"
                    and item.get("provisional") is not True
                    for item in cast(list[dict[str, object]], current["messages"])):
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            message = {"role": "assistant", "jobId": job_id, "content": text,
                       "scope": question["scope"], "provisional": True}
        return self.reserve_append(project, conversation_id, revision,
            messages=(message,), _replace_provisional_job_id=job_id,
            _delivery_job_id=job_id)

    def set_active(self, project: str, conversation_id: str, job_id: str,
                   active: bool = True) -> None:
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if current is None:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            updated = cast(dict[str, object], self._detached(current))
            jobs = cast(list[str], updated["active"])
            if active and job_id not in jobs:
                jobs.append(job_id)
            elif not active and job_id in jobs:
                jobs.remove(job_id)
            candidate = self._conversations.copy()
            candidate[key] = updated
            self._commit(candidate, key, delivery_job_id=job_id)

    def set_draft(self, project: str, conversation_id: str, draft: bool) -> None:
        if type(draft) is not bool:
            raise ServiceError("AI_INVALID_INPUT")
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if current is None:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            updated = dict(current, draft=draft)
            candidate = self._conversations.copy()
            candidate[key] = updated
            self._commit(candidate, key)

    def note_request(self, project: str, conversation_id: str,
                     request_key: tuple[str, str], digest: str) -> None:
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if current is None:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            updated = cast(dict[str, object], self._detached(current))
            cast(list[object], updated["requests"]).append(
                {"epoch": request_key[0], "requestId": request_key[1], "digest": digest})
            candidate = self._conversations.copy()
            candidate[key] = updated
            self._commit(candidate, key)

    def clear(self, project: str, conversation_id: str, revision: int) -> tuple[str, ...]:
        key = (project, conversation_id)
        with self._lock:
            current = self._conversations.get(key)
            if (current is None or type(revision) is not int
                    or revision <= cast(int, current["revision"])):
                raise ServiceError("AI_INVALID_INPUT")
            jobs = tuple(cast(list[str], current["active"]))
            tombstones = self._tombstones.copy()
            for request in cast(list[dict[str, str]], current["requests"]):
                tombstones[(request["epoch"], request["requestId"])] = request["digest"]
            while len(tombstones) > self.MAX_TOMBSTONES:
                tombstones.popitem(last=False)
            candidate = self._conversations.copy()
            candidate[key] = self._empty(project, conversation_id, revision)
            retired = self._retired_for(candidate)
            while len(self._encoded(self._state(candidate, tombstones,
                                                self._closed_revisions,
                                                retired))) > self.MAX_BYTES:
                if not tombstones:
                    raise ServiceError("AI_CONVERSATION_CAPACITY")
                tombstones.popitem(last=False)
            for owner, context in tuple(self._retained_contexts.items()):
                if owner[:2] != key or owner in self._retired_evidence:
                    continue
                context._begin_revoke()
                self._retired_evidence[owner] = retired[owner]
                self._pending_retained_closures.add(context)
            self._conversations = candidate
            self._tombstones = tombstones
            self._evicted_ids = ()
            self._evicted_requests = ()
            return jobs

    def remember_tombstone(self, request_key: tuple[str, str], digest: str) -> None:
        with self._lock:
            tombstones = self._tombstones.copy()
            tombstones[request_key] = digest
            while (len(tombstones) > self.MAX_TOMBSTONES
                   or len(self._encoded(self._state(self._conversations, tombstones,
                                                    self._closed_revisions,
                                                    self._retired_evidence)))
                   > self.MAX_BYTES):
                tombstones.popitem(last=False)
            self._tombstones = tombstones

    def tombstone(self, request_key: tuple[str, str]) -> str | None:
        with self._lock:
            return self._tombstones.get(request_key)

    def revision_of(self, project: str, conversation_id: str) -> int | None:
        with self._lock:
            record = self._conversations.get((project, conversation_id))
            return cast(int, record["revision"]) if record is not None else None

    def request_keys(self, project: str, conversation_id: str) -> tuple[tuple[str, str], ...]:
        with self._lock:
            record = self._conversations.get((project, conversation_id))
            if record is None:
                return ()
            return tuple((request["epoch"], request["requestId"])
                         for request in cast(list[dict[str, str]], record["requests"]))

    @property
    def tombstone_count(self) -> int:
        with self._lock:
            return len(self._tombstones)

    @property
    def evidence_count(self) -> int:
        with self._lock:
            return len(self._evidence_ids(self._conversations,
                                          self._retired_evidence))

    @property
    def logical_bytes(self) -> int:
        with self._lock:
            return len(self._encoded(self._state(self._conversations, self._tombstones,
                                                 self._closed_revisions,
                                                 self._retired_evidence)))

    def logical_state(self) -> dict[str, object]:
        with self._lock:
            return cast(dict[str, object], self._detached(
                self._state(self._conversations, self._tombstones,
                            self._closed_revisions, self._retired_evidence)))

    def _project_capture(self, project: str) -> tuple[dict[str, object], ...]:
        """Capture only display candidates; never copy retained proofs or tool data."""
        with self._lock:
            return tuple({"id": record["id"], "revision": record["revision"],
                          "messages": tuple(cast(list[object], record["messages"])),
                          "results": dict(cast(dict[str, object], record["results"])),
                          "recovery": dict(cast(dict[str, object], record.get("recovery", {}))),
                          **({"projectUnderstanding": self._detached(record["projectUnderstanding"])}
                             if "projectUnderstanding" in record else {})}
                         for key, record in self._conversations.items() if key[0] == project)

    def _project_revisions(self, project: str) -> tuple[tuple[str, int], ...]:
        with self._lock:
            return tuple((key[1], cast(int, record["revision"]))
                         for key, record in self._conversations.items() if key[0] == project)

    def _capture_project_understanding(self, project: str) -> dict[str, object]:
        """Capture only the current reference and require its original stored answer."""
        with self._lock:
            owners = [record for key, record in self._conversations.items()
                      if key[0] == project and "projectUnderstanding" in record]
            if len(owners) != 1:
                raise AgentError("evidence_unavailable")
            reference = validate_reference(owners[0]["projectUnderstanding"])
            if not owns_answer(reference, owners[0]):
                raise AgentError("evidence_unavailable")
            return cast(dict[str, object], self._detached(reference))

    def _capture_answer_origin(self, owner: tuple[str, str, str]) -> AnswerOriginManifest:
        with self._lock:
            record = self._conversations.get(owner[:2])
            if record is None:
                raise AgentError("evidence_unavailable")
            manifest = AnswerOriginManifest.from_payload(cast(dict[str, object], record.get("origins", {})).get(owner[2]))
            answer = cast(dict[str, dict[str, object]], record["results"]).get(owner[2], {}).get("answer")
            if (manifest.owner != owner or manifest.conversation_revision != record["revision"]
                    or type(answer) is not str or not manifest.matches_answer(answer)):
                raise AgentError("evidence_unavailable")
            return manifest

    def _memory_slots(self, projects: set[str] | None = None) -> tuple[tuple[str, str, int], ...]:
        with self._lock:
            return tuple((key[0], key[1], cast(int, record["revision"]))
                         for key, record in self._conversations.items()
                         if (projects is None or key[0] in projects)
                         and any(record.get(field) for field in (
                             "messages", "evidence", "results", "bindings", "retained", "requests", "active", "draft")))

    def _remember_recovery(self, project: str, conversation_id: str, revision: int,
                           job_id: str, binding: dict[str, object]) -> None:
        with self._lock:
            key = (project, conversation_id)
            current = self._conversations.get(key)
            if current is None or current["revision"] != revision:
                return  # Never repopulate a cleared/evicted conversation.
            recovery = dict(cast(dict[str, object], current.get("recovery", {})))
            recovery[job_id] = binding
            updated = dict(current, recovery=recovery)
            candidate = self._conversations.copy()
            candidate[key] = updated
            self._commit(candidate, key, delivery_job_id=job_id)

    def _advance_recovery(self, job_id: str, seq: int, status: JobStatus) -> None:
        with self._lock:
            for key, record in self._conversations.items():
                recovery = dict(cast(dict[str, dict[str, object]], record.get("recovery", {})))
                if job_id not in recovery:
                    continue
                recovery[job_id] = dict(recovery[job_id], seq=seq, status=status.value)
                candidate = self._conversations.copy()
                candidate[key] = dict(record, recovery=recovery)
                self._commit(candidate, key, delivery_job_id=job_id)
                return

    def _pending_evictions(self, project: str | None, conversation_id: str,
                           revision: int, job_id: str) -> tuple[str, ...]:
        with self._lock:
            record = self._conversations.get((project, conversation_id)) if project is not None else None
            if record is None or record["revision"] != revision:
                return ()
            return tuple(cast(dict[str, list[str]], record.get("evictions", {})).get(job_id, ()))

    def _borrow_retained(
        self, project: str, conversation_id: str, job_id: str,
        identity: RetainedEvidenceIdentity,
    ) -> RetainedEvidenceLease:
        with self._lock:
            context = self._retained_contexts.get((project, conversation_id, job_id))
            if context is None or (project, conversation_id, job_id) in self._retired_evidence:
                raise AgentError("evidence_unavailable")
            return context.borrow(identity)

    def _revoke_retained_except(
        self, allowed: dict[tuple[str, str], RetainedEvidenceIdentity],
    ) -> None:
        """Deny new leases atomically; actual close is drained outside state locks."""
        with self._lock:
            candidate = self._conversations.copy()
            changed = False
            for owner, context in self._retained_contexts.items():
                if owner in self._retired_evidence or allowed.get(owner[:2]) == context.identity:
                    continue
                record = candidate.get(owner[:2])
                if record is not None:
                    updated = cast(dict[str, object], self._detached(record))
                    cast(dict[str, object], updated["retained"]).pop(owner[2], None)
                    candidate[owner[:2]] = updated
                    changed = True
            if changed:
                self._commit(candidate)

    def _borrow_by_evidence(
        self, project: str, conversation_id: str, evidence_id: str,
        identity: RetainedEvidenceIdentity,
    ) -> RetainedEvidenceLease:
        _owner, context = self._capture_evidence_owner(project, conversation_id, evidence_id)
        return context.borrow(identity)

    def _capture_evidence_owner(self, project: str, conversation_id: str,
                                evidence_id: str) -> tuple[tuple[str, str, str], RetainedEvidenceContext]:
        """Capture only; borrow constructs its clock/ledger outside all state locks."""
        with self._lock:
            matches = [(owner, context) for owner, context in self._retained_contexts.items()
                       if owner[:2] == (project, conversation_id)
                       and owner not in self._retired_evidence
                       and evidence_id in context.evidence_ids]
            if len(matches) != 1:
                raise AgentError("evidence_unavailable")
            return matches[0]

    def _retained_closed(self, context: RetainedEvidenceContext) -> None:
        with self._lock:
            owners = tuple(owner for owner, current in self._retained_contexts.items()
                           if current is context)
            for owner in owners:
                self._retained_contexts.pop(owner, None)
                self._retired_evidence.pop(owner, None)
            self._pending_retained_closures.discard(context)

    def _drain_retained_closures(self) -> None:
        with self._lock:
            pending = tuple(self._pending_retained_closures)
            self._pending_retained_closures.clear()
        for context in pending:
            context._close_if_ready()

    def _has_retained_resources(self) -> bool:
        with self._lock:
            return bool(self._retained_contexts)

    def _select_eligible_history(self, project: str, conversation_id: str,
                                 revision: int, *, project_epoch: str, origin: str,
                                 generation: int, consent_revision: int,
                                 config_revision: int,
                                 normalized_scope: str) -> tuple[dict[str, str], ...]:
        """Return only completed, source-free service turns for this exact authority."""
        expected_scope = {"projectEpoch": project_epoch, "origin": origin,
                          "generation": generation, "consentRevision": consent_revision,
                          "configRevision": config_revision,
                          "normalizedScope": normalized_scope}
        with self._lock:
            record = self._conversations.get((project, conversation_id))
            if record is None or record["revision"] != revision:
                return ()
            messages = cast(list[object], record["messages"])
            results = cast(dict[str, object], record["results"])
            safe_jobs: set[str] = set()
            for item in messages:
                if type(item) is not dict or item.get("role") != "assistant":
                    continue
                job_id = item.get("jobId")
                result = results.get(job_id) if type(job_id) is str else None
                if (item.get("provisional") is not True
                        and type(job_id) is str and type(result) is dict
                        and result.get("answer") == item.get("content")
                        and item.get("scope") == expected_scope
                        and item.get("sourceFree") is True
                        and item.get("sourceTags") == []):
                    safe_jobs.add(job_id)
            eligible: list[dict[str, str]] = []
            for item in messages:
                if type(item) is not dict:
                    continue
                role, content, job_id = (item.get("role"), item.get("content"),
                                         item.get("jobId"))
                if (role in ("user", "assistant") and type(content) is str
                        and type(job_id) is str and job_id in safe_jobs
                        and item.get("scope") == expected_scope
                        and not item.get("sourceTags")):
                    eligible.append({"role": role, "content": content})
            return tuple(eligible[-6:])

    @property
    def conversation_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(key[1] for key in self._conversations)

    @property
    def evicted_ids(self) -> tuple[str, ...]:
        with self._lock:
            return self._evicted_ids

    @property
    def evicted_request_keys(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return self._evicted_requests


async def _ready(_prepared: PreparedRequest) -> None:
    return None


def _provider_payload(event: ProviderEvent) -> dict[str, object]:
    if type(event) is not ProviderEvent or type(event.payload) is not bytes:
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
    if not 0 < len(event.payload) <= 8192:
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        data = json.loads(event.payload, object_pairs_hook=unique,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE") from None
    if type(data) is not dict:
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
    return data


def _wire_reservation(prepared: PreparedRequest) -> tuple[BudgetReservation, tuple[str, ...]]:
    """Preserve the strict service facade while sharing pure wire validation."""
    try:
        return wire_reservation(prepared)
    except WireAccountingError as error:
        raise ServiceError(error.code) from None


class _SendPolicy:
    """Service error facade over the pure request-local accounting policy."""

    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        trusted_profiles: tuple[TrustedEstimatedProfile, ...] = (),
        explanation_budget: ExplanationBudgetConfig | None = None,
        trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = (),
    ) -> None:
        try:
            self._policy = WireAccountingPolicy(
                ledger,
                trusted_profiles=trusted_profiles,
                budget=explanation_budget,
                trusted_interpretations=trusted_interpretations,
            )
        except WireAccountingError as error:
            raise ServiceError(error.code) from None
        self._legacy_active_id: str | None = None

    @property
    def disclosed_files(self) -> tuple[str, ...]:
        return self._policy.disclosed_files

    @property
    def observed(self) -> ObservedUsage:
        try:
            return self._policy.observed
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    @property
    def snapshot(self) -> InputAccountingSnapshot | None:
        try:
            return self._policy.snapshot
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def ensure_can_prepare(self) -> None:
        try:
            self._policy.ensure_can_prepare()
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def admit(self, prepared: PreparedRequest,
              phase: Literal["ordinary", "final", "repair"]
              ) -> tuple[BudgetReservation, tuple[str, ...]]:
        try:
            if self._legacy_active_id is not None:
                self._policy.finalize(self._legacy_active_id)
                self._legacy_active_id = None
            result = self._policy.admit(prepared, phase)
            self._legacy_active_id = prepared.preparation_id
            return result
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def dispatch(self, preparation_id: str) -> None:
        try:
            self._policy.dispatch(preparation_id)
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def observe(self, preparation_id: str, usage: ObservedUsage) -> None:
        try:
            self._policy.observe(preparation_id, usage)
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def finalize(self, preparation_id: str) -> None:
        try:
            self._policy.finalize(preparation_id)
            if self._legacy_active_id == preparation_id:
                self._legacy_active_id = None
        except WireAccountingError as error:
            raise ServiceError(error.code) from None

    def report_usage(self, reservation: BudgetReservation, usage: dict[str, object]) -> None:
        try:
            self._policy.report_usage(reservation, usage)
            self._legacy_active_id = None
        except WireAccountingError as error:
            raise ServiceError(error.code) from None


def _validated_trusted_profiles(
    profiles: tuple[TrustedEstimatedProfile, ...],
) -> tuple[TrustedEstimatedProfile, ...]:
    """Validate and detach the private application-owned profile allow-list."""
    try:
        if type(profiles) is not tuple:
            raise ValueError("invalid profiles")
        copied: list[TrustedEstimatedProfile] = []
        seen: set[str] = set()
        for profile in profiles:
            if type(profile) is not TrustedEstimatedProfile:
                raise ValueError("invalid profile")
            values = (
                (profile.profile_id, 128),
                (profile.endpoint, 2048),
                (profile.model, 128),
            )
            if any(type(value) is not str or not 1 <= len(value) <= limit
                   or not value.isprintable() for value, limit in values):
                raise ValueError("invalid profile")
            for value, _limit in values:
                value.encode("utf-8", "strict")
            if (type(profile.method) is not str
                    or profile.method != "wire-bytes-v1"
                    or profile.profile_id in seen):
                raise ValueError("invalid profile")
            seen.add(profile.profile_id)
            copied.append(TrustedEstimatedProfile(
                profile.profile_id,
                profile.endpoint,
                profile.model,
                profile.method,
            ))
        return tuple(copied)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE") from None


def _validated_trusted_connection_profiles(
    profiles: tuple[TrustedConnectionProfile, ...],
) -> tuple[TrustedConnectionProfile, ...]:
    try:
        return _copy_connection_profiles(profiles)
    except (AttributeError, TypeError, ValueError):
        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE") from None


def _estimated_accounting_fields(prepared: PreparedRequest) -> tuple[object, ...]:
    accounting = prepared.estimated_input_accounting
    if accounting is None:
        return (None, None, None, None)
    return (
        accounting.profile_id,
        accounting.method,
        accounting.estimated_input_tokens,
        accounting.body_sha256,
    )


def _verify_wire_files(snapshot: AnalysisSnapshot | None, consent: SourceConsent | None,
                       scope: RequestScope, origin: str, files: tuple[str, ...],
                       checkpoint: Callable[[], None]) -> None:
    """Use B4's bounded native reads and whole-file screening for every identity."""
    if not files:
        return
    try:
        if (type(snapshot) is not AnalysisSnapshot or type(consent) is not SourceConsent
                or consent.binding != snapshot.binding or consent.provider_origin != origin
                or not snapshot.matches_scope(scope)):
            raise ValueError("unavailable consent")
        root_state = _root_observation(snapshot.binding)
        for path in files:
            checkpoint()
            file = snapshot.get_file(path)
            if (is_protected_source_path(path) or path not in consent.allowed_files
                    or file is None or not file.was_parsed or file.content_hash is None
                    or not path.casefold().endswith(".py")
                    or any(path == ignored or path.startswith(ignored + "/")
                           for ignored in snapshot.binding.ignore_paths)):
                raise ValueError("untrusted file")
            read = read_source_bytes(Path(snapshot.binding.project_root), path,
                                     max_bytes=5_000_000)
            if (_native_digest(read, 5_000_000) != read.content_hash
                    or read.content_hash != file.content_hash):
                raise ValueError("changed file")
            source = decode_python_source(read.raw_bytes)
            line_count = max(1, source.count("\n") + int(not source.endswith("\n")))
            if line_count != file.line_count or contains_suspected_secret(source):
                raise ValueError("blocked file")
            checkpoint()
            again = read_source_bytes(Path(snapshot.binding.project_root), path,
                                      max_bytes=5_000_000)
            if (_native_digest(again, 5_000_000) != file.content_hash
                    or _root_observation(snapshot.binding) != root_state):
                raise ValueError("changed file")
            checkpoint()
    except (SourceReadError, SourceExcerptError, OSError, ValueError):
        raise ServiceError("AI_CONSENT_REQUIRED") from None


class AssistantService:
    """One loop thread, short state lock and an optional async dispatch readiness port."""

    def __init__(self, provider_factory: Callable[[], ProviderAdapter], *,
                 before_dispatch: Callable[[PreparedRequest], Awaitable[None]] = _ready,
                 conversations: ConversationStore | None = None,
                 trusted_input_profiles: tuple[TrustedEstimatedProfile, ...] = (),
                 trusted_connection_profiles: tuple[TrustedConnectionProfile, ...] = (),
                 explanation_budget: ExplanationBudgetConfig | None = None,
                 _validation_diagnostics: DiagnosticsSink | None = None) -> None:
        if (explanation_budget is not None
                and type(explanation_budget) is not ExplanationBudgetConfig):
            raise ServiceError("AI_INVALID_INPUT")
        trusted_profiles = _validated_trusted_profiles(trusted_input_profiles)
        connection_profiles = _validated_trusted_connection_profiles(trusted_connection_profiles)
        try:
            frozen_budget = (None if explanation_budget is None else
                             replace(explanation_budget))
        except (AgentError, TypeError, ValueError):
            raise ServiceError("AI_INVALID_INPUT") from None
        self._provider_factory = provider_factory
        self._before_dispatch = before_dispatch
        self._trusted_input_profiles = trusted_profiles
        self._trusted_connection_profiles = connection_profiles
        self._explanation_budget = frozen_budget
        self._validation_diagnostics = _validation_diagnostics
        self._lock = Lock()
        self._conversations = conversations if conversations is not None else ConversationStore()
        self._runtime_owned_store = conversations is not None
        self._recovery_unavailable: set[str] = set()
        self._identity: PublicationIdentity | None = None
        self._snapshot: AnalysisSnapshot | None = None
        self._authority: AuthorityState | None = None
        self._source_consent: SourceConsent | None = None
        self._jobs: dict[str, _Job] = {}
        self._completed: dict[str, _RetiredJob] = {}
        self._active_job_ids: set[str] = set()
        self._connection_slots: set[str] = set()
        self._navigation_jobs: dict[str, _NavigationJob] = {}
        self._navigation_completed: OrderedDict[str, _RetiredNavigation] = OrderedDict()
        self._pending_navigation_closures: set[str] = set()
        self._connection_config: ProviderConfig | None = None
        self._connection_jobs: dict[str, _ConnectionJob] = {}
        self._connection_requests: dict[tuple[int, str], str] = {}
        self._connection_completed: OrderedDict[str, _RetiredConnection] = OrderedDict()
        self._pending_connection_closures: set[str] = set()
        self._requests: dict[tuple[str, str], tuple[str, str]] = {}
        self._retired_maps: list[tuple[dict[tuple[str, str], tuple[str, str]],
                                       dict[str, _RetiredJob]]] = []
        self._pending_closures: set[str] = set()
        self._b_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assistant-b")
        self._b_outstanding = 0
        self._loop = asyncio.new_event_loop()
        started = Event()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            started.set()
            self._loop.run_forever()
            self._loop.close()

        self._thread = Thread(target=run, name="assistant-jobs", daemon=True)
        self._thread.start()
        started.wait()

    def replace_current(self, identity: PublicationIdentity) -> None:
        """Replace the mirror and record closure work without entering the worker loop."""
        self._replace_publication(identity, None)

    def _replace_publication(self, identity: PublicationIdentity,
                             snapshot: AnalysisSnapshot | None) -> None:
        """Atomically mirror identity and its detached immutable analysis snapshot."""
        with self._lock:
            previous = self._identity
            dependencies_changed = (previous is not None
                                    and self._dependency_identity(previous)
                                    != self._dependency_identity(identity))
            if previous is not None and previous.project_epoch != identity.project_epoch:
                if self._retired_maps:
                    raise ServiceError("AI_PUBLICATION_BACKLOG")
                if self._requests or self._completed:
                    self._retired_maps.append((self._requests, self._completed))
                self._requests = {}
                self._completed = {}
                self._authority = None
                self._source_consent = None
            self._identity = identity
            self._snapshot = (self._snapshot if snapshot is None and previous is not None
                              and previous.eligible and identity.eligible
                              and not dependencies_changed else snapshot)
            if dependencies_changed:
                for job_id, retired in tuple(self._completed.items()):
                    if (retired.snapshot.status is JobStatus.COMPLETED
                            and self._dependency_identity(retired.publication)
                            != self._dependency_identity(identity)):
                        self._completed[job_id] = replace(
                            retired, snapshot=replace(retired.snapshot, status=JobStatus.STALE),
                            seq=retired.seq + 1,
                            events=(*retired.events[-7:], {
                                "seq": retired.seq + 1, "jobId": job_id, "type": "status",
                                "status": JobStatus.STALE.value, "resultReady": False}))
            for job_id in self._active_job_ids:
                job = self._jobs[job_id]
                if not self._job_eligible_locked(job):
                    job.cancel_requested = True
                    self._sync_ring_locked(job)
                    self._pending_closures.add(job_id)
            self._sync_retained_authority_locked()

    def _replace_authority(self, authority: AuthorityState | None) -> None:
        """Trusted config/grant replacement; caller drains closure work outside locks."""
        if authority is not None:
            if type(authority) is not AuthorityState:
                raise ServiceError("AI_INVALID_INPUT")
            authority.__post_init__()
        with self._lock:
            self._authority = authority
            self._invalidate_ineligible_jobs_locked()
            self._sync_retained_authority_locked()

    def _replace_source_consent(self, consent: SourceConsent | None) -> None:
        if consent is not None:
            if type(consent) is not SourceConsent:
                raise ServiceError("AI_INVALID_INPUT")
            consent.__post_init__()
        with self._lock:
            self._source_consent = consent
            self._sync_retained_authority_locked()

    def _replace_runtime_authority(self, authority: AuthorityState | None,
                                   consent: SourceConsent | None) -> None:
        """Private composition transaction; no half-updated grant is eligible."""
        if authority is not None:
            if type(authority) is not AuthorityState:
                raise ServiceError("AI_INVALID_INPUT")
            authority.__post_init__()
        if consent is not None:
            if type(consent) is not SourceConsent:
                raise ServiceError("AI_INVALID_INPUT")
            consent.__post_init__()
        with self._lock:
            if authority is None and consent is None:
                self._clear_runtime_memory_locked(all_history=False)
            self._authority, self._source_consent = authority, consent
            self._invalidate_ineligible_jobs_locked()
            self._sync_retained_authority_locked()

    def _clear_runtime_memory_locked(self, *, all_history: bool) -> None:
        """Clear affected records via revisions while the service lock is held."""
        authority = self._authority
        state = self._conversations.logical_state()
        records = cast(list[dict[str, object]], state["conversations"])
        eligible = (None if all_history else set() if authority is None else
                    {(authority.project_key, conversation_id)
                     for conversation_id, _ in authority.conversation_revisions})
        for record in records:
            key = (cast(str, record["project"]), cast(str, record["id"]))
            if eligible is not None and key not in eligible:
                continue
            has_memory = (any(record[field] for field in (
                "messages", "evidence", "results", "bindings", "retained",
                "requests", "active")) or record["draft"] is True)
            if not has_memory:
                continue
            request_keys = self._conversations.request_keys(*key)
            revision = cast(int, record["revision"])
            self._conversations.clear(*key, revision + 1)
            for request_key in request_keys:
                entry = self._requests.get(request_key)
                if entry is None:
                    continue
                retired = self._completed.get(entry[1])
                if retired is not None:
                    self._completed[entry[1]] = replace(
                        retired,
                        snapshot=replace(retired.snapshot, status=JobStatus.STALE),
                        seq=retired.seq + 1,
                        events=({"seq": retired.seq + 1, "jobId": entry[1],
                                 "type": "status", "status": JobStatus.STALE.value,
                                 "resultReady": False},))

    def _invalidate_runtime_memory(self, *, all_history: bool = False) -> None:
        """Revoke runtime authority and clear affected memory through store revisions."""
        if type(all_history) is not bool:
            raise ServiceError("AI_INVALID_INPUT")
        with self._lock:
            self._clear_runtime_memory_locked(all_history=all_history)
            self._authority, self._source_consent = None, None
            self._invalidate_ineligible_jobs_locked()
            self._sync_retained_authority_locked()

    def _retained_identity_locked(self, conversation_id: str) -> RetainedEvidenceIdentity:
        identity, authority, consent = self._identity, self._authority, self._source_consent
        if (identity is None or not identity.eligible or authority is None or not authority.config_enabled
                or consent is None or authority.project_key != identity.project_key
                or consent.binding.project_epoch != identity.project_epoch
                or consent.binding.analysis_generation != identity.analysis_generation
                or consent.binding.project_root != str(identity.root)
                or consent.binding.ignore_paths != identity.ignore_paths):
            raise AgentError("evidence_unavailable")
        revision = authority.conversation_revision(conversation_id)
        if revision is None:
            raise AgentError("evidence_unavailable")
        return RetainedEvidenceIdentity(consent.binding.project_epoch, consent.binding.analysis_generation,
                                        authority.config_revision, authority.grant_revision, revision,
                                        authority.origin, authority.scope_fingerprint, consent)

    def _sync_retained_authority_locked(self) -> None:
        allowed: dict[tuple[str, str], RetainedEvidenceIdentity] = {}
        authority = self._authority
        if authority is not None:
            for conversation_id, _ in authority.conversation_revisions:
                try:
                    allowed[(authority.project_key, conversation_id)] = self._retained_identity_locked(conversation_id)
                except AgentError:
                    continue
        self._conversations._revoke_retained_except(allowed)
        self._invalidate_navigation_locked()

    def _borrow_evidence(self, conversation_id: str, evidence_id: str) -> RetainedEvidenceLease:
        with self._lock:
            identity = self._retained_identity_locked(conversation_id)
            authority = self._authority
            if authority is None:
                raise AgentError("evidence_unavailable")
            _owner, context = self._conversations._capture_evidence_owner(
                authority.project_key, conversation_id, evidence_id)
        lease = context.borrow(identity)
        try:
            with self._lock:
                current = self._retained_identity_locked(conversation_id)
                _owner, captured = self._conversations._capture_evidence_owner(
                    authority.project_key, conversation_id, evidence_id)
                if current != identity or captured is not context:
                    raise AgentError("evidence_unavailable")
            return lease
        except Exception:
            lease.release()
            raise

    def _set_conversation_revision(self, conversation_id: str, revision: int) -> None:
        """Trusted clear/new-conversation update, monotonic for the current slot."""
        if (type(conversation_id) is not str or not conversation_id
                or type(revision) is not int or revision < 0):
            raise ServiceError("AI_INVALID_INPUT")
        with self._lock:
            authority = self._authority
            if authority is None:
                raise ServiceError("AI_CONSENT_REQUIRED")
            revisions = dict(authority.conversation_revisions)
            previous = revisions.get(conversation_id)
            if previous is not None and revision < previous:
                raise ServiceError("AI_INVALID_INPUT")
            revisions[conversation_id] = revision
            if len(revisions) > 10:
                victim = next((name for name in authority.conversation_revisions
                               if name[0] != conversation_id
                               and self._conversations.evict_inactive(
                                   authority.project_key, name[0])), None)
                if victim is None:
                    raise ServiceError("AI_CONVERSATION_CAPACITY")
                revisions.pop(victim[0])
                self._drop_evicted_requests_locked()
            if previous is not None and revision > previous:
                stored = self._conversations.revision_of(authority.project_key,
                                                          conversation_id)
                if stored is not None:
                    request_keys = self._conversations.request_keys(
                        authority.project_key, conversation_id)
                    self._conversations.clear(authority.project_key, conversation_id,
                                              revision)
                    for key in request_keys:
                        entry = self._requests.get(key)
                        if entry is not None:
                            retired = self._completed.get(entry[1])
                            if retired is not None:
                                self._completed[entry[1]] = replace(
                                    retired,
                                    snapshot=replace(retired.snapshot,
                                                     status=JobStatus.STALE),
                                    events=())
            self._authority = replace(authority,
                                      conversation_revisions=tuple(revisions.items()))
            self._invalidate_ineligible_jobs_locked()
            self._sync_retained_authority_locked()
        self._drain_pending_closures()

    def _invalidate_ineligible_jobs_locked(self) -> None:
        for job_id in self._active_job_ids:
            job = self._jobs[job_id]
            if not self._job_eligible_locked(job):
                job.cancel_requested = True
                self._sync_ring_locked(job)
                self._pending_closures.add(job_id)

    def _drain_pending_closures(self) -> None:
        """Schedule recorded closures after the publication/state locks are released."""
        self._conversations._drain_retained_closures()
        with self._lock:
            navigation_pending = tuple(self._pending_navigation_closures)
            self._pending_navigation_closures.clear()
            connection_pending = tuple(self._pending_connection_closures)
            self._pending_connection_closures.clear()
            pending = tuple(self._pending_closures)
            self._pending_closures.difference_update(
                job_id for job_id in pending if job_id not in self._jobs)
            jobs = tuple(self._jobs[job_id] for job_id in pending if job_id in self._jobs)
            retired_maps = tuple(self._retired_maps)
        for requests, completed in retired_maps:
            if self._runtime_owned_store:
                for retired in completed.values():
                    try:
                        self._conversations._advance_recovery(
                            retired.snapshot.job_id, retired.seq, retired.snapshot.status)
                    except ServiceError:
                        with self._lock:
                            self._recovery_unavailable.add(retired.snapshot.job_id)
            requests.clear()
            completed.clear()
        if retired_maps:
            with self._lock:
                if self._retired_maps and self._retired_maps[0] is retired_maps[0]:
                    self._retired_maps = []
        ledgers = tuple(job.ledger for job in jobs)
        for ledger in ledgers:
            if ledger is not None:
                ledger.cancel()
        for job in jobs:
            self._loop.call_soon_threadsafe(self._schedule_close, job.job_id)
            with self._lock:
                self._pending_closures.discard(job.job_id)
        for job_id in connection_pending:
            self._loop.call_soon_threadsafe(self._schedule_connection_close, job_id)
        for job_id in navigation_pending:
            self._loop.call_soon_threadsafe(self._schedule_navigation_close, job_id)

    def _capture_understanding_item_locked(self, binding: UnderstandingRef) -> tuple[dict[str, object], Answer]:
        authority = self._authority
        if authority is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        try:
            reference = self._conversations._capture_project_understanding(authority.project_key)
            owner = cast(str, reference["conversationId"])
            retained = self._retained_identity_locked(owner)
            state, _events = self._capture_explanation_locked(cast(str, reference["jobId"]), retained.project_epoch)
            if (reference["artifactId"] != binding.artifact_id or reference["revision"] != binding.revision
                    or any(reference[field] != value for field, value in (
                        ("projectEpoch", retained.project_epoch), ("analysisGeneration", retained.analysis_generation),
                        ("conversationRevision", retained.conversation_revision),
                        ("configRevision", retained.config_revision), ("consentRevision", retained.grant_revision)))
                    or state.lifecycle.status is not JobStatus.COMPLETED or state.answer is None
                    or binding.item_id not in item_ids(state.answer)):
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            return reference, state.answer
        except (AgentError, ValueError, KeyError, TypeError):
            raise ServiceError("AI_RESULT_UNAVAILABLE") from None

    def _followup_eligible_locked(self, request: JobRequest) -> bool:
        if request.understanding is None:
            return True
        try:
            reference, _answer = self._capture_understanding_item_locked(request.understanding)
            return (request.understanding_owner == (request.expected_identity.project_key,
                        reference["conversationId"], reference["jobId"])
                    and request.understanding_owner_revision == reference["conversationRevision"])
        except ServiceError:
            return False

    def _prior_interpretation_locked(self, request: JobRequest) -> PriorInterpretationOrigin:
        binding, owner = request.understanding, request.understanding_owner
        if binding is None or owner is None or not self._followup_eligible_locked(request):
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        reference, answer = self._capture_understanding_item_locked(binding)
        try:
            manifest = self._conversations._capture_answer_origin(owner)
            if (self._authority is None or self._snapshot is None or manifest.binding != self._snapshot.binding
                    or manifest.config_revision != request.config_revision
                    or manifest.consent_revision != request.consent_revision
                    or manifest.origin != self._authority.origin):
                raise ValueError()
            if (manifest.normalized_scope != request.normalized_scope or not manifest.matches_answer(
                    json.dumps(answer_to_public(answer), ensure_ascii=False, separators=(",", ":"), allow_nan=False))):
                raise ValueError()
            for path, expected_hash in manifest.dependency_files:
                file = self._snapshot.get_file(path)
                if file is None or file.content_hash != expected_hash:
                    raise ValueError()
            # The exact original Answer occurrence is already bound by the stored digest.
            text = ""
            for section in answer.sections:
                for index, claim in enumerate(section.claims):
                    if binding.item_id == "project" or binding.item_id == f"{section.kind}:{index}":
                        text = (text + ("\n" if text else "") + claim.text)[:1000]
                        if len(text) == 1000:
                            break
                if len(text) == 1000:
                    break
            return PriorInterpretationOrigin(uuid.uuid4().hex, manifest,
                cast(str, reference["artifactId"]), cast(int, reference["revision"]), binding.item_id, text)
        except (AgentError, ValueError, TypeError, KeyError):
            raise ServiceError("AI_EVIDENCE_INVALID") from None

    def _understanding_matches_locked(self, binding: UnderstandingRef | None,
                                      owner: tuple[str, str, str], evidence_id: str,
                                      retained: RetainedEvidenceIdentity) -> bool:
        if binding is None:
            return True  # Original C6 path has no artifact constraint.
        try:
            reference = self._conversations._capture_project_understanding(owner[0])
            if (reference["artifactId"] != binding.artifact_id or reference["revision"] != binding.revision
                    or reference["conversationId"] != owner[1] or reference["jobId"] != owner[2]
                    or any(reference[field] != value for field, value in (
                        ("projectEpoch", retained.project_epoch), ("analysisGeneration", retained.analysis_generation),
                        ("conversationRevision", retained.conversation_revision),
                        ("configRevision", retained.config_revision), ("consentRevision", retained.grant_revision)))
                    or self._retained_identity_locked(owner[1]) != retained):
                return False
            state, _events = self._capture_explanation_locked(owner[2], retained.project_epoch)
            if (state.lifecycle.status is not JobStatus.COMPLETED or state.answer is None
                    or state.identity.conversation_id != owner[1]
                    or state.identity.conversation_revision != retained.conversation_revision
                    or state.identity.analysis_generation != retained.analysis_generation
                    or state.config_revision != retained.config_revision or state.consent_revision != retained.grant_revision):
                return False
            return evidence_id in item_evidence_ids(state.answer, binding.item_id)
        except (AgentError, ServiceError, ValueError, TypeError, KeyError):
            return False

    def _navigation_eligible_locked(self, job: _NavigationJob) -> bool:
        if (self._identity != job.publication or job.context is None
                or self._snapshot is not job.context.snapshot):
            return False
        try:
            current = self._retained_identity_locked(job.owner[1])
            owner, context = self._conversations._capture_evidence_owner(
                job.owner[0], job.owner[1], job.evidence_id)
            return (current == job.retained_identity and owner == job.owner
                    and context is job.retained_context
                    and self._understanding_matches_locked(job.understanding, job.owner,
                        job.evidence_id, job.retained_identity))
        except AgentError:
            return False

    @staticmethod
    def _navigation_state_locked(job: _NavigationJob) -> PollState:
        return PollState(job.identity, job.snapshot(), job.retained_identity.config_revision,
            job.retained_identity.grant_revision, job.seq, evidence_id=job.evidence_id,
            action=job.action, target=job.target if job.status is JobStatus.COMPLETED else None)

    @staticmethod
    def _navigation_emit_locked(job: _NavigationJob, kind: Literal["status", "result", "lifecycle"],
                                payload: dict[str, object]) -> None:
        job.seq += 1
        job.events.append(DisplayEvent(job.seq, kind, payload))

    def _navigation_lifecycle_locked(self, job: _NavigationJob) -> None:
        self._navigation_emit_locked(job, "lifecycle", {
            "cancelRequested": job.cancel_requested, "cancelAccepted": job.cancel_accepted,
            "dispatchClosed": job.dispatch_closed, "resourcesReleased": job.resources_released})

    def _invalidate_navigation_locked(self) -> None:
        for job in self._navigation_jobs.values():
            if not self._navigation_eligible_locked(job):
                self._request_navigation_close_locked(job)
        for job_id, retired in tuple(self._navigation_completed.items()):
            state = retired.state
            if self._identity is None or state.identity.project_epoch != self._identity.project_epoch:
                self._navigation_completed.pop(job_id, None)
                continue
            try:
                valid = (self._identity == retired.publication
                    and self._retained_identity_locked(retired.owner[1]) == retired.retained_identity
                    and self._conversations._capture_evidence_owner(retired.owner[0], retired.owner[1],
                        cast(str, state.evidence_id))[0] == retired.owner
                    and self._understanding_matches_locked(retired.understanding, retired.owner,
                        cast(str, state.evidence_id), retired.retained_identity))
            except AgentError:
                valid = False
            if not valid and state.lifecycle.status is JobStatus.COMPLETED:
                stale = replace(state, lifecycle=replace(state.lifecycle, status=JobStatus.STALE),
                                target=None, seq=state.seq + 1)
                self._navigation_completed[job_id] = replace(retired, state=stale, events=())

    def _request_navigation_close_locked(self, job: _NavigationJob) -> None:
        if job.resources_released:
            return
        if job.status is JobStatus.COMPLETED:
            job.status, job.target, job.dispatch_closed = JobStatus.STALE, None, True
            self._navigation_emit_locked(job, "status", {"status": JobStatus.STALE.value})
        elif not job.cancel_requested:
            job.cancel_requested, job.dispatch_closed = True, True
            self._navigation_lifecycle_locked(job)
        elif not job.dispatch_closed:
            job.dispatch_closed = True
            self._navigation_lifecycle_locked(job)
        self._pending_navigation_closures.add(job.job_id)

    def _navigation_replay_locked(self, request: ResolveRequest, publication: PublicationIdentity,
                                  retained: RetainedEvidenceIdentity) -> _NavigationJob | None:
        for job in self._navigation_jobs.values():
            if (job.publication == publication and job.retained_identity == retained
                    and job.owner[1] == request.conversation_id and job.evidence_id == request.evidence_id
                    and job.action == request.action and not job.cancel_requested
                    and job.understanding == request.understanding and not job.dispatch_closed
                    and (job.understanding is None or self._navigation_eligible_locked(job))):
                return job
        return None

    @staticmethod
    def _navigation_receipt(job: _NavigationJob) -> dict[str, object]:
        return {"jobId": job.job_id, "status": job.status.value, "kind": "navigation",
            "conversationId": job.owner[1], "scope": {"projectEpoch": job.scope.project_epoch,
                "analysisGeneration": job.scope.analysis_generation, "requestId": job.scope.request_id}}

    def _schedule_navigation(self, job: _NavigationJob) -> None:
        try:
            self._loop.call_soon_threadsafe(self._launch_navigation, job.job_id)
        except Exception:
            # Release the already borrowed lease outside state locks even when
            # scheduling never produced a task/future.
            with self._lock:
                job.status, job.dispatch_closed = JobStatus.FAILED, True
                job.issue = issue_for_code("AI_CAPACITY")
            if job.lease is not None:
                job.lease.release()
            with self._lock:
                self._retire_navigation_locked(job)

    def _launch_navigation(self, job_id: str) -> None:
        with self._lock:
            job = self._navigation_jobs.get(job_id)
            if job is None or job.task is not None:
                return
            job.task = self._loop.create_task(self._work_navigation(job))

    @staticmethod
    def _navigation_symbol_identity(symbol: SymbolRecord | None, code: CodeNodeRecord | None) -> bool:
        """Prove an ordinary Symbol root from immutable indexed definition facts."""
        return (type(symbol) is SymbolRecord and type(code) is CodeNodeRecord
                and symbol.definition
                and symbol.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)
                and (symbol.id, symbol.kind, symbol.name, symbol.qualified_name,
                     symbol.scope, symbol.file_path, (symbol.source_anchor,))
                == (code.id, code.kind, code.name, code.qualified_name,
                    code.scope, code.file_path, code.source_anchors))

    @staticmethod
    def _navigation_reveal(ctx: AssistantContext, target: EntityRef) -> RevealTarget:
        fact = ctx.navigation_facts.get(target)
        if fact is None:
            raise NavigationError()
        for location in fact.visible_locations:
            mapped = ctx.context_map.get(location)
            if (location.visible_node_id == target.id and location.detail_source_id is None
                    and mapped is not None and not mapped.truncated and not mapped.source_only
                    and mapped.reason is None and mapped.targets == (target,)):
                return RevealTarget(location.view_kind, location.root_id, target.id)
        if target.kind == "symbol" and fact.visible_locations == () and ctx.snapshot is not None:
            symbol = ctx.snapshot.get_symbol(target.id)
            code = ctx.snapshot.get_code_node(target.id)
            if AssistantService._navigation_symbol_identity(symbol, code):
                # This plans a root; only a later normal get_view can deliver it.
                return RevealTarget(ViewKind.SYMBOL, target.id, target.id)
        raise NavigationError()

    def _check_navigation(self, job: _NavigationJob) -> NavigationTarget:
        def checkpoint() -> None:
            with self._lock:
                if job.cancel_requested or job.dispatch_closed or not self._navigation_eligible_locked(job):
                    raise ServiceError("AI_CANCELLED")

        checkpoint()
        context, lease = job.context, job.lease
        if context is None or context.snapshot is None or lease is None:
            raise NavigationError()
        report = verify_snapshot_freshness(context.snapshot, scope=job.scope, checkpoint=checkpoint)
        if report.status is not FreshnessStatus.FRESH:
            raise ServiceError("AI_ANALYSIS_STALE")
        resolved = lease.resolve_evidence(job.evidence_id)
        checkpoint()
        reveal = None
        if job.action == "graph":
            if resolved.proof.status != "bound" or type(resolved.proof.target) is not EntityRef:
                raise NavigationError()
            reveal = self._navigation_reveal(context, resolved.proof.target)
        target = build_navigation_target(resolved, context.snapshot, job.scope, job.action,
            checked_at=datetime.now(timezone.utc).isoformat(), conversation_id=job.owner[1],
            conversation_revision=job.retained_identity.conversation_revision,
            config_revision=job.retained_identity.config_revision,
            consent_revision=job.retained_identity.grant_revision, reveal=reveal)
        # Re-resolve the original receipt, never issue one or trust returned JSON.
        lease.resolve_evidence(job.evidence_id)
        checkpoint()
        return target

    async def _work_navigation(self, job: _NavigationJob) -> None:
        try:
            with self._lock:
                if job.cancel_requested or job.dispatch_closed or not self._navigation_eligible_locked(job):
                    raise ServiceError("AI_CANCELLED")
                if self._b_outstanding >= 2:
                    raise ServiceError("AI_CAPACITY")
                job.status = JobStatus.RUNNING
                self._navigation_emit_locked(job, "status", {"status": JobStatus.RUNNING.value})
                job.b_submitting = True
                self._b_outstanding += 1
            try:
                raw = self._b_executor.submit(self._check_navigation, job)
            except Exception:
                with self._lock:
                    job.b_submitting = False
                    self._b_outstanding -= 1
                raise ServiceError("AI_CAPACITY") from None
            with self._lock:
                job.b_future, job.b_submitting = raw, False

            def finished(_future: Future[NavigationTarget]) -> None:
                with self._lock:
                    self._b_outstanding -= 1

            raw.add_done_callback(finished)
            target = await asyncio.shield(asyncio.wrap_future(raw))
            with self._lock:
                if job.cancel_requested or job.dispatch_closed or not self._navigation_eligible_locked(job):
                    raise ServiceError("AI_CANCELLED")
                job.target, job.status, job.dispatch_closed = target, JobStatus.COMPLETED, True
                self._navigation_emit_locked(job, "result", {"available": True})
        except (Exception, asyncio.CancelledError) as error:
            with self._lock:
                if not job.cancel_requested and not job.resources_released:
                    code = error.code if isinstance(error, ServiceError) else "AI_EVIDENCE_INVALID"
                    job.issue = issue_for_code(code)
                    job.status, job.target, job.dispatch_closed = JobStatus.FAILED, None, True
                    self._navigation_emit_locked(job, "status", {"status": JobStatus.FAILED.value})
        finally:
            await self._close_navigation(job)

    def _schedule_navigation_close(self, job_id: str) -> None:
        with self._lock:
            job = self._navigation_jobs.get(job_id)
        if job is not None:
            self._loop.create_task(self._close_navigation(job))

    async def _close_navigation(self, job: _NavigationJob) -> None:
        with self._lock:
            if job.closing or job.resources_released:
                return
            job.closing = True
            job.dispatch_closed = True
        try:
            # Never cancel the OS Future, return its lease, or free its slot
            # while the underlying read is still running.
            while True:
                with self._lock:
                    submitting, raw = job.b_submitting, job.b_future
                if raw is not None:
                    try:
                        await asyncio.shield(asyncio.wrap_future(raw))
                    except (Exception, asyncio.CancelledError):
                        if not raw.done():
                            raise
                    if not raw.done():
                        return
                    break
                if not submitting:
                    break
                await asyncio.sleep(0.01)
            if job.lease is not None:
                job.lease.release()
            with self._lock:
                if job.cancel_requested:
                    job.status, job.target, job.cancel_accepted = JobStatus.CANCELLED, None, True
                elif job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.status = JobStatus.CANCELLED
                    job.cancel_requested = job.cancel_accepted = True
                self._retire_navigation_locked(job)
        except (Exception, asyncio.CancelledError):
            return  # Keep ownership for a later cleanup retry.
        finally:
            with self._lock:
                job.closing = False

    def _retire_navigation_locked(self, job: _NavigationJob) -> None:
        if (job.understanding is not None and job.status is JobStatus.COMPLETED
                and not self._navigation_eligible_locked(job)):
            job.status, job.target = JobStatus.STALE, None
            self._navigation_emit_locked(job, "status", {"status": JobStatus.STALE.value})
        job.resources_released = True
        self._navigation_lifecycle_locked(job)
        state = self._navigation_state_locked(job)
        self._navigation_jobs.pop(job.job_id, None)
        self._pending_navigation_closures.discard(job.job_id)
        job.lease, job.retained_context, job.context, job.b_future, job.task = None, None, None, None, None
        if self._identity is not None and self._identity.project_epoch == job.scope.project_epoch:
            self._navigation_completed[job.job_id] = _RetiredNavigation(state, job.publication,
                job.retained_identity, job.owner, tuple(job.events), job.understanding)
            while len(self._navigation_completed) > 1024:
                self._navigation_completed.popitem(last=False)

    def _capture_navigation_locked(self, job_id: str, epoch: str) -> tuple[PollState, tuple[DisplayEvent, ...]]:
        job = self._navigation_jobs.get(job_id)
        if job is not None and job.scope.project_epoch == epoch:
            if job.understanding is not None:
                self._invalidate_navigation_locked()
            return self._navigation_state_locked(job), tuple(job.events)
        retired = self._navigation_completed.get(job_id)
        if retired is None or retired.state.identity.project_epoch != epoch:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        if retired.understanding is not None:
            self._invalidate_navigation_locked()
            retired = self._navigation_completed[job_id]
        return retired.state, retired.events

    def _poll_navigation(self, request: PollRequest) -> dict[str, object]:
        if request.project_epoch is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with self._lock:
            state, events = self._capture_navigation_locked(request.job_id, request.project_epoch)
            job = self._navigation_jobs.get(request.job_id)
            close_constrained = (job is not None and job.understanding is not None
                                 and request.job_id in self._pending_navigation_closures)
        if close_constrained:
            self._drain_pending_closures()
        if request.after_seq > state.seq:
            raise ServiceError("AI_INVALID_INPUT")
        reset = request.after_seq < (events[0].seq - 1 if events else state.seq)
        return poll_to_public(state, after_seq=request.after_seq,
            events=() if reset else tuple(event for event in events if event.seq > request.after_seq), reset=reset)

    def _cancel_navigation(self, request: CancelRequest) -> dict[str, object]:
        if request.project_epoch is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with self._lock:
            self._capture_navigation_locked(request.job_id, request.project_epoch)
            job = self._navigation_jobs.get(request.job_id)
            if job is not None:
                if job.status is not JobStatus.COMPLETED:
                    self._request_navigation_close_locked(job)
                state = self._navigation_state_locked(job)
            else:
                state, _events = self._capture_navigation_locked(request.job_id, request.project_epoch)
        if job is not None:
            self._loop.call_soon_threadsafe(self._schedule_navigation_close, job.job_id)
        return cancel_receipt_to_public(state)

    def _retire_job_locked(self, job: _Job) -> None:
        """Drop the full request after release; keep only an active-epoch snapshot."""
        if job.resources_released and job.validation_capture is not None:
            try:
                if self._validation_diagnostics is not None:
                    self._validation_diagnostics.publish(job.validation_capture)
            except Exception:
                pass
            finally:
                job.validation_capture = None
        project_key = job.request.expected_identity.project_key
        if (project_key is not None and job.conversation_revision is not None
                and self._conversations.revision_of(
                    project_key, job.request.conversation_id)
                == job.conversation_revision):
            self._conversations.set_active(project_key,
                                           job.request.conversation_id, job.job_id, False)
        self._active_job_ids.discard(job.job_id)
        self._jobs.pop(job.job_id, None)
        job.provider_factory = None
        job.provider = None
        job.prepared = None
        identity = self._identity
        key = (job.request.scope.project_epoch, job.request.scope.request_id)
        if (identity is not None and job.request.scope.project_epoch == identity.project_epoch
                and self._requests.get(key) == (job.digest, job.job_id)):
            self._completed[job.job_id] = _RetiredJob(
                job.snapshot(), job.ring.seq, "explanation", job.request.expected_identity,
                job.ring.retirement_events(),
                self._explanation_state_locked(job, text=cast(str, job.ring.poll(job.ring.seq)["text"]))
                if job.public_identity is not None else None,
                job.input_accounting, job.project_coverage)

    def _project_coverage(self, job_id: str) -> ProjectCoverage | None:
        """Private P1.2 handoff; no public state, artifact or independent cache."""
        with self._lock:
            active = self._jobs.get(job_id)
            if active is not None:
                return active.project_coverage
            retired = self._completed.get(job_id)
            return None if retired is None else retired.project_coverage

    @staticmethod
    def _explanation_state_locked(job: _Job, *, text: str = "") -> PollState:
        if job.public_identity is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        return PollState(job.public_identity, job.snapshot(), job.request.config_revision,
            job.request.consent_revision, job.ring.seq,
            usage=job.ledger.usage if job.ledger is not None else BudgetUsage(),
            observed=job.observed or ObservedUsage(), display_text=text,
            answer=job.validated_answer if job.status is JobStatus.COMPLETED else None,
            input_accounting=job.input_accounting)

    def _capture_explanation_locked(self, job_id: str, epoch: str) -> tuple[
            PollState, tuple[dict[str, object], ...]]:
        job = self._jobs.get(job_id)
        if job is None:
            retired = self._completed.get(job_id)
            if (retired is None or retired.public_state is None
                    or retired.public_state.identity.project_epoch != epoch):
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            return retired.public_state, retired.events
        if job.public_identity is None or job.public_identity.project_epoch != epoch:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        # Recover the bounded ring through its cursor API; no private provider
        # event, raw ring or unvalidated serialized answer crosses this port.
        cursor = max(0, job.ring.seq - 256)
        events: list[dict[str, object]] = []
        raw = job.ring.poll(cursor)
        while cursor < job.ring.seq:
            part = cast(list[dict[str, object]], raw["events"])
            if not part:
                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
            events.extend(part)
            cursor = cast(int, part[-1]["seq"])
            raw = job.ring.poll(cursor)
        return self._explanation_state_locked(job, text=cast(str, raw["text"])), tuple(events)

    @staticmethod
    def _explanation_events(state: PollState, raw: tuple[dict[str, object], ...],
                            after_seq: int) -> tuple[DisplayEvent, ...]:
        flags = {"cancelRequested": state.lifecycle.cancel_requested,
                 "cancelAccepted": state.lifecycle.cancel_accepted,
                 "dispatchClosed": state.lifecycle.dispatch_closed,
                 "resourcesReleased": state.lifecycle.resources_released}
        converted: list[DisplayEvent] = []
        for event in reversed(raw):
            seq = cast(int, event["seq"])
            kind = event["type"]
            if kind == "confirmation":
                converted.append(DisplayEvent(seq, "lifecycle", dict(flags)))
                for key in flags.keys() & event.keys():
                    flags[key] = False
            elif kind == "text":
                converted.append(DisplayEvent(seq, "display_text", {"text": event["text"]}))
            elif kind == "status":
                if event["status"] == JobStatus.COMPLETED.value:
                    converted.append(DisplayEvent(seq, "result", {"available": True}))
                else:
                    converted.append(DisplayEvent(seq, "status", {"status": event["status"]}))
            elif kind == "result":
                converted.append(DisplayEvent(seq, "result", {"available": event["available"]}))
            else:
                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
        return tuple(event for event in reversed(converted) if event.seq > after_seq)

    def _poll_explanation(self, request: PollRequest) -> dict[str, object]:
        if request.project_epoch is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with self._lock:
            state, raw = self._capture_explanation_locked(request.job_id, request.project_epoch)
            job = self._jobs.get(request.job_id)
            publication = (job.request.expected_identity if job is not None
                           else self._completed[request.job_id].publication)
            evicted = self._conversations._pending_evictions(publication.project_key,
                cast(str, state.identity.conversation_id),
                cast(int, state.identity.conversation_revision), request.job_id)
        if request.after_seq > state.seq:
            raise ServiceError("AI_INVALID_INPUT")
        oldest = cast(int, raw[0]["seq"]) if raw else state.seq + 1
        reset = request.after_seq < oldest - 1
        events = () if reset else self._explanation_events(state, raw, request.after_seq)
        return poll_to_public(state, after_seq=request.after_seq, events=events, reset=reset,
                              evicted=evicted)

    def _cancel_explanation(self, request: CancelRequest) -> dict[str, object]:
        if request.project_epoch is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with self._lock:
            self._capture_explanation_locked(request.job_id, request.project_epoch)
            job = self._jobs.get(request.job_id)
            if job is not None and not job.resources_released:
                if job.status is not JobStatus.COMPLETED:
                    job.cancel_requested = True
                    self._sync_ring_locked(job)
            state, _raw = self._capture_explanation_locked(request.job_id, request.project_epoch)
        receipt = cancel_receipt_to_public(state)
        if job is not None and not job.resources_released:
            if job.ledger is not None:
                job.ledger.cancel()
            self._loop.call_soon_threadsafe(self._schedule_close, job.job_id)
        return receipt

    def _drop_evicted_requests_locked(self) -> None:
        for evicted_key in self._conversations.evicted_request_keys:
            old = self._requests.get(evicted_key)
            if old is not None:
                self._completed.pop(old[1], None)

    @staticmethod
    def _dependency_identity(identity: PublicationIdentity) -> tuple[object, ...]:
        return (identity.project_epoch, identity.analysis_generation,
                identity.project_key, identity.root, identity.ignore_paths,
                identity.status, identity.eligible)

    def _sync_ring_locked(self, job: _Job) -> None:
        """Service lock precedes the ring lock; no callback or I/O runs here."""
        job.ring.update_confirmations(
            cancel_requested=job.cancel_requested,
            cancel_accepted=job.cancel_accepted,
            dispatch_closed=job.dispatch_closed,
            resources_released=job.resources_released)
        job.ring.set_status(job.status)
        if (self._runtime_owned_store and job.request.expected_identity.project_key is not None
                and job.conversation_revision is not None):
            try:
                self._conversations._remember_recovery(
                    job.request.expected_identity.project_key, job.request.conversation_id,
                    job.conversation_revision, job.job_id, {
                        "projectEpoch": job.request.scope.project_epoch,
                        "analysisGeneration": job.request.scope.analysis_generation,
                        "conversationRevision": job.conversation_revision,
                        "configRevision": job.request.config_revision,
                        "consentRevision": job.request.consent_revision,
                        "seq": job.ring.seq, "status": job.status.value})
                self._drop_evicted_requests_locked()
                self._recovery_unavailable.discard(job.job_id)
            except ServiceError:
                # Cleanup must still release resources if recovery cannot fit.
                # get_state returns AI_CAPACITY rather than a partial cursor.
                self._recovery_unavailable.add(job.job_id)

    def _eligible_locked(self, request: JobRequest) -> bool:
        identity = self._identity
        return bool(identity == request.expected_identity and identity is not None
                    and identity.eligible
                    and identity.project_epoch == request.scope.project_epoch
                    and identity.analysis_generation == request.scope.analysis_generation)

    def _authority_revision_locked(self, request: JobRequest) -> int | None:
        authority = self._authority
        identity = self._identity
        if (authority is None or identity is None or not authority.config_enabled
                or authority.project_key != identity.project_key
                or authority.config_revision != request.config_revision
                or authority.grant_revision != request.consent_revision
                or authority.scope_fingerprint != request.normalized_scope
                or authority.model != request.model):
            return None
        revision = authority.conversation_revision(request.conversation_id)
        stored = self._conversations.revision_of(authority.project_key, request.conversation_id)
        return revision if stored is None or stored == revision else None

    def _job_eligible_locked(self, job: _Job) -> bool:
        return (self._eligible_locked(job.request)
                and job.conversation_revision is not None
                and self._authority is not None
                and job.authority_origin == self._authority.origin
                and job.authority_endpoint == self._authority.endpoint
                and self._authority_revision_locked(job.request) == job.conversation_revision
                and self._followup_eligible_locked(job.request))

    def _reserve_connection_slot(self) -> str:
        """Hold a connection test's global admission slot until its resources close."""
        slot = uuid.uuid4().hex
        with self._lock:
            if len(self._active_job_ids) + len(self._connection_slots) + len(self._navigation_jobs) >= 2:
                raise ServiceError("AI_BUSY")
            self._connection_slots.add(slot)
            return slot

    def _release_connection_slot(self, slot: str) -> None:
        with self._lock:
            if slot not in self._connection_slots:
                raise ServiceError("AI_INVALID_INPUT")
            if slot in self._connection_jobs:
                raise ServiceError("AI_INVALID_INPUT")
            self._connection_slots.remove(slot)

    def _replace_connection_config(self, config: ProviderConfig | None) -> None:
        """Runtime -> engine mirror. Only bounded swaps/marks under this lock."""
        with self._lock:
            self._connection_config = config
            for job in self._connection_jobs.values():
                if job.config != config:
                    self._request_connection_close_locked(job)
                    self._pending_connection_closures.add(job.job_id)

    @staticmethod
    def _connection_state_locked(job: _ConnectionJob) -> PollState:
        capabilities = (
            _copy_connection_capabilities(job.connection_capabilities)
            if (job.status is JobStatus.COMPLETED
                and job.connection_capabilities is not None)
            else None
        )
        return PollState(
            PollIdentity("connection", job.job_id, None, None, None, None),
            job.snapshot(), job.config.revision, None, job.seq,
            usage=job.resources.ledger.usage, observed=job.observed or ObservedUsage(),
            connection_supported=job.status is JobStatus.COMPLETED,
            input_accounting=job.input_accounting,
            connection_capabilities=capabilities)

    @staticmethod
    def _connection_event_locked(job: _ConnectionJob, kind: Literal[
            "status", "usage", "result", "lifecycle"], payload: dict[str, object]) -> None:
        job.seq += 1
        job.events.append(DisplayEvent(job.seq, kind, payload))

    def _connection_lifecycle_locked(self, job: _ConnectionJob) -> None:
        self._connection_event_locked(job, "lifecycle", {
            "cancelRequested": job.cancel_requested, "cancelAccepted": job.cancel_accepted,
            "dispatchClosed": job.dispatch_closed, "resourcesReleased": job.resources_released})

    def _request_connection_close_locked(self, job: _ConnectionJob) -> None:
        # Completed results remain Completed. C7 disallows a requested
        # cancellation on Completed; cancel then retries only resource cleanup.
        if job.status is not JobStatus.COMPLETED and not job.cancel_requested:
            job.cancel_requested = True
            self._connection_lifecycle_locked(job)

    def _connection_replay_locked(self, request: ConnectionRequest) -> dict[str, object] | None:
        config = self._connection_config
        if config is None or config.revision != request.config_revision:
            raise ServiceError("AI_NOT_CONFIGURED")
        old_id = self._connection_requests.get((request.config_revision, request.request_id))
        if old_id is not None:
            active = self._connection_jobs.get(old_id)
            retired = self._connection_completed.get(old_id)
            if active is None and retired is None:
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            status = active.status if active is not None else cast(_RetiredConnection, retired).state.lifecycle.status
            return {"jobId": old_id, "status": status.value, "scope": None,
                    "conversationId": None, "kind": "connection"}
        if len(self._connection_requests) >= 1024:
            raise ServiceError("AI_REQUEST_LIMIT")
        if (len(self._active_job_ids) + len(self._connection_slots) + len(self._navigation_jobs) >= 2
                or any(job.config.revision == config.revision
                       for job in self._connection_jobs.values())):
            raise ServiceError("AI_CAPACITY")
        return None

    def _start_connection(self, request: ConnectionRequest,
                          provider_factory: Callable[[], ProviderAdapter]) -> dict[str, object]:
        with self._lock:
            replay = self._connection_replay_locked(request)
            if replay is not None:
                return replay
        admitted_at = monotonic()
        resources = _ConnectionResources(admitted_at=admitted_at, clock=monotonic,
                                         deadline=admitted_at + 15.0)
        job_id = uuid.uuid4().hex
        with self._lock:
            replay = self._connection_replay_locked(request)
            if replay is not None:
                return replay
            config = self._connection_config
            if config is None:
                raise ServiceError("AI_NOT_CONFIGURED")
            job = _ConnectionJob(job_id, config, request.request_id, resources, provider_factory)
            self._connection_jobs[job_id] = job
            self._connection_slots.add(job_id)
            self._connection_requests[(config.revision, request.request_id)] = job_id
        try:
            self._loop.call_soon_threadsafe(self._launch_connection, job_id)
        except Exception:
            # No provider was constructed. Keep the tombstone and slot until
            # the resource owner actually confirms cleanup on the loop.
            with self._lock:
                job.dispatch_closed = True
                job.status = JobStatus.FAILED
                job.issue = issue_for_code("AI_DEPENDENCY_MISSING")
                job.provider_factory = None
                self._connection_lifecycle_locked(job)
                self._connection_event_locked(job, "status", {"status": job.status.value})
            raise ServiceError("AI_DEPENDENCY_MISSING") from None
        return {"jobId": job_id, "status": "Queued", "scope": None,
                "conversationId": None, "kind": "connection"}

    def _launch_connection(self, job_id: str) -> None:
        with self._lock:
            job = self._connection_jobs.get(job_id)
            if job is None or job.task is not None:
                return
            if job.cancel_requested or job.dispatch_closed:
                self._loop.create_task(self._close_connection(job))
                return
            job.status = JobStatus.RUNNING
            self._connection_event_locked(job, "status", {"status": job.status.value})
            job.task = self._loop.create_task(self._work_connection(job))

    def _connection_guard(self, job: _ConnectionJob, prepared: PreparedRequest) -> None:
        if (prepared.endpoint != job.config.endpoint or prepared.method != "POST"
                or json.loads(prepared.body).get("model") != job.config.model):
            raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
        with self._lock:
            if (self._connection_jobs.get(job.job_id) is not job or job.cancel_requested
                    or job.dispatch_closed or self._connection_config != job.config):
                raise ServiceError("AI_CANCELLED")

    async def _work_connection(self, job: _ConnectionJob) -> None:
        try:
            factory = job.provider_factory
            if factory is None:
                raise ServiceError("AI_DEPENDENCY_MISSING")
            binding = _select_connection_profile(job.config, self._trusted_connection_profiles)
            result = await run_connection(job.resources, factory, self._before_dispatch,
                                          partial(self._connection_guard, job),
                                          trusted_profiles=self._trusted_input_profiles,
                                          connection_profile=binding)
            factory = None  # Do not retain the frozen key through teardown/history.
            with self._lock:
                job.provider_factory = None
                job.usage = result.usage
                job.observed = result.observed
                job.input_accounting = result.input_accounting
                if job.cancel_requested or self._connection_config != job.config:
                    self._request_connection_close_locked(job)
                else:
                    capabilities = (
                        None if result.connection_capabilities is None else
                        _copy_connection_capabilities(result.connection_capabilities)
                    )
                    job.connection_capabilities = capabilities
                    job.dispatch_closed = True
                    self._connection_lifecycle_locked(job)
                    job.status = JobStatus.COMPLETED
                    self._connection_event_locked(job, "status", {"status": job.status.value})
                    usage = snapshot_to_public(self._connection_state_locked(job))["usage"]
                    self._connection_event_locked(job, "usage", {"usage": usage})
                    self._connection_event_locked(job, "result", {"available": True})
        except asyncio.CancelledError:
            return  # The loop close command owns cancellation confirmation.
        except Exception as error:
            factory = None
            observed = job.resources.observed
            input_accounting = job.resources.input_accounting
            code, diagnostic = _connection_failure(error)
            with self._lock:
                job.provider_factory = None
                job.usage = job.resources.ledger.usage
                job.observed = observed
                job.input_accounting = input_accounting
                if not job.cancel_requested:
                    job.issue = issue_for_code(code, diagnostic)
                    job.dispatch_closed = True
                    self._connection_lifecycle_locked(job)
                    job.status = JobStatus.FAILED
                    self._connection_event_locked(job, "status", {"status": job.status.value})
        await self._close_connection(job, cancel_task=False)

    def _schedule_connection_close(self, job_id: str) -> None:
        with self._lock:
            job = self._connection_jobs.get(job_id)
        if job is not None:
            self._loop.create_task(self._close_connection(job))

    async def _close_connection(self, job: _ConnectionJob, *, cancel_task: bool = True) -> None:
        with self._lock:
            if self._connection_jobs.get(job.job_id) is not job or job.closing:
                return
            job.closing = True
            if not job.dispatch_closed:
                job.dispatch_closed = True
                self._connection_lifecycle_locked(job)
            task = job.task
        try:
            if cancel_task and task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            job.provider_factory = None
            observed = job.resources.observed
            input_accounting = job.resources.input_accounting
            with self._lock:
                job.observed = observed
                job.input_accounting = input_accounting
            job.resources.ledger.cancel()
            await job.resources.cancel_and_close()
        except Exception:
            return  # Keep the global slot and unconfirmed state on failed cleanup.
        finally:
            with self._lock:
                job.closing = False
        with self._lock:
            if not job.resources.resources_released:
                return
            job.resources_released = True
            job.usage = job.resources.ledger.usage
            if job.cancel_requested:
                job.cancel_accepted = True
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.status = JobStatus.CANCELLED
                    self._connection_event_locked(job, "status", {"status": job.status.value})
            self._connection_lifecycle_locked(job)
            state = self._connection_state_locked(job)
            self._connection_completed[job.job_id] = _RetiredConnection(
                state,
                tuple(job.events),
                job.input_accounting,
            )
            while len(self._connection_completed) > 128:
                self._connection_completed.popitem(last=False)
            self._connection_jobs.pop(job.job_id, None)
            self._connection_slots.discard(job.job_id)
            self._pending_connection_closures.discard(job.job_id)
            job.task = None

    def _capture_connection_locked(self, job_id: str) -> tuple[PollState, tuple[DisplayEvent, ...]]:
        job = self._connection_jobs.get(job_id)
        if job is not None:
            return self._connection_state_locked(job), tuple(job.events)
        retired = self._connection_completed.get(job_id)
        if retired is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        return retired.state, retired.events

    def _poll_connection(self, request: PollRequest) -> dict[str, object]:
        with self._lock:
            state, history = self._capture_connection_locked(request.job_id)
        if request.after_seq > state.seq:
            raise ServiceError("AI_INVALID_INPUT")
        oldest = history[0].seq if history else state.seq + 1
        reset = request.after_seq < oldest - 1
        events = () if reset else tuple(event for event in history if event.seq > request.after_seq)
        return poll_to_public(state, after_seq=request.after_seq, events=events, reset=reset)

    def _cancel_connection(self, request: CancelRequest) -> dict[str, object]:
        with self._lock:
            job = self._connection_jobs.get(request.job_id)
            if job is not None:
                self._request_connection_close_locked(job)
            state, _history = self._capture_connection_locked(request.job_id)
        receipt = cancel_receipt_to_public(state)
        if job is not None:
            job.resources.ledger.cancel()
            self._loop.call_soon_threadsafe(self._schedule_connection_close, request.job_id)
        return receipt

    def _admission_replay_locked(self, request: JobRequest, digest: str,
                                 key: tuple[str, str]) -> JobSnapshot | None:
        if self._identity is None or self._identity.project_epoch != request.scope.project_epoch:
            raise ServiceError("AI_ANALYSIS_REQUIRED")
        replay = self._requests.get(key)
        if replay is not None:
            old_digest, job_id = replay
            if digest != old_digest:
                raise ServiceError("AI_REQUEST_CONFLICT")
            active = self._jobs.get(job_id)
            if active is not None:
                return active.snapshot()
            completed = self._completed.get(job_id)
            if completed is None:
                raise ServiceError("AI_CONVERSATION_CLEARED")
            return completed.snapshot
        if self._conversations.tombstone(key) is not None:
            raise ServiceError("AI_CONVERSATION_CLEARED")
        if not self._eligible_locked(request):
            raise ServiceError("AI_ANALYSIS_REQUIRED")
        if self._authority_revision_locked(request) is None:
            raise ServiceError("AI_CONSENT_REQUIRED")
        if len(self._active_job_ids) + len(self._connection_slots) + len(self._navigation_jobs) >= 2:
            raise ServiceError("AI_BUSY")
        if any(self._jobs[job_id].request.scope.project_epoch == request.scope.project_epoch
               for job_id in self._active_job_ids):
            raise ServiceError("AI_BUSY")
        if len(self._requests) >= 1024:
            raise ServiceError("AI_REQUEST_LIMIT")
        return None

    def _fail_admission_schedule(self, job: _Job) -> None:
        with self._lock:
            job.admission_pending = False
            if self._jobs.get(job.job_id) is not job:
                return
            job.status = (JobStatus.CANCELLED if job.cancel_requested
                          else JobStatus.FAILED)
            job.dispatch_closed = True
            job.resources_released = not job.b_submitting and job.b_future is None
            job.cancel_accepted = job.cancel_requested and job.resources_released
            self._sync_ring_locked(job)
            self._pending_closures.discard(job.job_id)
            if job.resources_released:
                self._retire_job_locked(job)

    def start(self, request: JobRequest) -> JobSnapshot:
        snapshot, admitted = self._admit_job(request)
        if admitted is not None:
            self._schedule_admitted(admitted)
        return snapshot

    def _admit_job(self, request: JobRequest, *,
                   provider_factory: Callable[[], ProviderAdapter] | None = None,
                   expected_conversation_revision: int | None = None,
                   prepared_digest: str | None = None, prepared_at: float | None = None,
                   prepared_job_id: str | None = None) -> tuple[JobSnapshot, _Job | None]:
        """Private atomic insertion; runtime schedules only after dropping its lock."""
        if type(request) is not JobRequest:
            raise ServiceError("AI_INVALID_INPUT")
        request.__post_init__()
        if request.budget is None and self._explanation_budget is not None:
            request = replace(request, budget=replace(self._explanation_budget))
        elif request.budget != self._explanation_budget:
            raise ServiceError("AI_INVALID_INPUT")
        digest = request.digest()
        if prepared_digest is not None and prepared_digest != digest:
            raise ServiceError("AI_INVALID_INPUT")
        key = (request.scope.project_epoch, request.scope.request_id)
        with self._lock:
            replay = self._admission_replay_locked(request, digest, key)
            if replay is not None:
                return replay, None
        job_id = uuid.uuid4().hex if prepared_job_id is None else prepared_job_id
        ring = EventRing(job_id, kind="explanation")
        admitted_at = monotonic() if prepared_at is None else prepared_at
        with self._lock:
            replay = self._admission_replay_locked(request, digest, key)
            if replay is not None:
                return replay, None
            if (expected_conversation_revision is not None
                    and self._authority_revision_locked(request) != expected_conversation_revision):
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            timeout_seconds = (90.0 if request.budget is None else
                               request.budget.timeout_seconds)
            if provider_factory is not None:
                request = replace(request, draft=replace(
                    request.draft, deadline=admitted_at + timeout_seconds))
            job = _Job(job_id, request, digest, ring)
            job.analysis_snapshot = self._snapshot
            job.conversation_revision = self._authority_revision_locked(request)
            job.provider_factory = provider_factory
            if provider_factory is not None:
                job.public_identity = PollIdentity("explanation", job_id, request.scope.project_epoch,
                    request.scope.analysis_generation, request.conversation_id, job.conversation_revision)
            job.authority_origin = self._authority.origin if self._authority else None
            job.authority_endpoint = self._authority.endpoint if self._authority else None
            job.admitted_at = admitted_at
            job.deadline = admitted_at + timeout_seconds
            authority = self._authority
            if authority is None or job.conversation_revision is None:
                raise ServiceError("AI_CONSENT_REQUIRED")
            self._conversations.reserve_append(
                authority.project_key, request.conversation_id, job.conversation_revision,
                messages=({"role": "user", "content": request.question,
                           "contextDigest": request.context_digest, "jobId": job_id,
                           "scope": {"projectEpoch": request.scope.project_epoch,
                                     "origin": authority.origin,
                                     "generation": request.scope.analysis_generation,
                                     "consentRevision": request.consent_revision,
                                     "configRevision": request.config_revision,
                                     "normalizedScope": request.normalized_scope},
                           "sourceTags": []},),
                active_job_id=job_id, request_key=key, digest=digest, create=True)
            self._drop_evicted_requests_locked()
            self._jobs[job_id] = job
            if self._validation_diagnostics is not None:
                try:
                    job.validation_capture = self._validation_diagnostics.claim()
                except Exception:
                    pass
            self._active_job_ids.add(job_id)
            self._requests[key] = (digest, job_id)
            if self._runtime_owned_store:
                self._sync_ring_locked(job)
            snapshot = job.snapshot()
        return snapshot, job

    def _schedule_admitted(self, job: _Job) -> None:
        job_id = job.job_id
        try:
            self._loop.call_soon_threadsafe(self._launch, job_id)
            with self._lock:
                job.admission_pending = False
                launch_waiting = job.launch_waiting and self._jobs.get(job_id) is job
            if launch_waiting:
                self._loop.call_soon_threadsafe(self._launch, job_id)
        except Exception:
            self._fail_admission_schedule(job)
            raise ServiceError("AI_SCHEDULE_FAILED") from None

    def get(self, job_id: str) -> JobSnapshot:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None and job_id not in self._completed:
                raise ServiceError("AI_JOB_NOT_FOUND")
            return job.snapshot() if job is not None else self._completed[job_id].snapshot

    def poll(self, job_id: str, after_seq: int) -> dict[str, object]:
        """Return live deltas or the compact authoritative retired state."""
        if type(after_seq) is not int or after_seq < 0:
            raise ServiceError("AI_INVALID_INPUT")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                if after_seq > job.ring.seq:
                    raise ServiceError("AI_INVALID_INPUT")
                return job.ring.poll(after_seq)
            retired = self._completed.get(job_id)
            if retired is None:
                raise ServiceError("AI_JOB_NOT_FOUND")
            if after_seq > retired.seq:
                raise ServiceError("AI_INVALID_INPUT")
            snapshot = retired.snapshot
            oldest = (cast(int, retired.events[0]["seq"]) if retired.events
                      else retired.seq + 1)
            reset = after_seq < oldest - 1
            events = ([] if reset else [dict(event) for event in retired.events
                                        if cast(int, event["seq"]) > after_seq])
            return {"jobId": snapshot.job_id, "kind": retired.kind,
                    "seq": retired.seq, "status": snapshot.status.value,
                    "cancelRequested": snapshot.cancel_requested,
                    "cancelAccepted": snapshot.cancel_accepted,
                    "dispatchClosed": snapshot.dispatch_closed,
                    "resourcesReleased": snapshot.resources_released,
                    "reset": reset, "events": events,
                    "text": "", "answer": None}

    def cancel(self, job_id: str) -> JobSnapshot:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                retired = self._completed.get(job_id)
                if retired is None:
                    raise ServiceError("AI_JOB_NOT_FOUND")
                return retired.snapshot
            if job.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED,
                                  JobStatus.FAILED, JobStatus.STALE) or not job.resources_released:
                job.cancel_requested = True
                self._sync_ring_locked(job)
            snapshot = job.snapshot()
        if snapshot.cancel_requested and job.ledger is not None:
            job.ledger.cancel()
        if snapshot.cancel_requested and not snapshot.cancel_accepted:
            self._loop.call_soon_threadsafe(self._schedule_close, job_id)
        return snapshot

    def _launch(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            pending = job.admission_pending
            if pending:
                job.launch_waiting = True
            else:
                job.launch_waiting = False
                cancelled = job.cancel_requested or job.dispatch_closed
                if not cancelled:
                    job.status = JobStatus.RUNNING
                    self._sync_ring_locked(job)
        if pending:
            return
        if not cancelled:
            job.task = self._loop.create_task(self._work(job))

    def _schedule_close(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            self._loop.create_task(self._close_job(job))

    async def _run_b(self, job: _Job, operation: Callable[[], _T]) -> _T:
        """Run one trusted local B operation with a lease held until its OS future ends."""
        with self._lock:
            if (self._jobs.get(job.job_id) is not job or job.cancel_requested
                    or job.dispatch_closed or not self._job_eligible_locked(job)):
                raise ServiceError("AI_CANCELLED")
            if job.b_submitting or job.b_future is not None or self._b_outstanding >= 2:
                raise ServiceError("AI_BUSY")
            job.b_submitting = True
            self._b_outstanding += 1

        def guarded_operation() -> _T:
            with self._lock:
                if (job.cancel_requested or job.dispatch_closed
                        or not self._job_eligible_locked(job)):
                    raise ServiceError("AI_CANCELLED")
            return operation()

        try:
            raw = self._b_executor.submit(guarded_operation)
        except Exception:
            with self._lock:
                job.b_submitting = False
                self._b_outstanding -= 1
            raise ServiceError("AI_SCHEDULE_FAILED") from None

        with self._lock:
            job.b_future = raw
            job.b_submitting = False
            cancelled = job.cancel_requested or job.dispatch_closed

        def finished(future: Future[_T]) -> None:
            release_terminal = False
            with self._lock:
                if job.b_future is future:
                    job.b_future = None
                    self._b_outstanding -= 1
                    if (job.dispatch_closed and not job.cancel_requested
                            and job.status in (JobStatus.COMPLETED, JobStatus.FAILED,
                                               JobStatus.STALE)
                            and not job.resources_released):
                        release_terminal = True
            if release_terminal:
                self._loop.call_soon_threadsafe(self._release_terminal_job, job)

        raw.add_done_callback(finished)
        if cancelled:
            raw.cancel()
        return await asyncio.wrap_future(raw)

    def _release_terminal_job(self, job: _Job) -> None:
        """Worker completion is not provider/session cleanup confirmation."""
        with self._lock:
            if (self._jobs.get(job.job_id) is not job or job.cancel_requested
                    or job.resources_released or job.b_submitting or job.b_future is not None
                    or not job.dispatch_closed
                    or job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STALE)
                    or (job.provider is not None and not job.provider_released)):
                return
        if job.tool_session is not None:
            job.tool_session.close()
            job.tool_session = None
        with self._lock:
            if job.cancel_requested:
                return  # Cancellation owns its acknowledgement.
            job.resources_released = True
            self._sync_ring_locked(job)
            self._retire_job_locked(job)

    async def _assert_fresh(self, job: _Job, ledger: BudgetLedger) -> None:
        snapshot = job.analysis_snapshot
        if snapshot is None:
            raise ServiceError("AI_STALE")

        def checkpoint() -> None:
            ledger.reserve(BudgetReservation())
            with self._lock:
                if (job.cancel_requested or job.dispatch_closed
                        or not self._job_eligible_locked(job)):
                    raise ServiceError("AI_CANCELLED")

        report = await self._run_b(job, lambda: verify_snapshot_freshness(
            snapshot, scope=job.request.scope, checkpoint=checkpoint))
        if report.status is not FreshnessStatus.FRESH:
            raise ServiceError("AI_STALE")

    def _failure_status_locked(self, job: _Job, error: Exception) -> JobStatus:
        if isinstance(error, ServiceError) and (
                error.code == "AI_STALE" or
                (error.code == "AI_CANCELLED" and not self._job_eligible_locked(job))):
            return JobStatus.STALE
        return JobStatus.FAILED

    def _retain_failed_evidence_locked(
        self, job: _Job, source_consent: SourceConsent | None,
        snapshots: dict[str, FailedEvidenceSnapshot],
    ) -> None:
        """Atomically retain inert metadata only while the original authority is current."""
        if (not snapshots or source_consent is None
                or self._source_consent is not source_consent
                or not self._job_eligible_locked(job)
                or job.conversation_revision is None):
            return
        authority = self._authority
        if authority is None:
            return
        evidence: dict[str, object] = {}
        bindings: dict[str, object] = {}
        for evidence_id, snapshot in sorted(snapshots.items()):
            if snapshot.evidence_id != evidence_id:
                return
            evidence[evidence_id], bindings[evidence_id] = snapshot.storage_entries()
        try:
            self._conversations.reserve_append(
                authority.project_key, job.request.conversation_id,
                job.conversation_revision, evidence=evidence, bindings=bindings,
                _delivery_job_id=job.job_id)
        except ServiceError:
            return  # Failed evidence retention is best-effort under the existing global cap.
        self._drop_evicted_requests_locked()

    async def _work(self, job: _Job) -> None:
        close_started = False
        provider_closed = False
        source_consent: SourceConsent | None = None
        failed_evidence: dict[str, FailedEvidenceSnapshot] = {}
        possibly_dispatched_files: set[str] = set()
        try:
            first_clock = True

            def admission_clock() -> float:
                nonlocal first_clock
                if first_clock:
                    first_clock = False
                    return job.admitted_at
                return monotonic()

            budget = job.request.budget
            limits = BudgetLimits() if budget is None else budget.limits()
            ledger = (BudgetLedger(clock=admission_clock) if budget is None else
                      BudgetLedger(limits, clock=admission_clock))
            with self._lock:
                prior_origin = (None if job.request.understanding is None else self._prior_interpretation_locked(job.request))
            interpretations = () if prior_origin is None else (prior_origin,)
            send_policy = _SendPolicy(
                ledger,
                trusted_profiles=self._trusted_input_profiles,
                explanation_budget=budget,
                trusted_interpretations=interpretations,
            )
            input_planner = InputBudgetPlanner(
                20_000 if budget is None else budget.max_input_tokens)
            source_planner = (SourceHeadroomPlanner(limits) if not interpretations else
                              SourceHeadroomPlanner(limits, trusted_interpretations=interpretations))

            def sync_accounting() -> None:
                observed = send_policy.observed
                input_accounting = send_policy.snapshot
                with self._lock:
                    job.observed = observed
                    job.input_accounting = input_accounting

            with self._lock:
                job.ledger = ledger
                cancelled = job.cancel_requested
            if cancelled:
                ledger.cancel()
            ledger.reserve(BudgetReservation())
            async with asyncio.timeout_at(job.deadline):
                with self._lock:
                    snapshot = job.analysis_snapshot
                    source_consent = self._source_consent
                    current_authority = self._authority
                if (snapshot is None or source_consent is None or current_authority is None
                        or source_consent.binding != snapshot.binding
                        or source_consent.provider_origin != job.authority_origin
                        or hashlib.sha256(json.dumps(
                            source_consent.allowed_files, ensure_ascii=True,
                            separators=(",", ":")).encode("ascii")).hexdigest()
                        != job.request.normalized_scope):
                    raise ServiceError("AI_CONSENT_REQUIRED")

                def current_source_state() -> SourceReadState:
                    with self._lock:
                        return SourceReadState(
                            job.request.scope, self._source_consent,
                            job.cancel_requested or not self._job_eligible_locked(job))

                registry = EvidenceRegistry(job.request.scope)
                session = create_tool_session(
                    snapshot, scope=job.request.scope, consent=source_consent,
                    ledger=ledger, registry=registry, current_state=current_source_state)
                job.tool_session = session
                provided: dict[str, ChosenTarget] = {}
                coverage_gaps: list[str] = []
                draft = job.request.draft
                if prior_origin is not None:
                    draft = inject_interpretation(draft, prior_origin)
                root_fragment: RootSourceFragment | None = None
                root_wire: tuple[str, str, int] | None = None
                retrieval = job.request.root_retrieval
                if retrieval is not None:
                    def retrieve_root() -> RootSourceFragment:
                        anchor = retrieval.anchor
                        result = session.dispatch("read_source_excerpt", {
                            "file_path": anchor.file_path, "start_line": anchor.start_line,
                            "start_column": anchor.start_column, "end_line": anchor.end_line,
                            "end_column": anchor.end_column})
                        if type(result) is not ExcerptToolResult:
                            raise ResponseError()
                        session.bind_evidence(result.evidence_id, retrieval.target)
                        proof = session.lookup_binding(result.evidence_id, retrieval.target)
                        record = registry.lookup(result.evidence_id, scope=job.request.scope,
                                                 expected_file_hash=proof.content_hash)
                        failed_evidence[result.evidence_id] = session._failed_evidence_snapshot(
                            result.evidence_id, retrieval.target, proof, record)
                        return RootSourceFragment.from_result(result)

                    root_fragment = await self._run_b(job, retrieve_root)
                    draft, pointer, content, start = inject_root(draft, root_fragment)
                    root_wire = (pointer, content, start)
                transcript = _protocol_boundary(
                    lambda: ToolLoopTranscript.maybe_create(draft), "pre_send")
                trusted_targets: list[TrustedTarget] = []
                frozen_catalog: tuple[EvidenceIdEntry, ...] | None = None

                def retained_excerpt(
                    item: _PendingToolProjection, projection: ToolProjection,
                ) -> ExcerptToolResult | None:
                    result = item.result
                    return (result if type(result) is ExcerptToolResult
                            and projection.source_spans else None)

                def excerpt_target(
                    item: _PendingToolProjection, result: ExcerptToolResult,
                    candidates: tuple[TrustedTarget, ...],
                ) -> ChosenTarget:
                    if item.preferred_relation is not None:
                        if job.request.purpose != "project_understanding" and job.request.understanding is None:
                            raise ResponseError()
                        # Never recover a key from an omitted original result or a node.
                        candidates = tuple(candidate for candidate in candidates
                                           if candidate.target == item.preferred_relation)
                    return choose_target(result.returned_anchor, candidates)

                def registered_entry(evidence_id: str, target: ChosenTarget) -> EvidenceIdEntry:
                    proof = session.lookup_binding(evidence_id, target)
                    record = registry.lookup(
                        evidence_id, scope=job.request.scope,
                        expected_file_hash=proof.content_hash)
                    if (proof.evidence_id != evidence_id or proof.target != target
                            or record.anchor.file_path != proof.returned_anchor.file_path):
                        raise ResponseError()
                    return EvidenceIdEntry(
                        evidence_id, record.anchor.file_path, target,
                        proof.status == "bound" and type(proof.target) is RelationKey)

                def guidance_catalog(
                    pending: tuple[_PendingToolProjection, ...] = (),
                    projections: tuple[ToolProjection, ...] = (),
                    *, include_root: bool = True,
                ) -> tuple[EvidenceIdEntry, ...]:
                    """Forecast only selected typed receipts; never register or bind here."""
                    if len(pending) != len(projections):
                        raise ResponseError()
                    entries = {identifier: registered_entry(identifier, target)
                               for identifier, target in provided.items()}

                    def add(entry: EvidenceIdEntry) -> None:
                        if entry.evidence_id in entries and entries[entry.evidence_id] != entry:
                            raise ResponseError()
                        entries[entry.evidence_id] = entry

                    if (include_root and root_fragment is not None and root_wire is not None
                            and retrieval is not None):
                        entry = registered_entry(root_fragment.evidence_id, retrieval.target)
                        if entry.relative_path != root_fragment.relative_path:
                            raise ResponseError()
                        add(entry)
                    candidates = list(trusted_targets)
                    for item, projection in zip(pending, projections, strict=True):
                        result = retained_excerpt(item, projection)
                        if result is not None:
                            target = excerpt_target(item, result, tuple(candidates))
                            add(EvidenceIdEntry(result.evidence_id, result.relative_path, target,
                                                type(target) is RelationKey))
                        for candidate in projection.targets:
                            if all(existing.target != candidate.target for existing in candidates):
                                candidates.append(candidate)
                    return tuple(entries[identifier] for identifier in sorted(entries))

                def verify_catalog_commit() -> None:
                    if (frozen_catalog is not None
                            and guidance_catalog(include_root=False) != frozen_catalog):
                        raise ResponseError()

                def allocation(phase: Literal["ordinary", "final", "repair"]) -> int:
                    value = output_allocation(
                        ledger.usage,
                        phase,
                        job.request.draft.max_output_tokens,
                        config=budget,
                    )
                    max_model_requests = (6 if budget is None else
                                          budget.max_model_requests)
                    if value is None or ledger.usage.model_requests >= max_model_requests:
                        raise ServiceError("AI_BUDGET_EXCEEDED")
                    return value

                def projection_gap(projection: ToolProjection) -> str | None:
                    try:
                        payload = json.loads(projection.content)["result"]
                        omitted = payload.get("omittedCount", 0)
                        types = payload.get("omittedTypes", [])
                        coverage_gap = payload.get("coverageGap")
                        if (type(payload) is not dict or type(types) is not list
                                or any(type(value) is not str for value in types)
                                or (coverage_gap is not None
                                    and type(coverage_gap) is not str)):
                            raise ValueError()
                        count = (sum(omitted.values()) if type(omitted) is dict
                                 and all(type(value) is int for value in omitted.values())
                                 else omitted)
                        if type(count) is not int or count <= 0:
                            return coverage_gap
                        detail = (f"Retrieval omitted {count} item(s) of type: "
                                  + ", ".join(types) + ".")
                        return detail if coverage_gap is None else f"{coverage_gap} {detail}"
                    except (AttributeError, KeyError, TypeError, ValueError,
                            json.JSONDecodeError):
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE") from None

                def fit_projections(
                    calls: tuple[ToolCall, ...],
                    pending: tuple[_PendingToolProjection, ...],
                    *, project: bool = False,
                ) -> tuple[ToolProjection, ...]:
                    if (transcript is None or (not project and len(calls) != len(pending))
                            or (project and calls)):
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")

                    def capped(item: _PendingToolProjection) -> ToolProjection:
                        maximum = None if project or type(item.result) is ExcerptToolResult else 3_072
                        return project_tool_result(
                            item.result,
                            snapshot=snapshot,
                            maximum_bytes=maximum,
                        )

                    selected = [capped(item) for item in pending]
                    final_limit = allocation("final")

                    def fits(values: list[ToolProjection]) -> bool:
                        catalog = guidance_catalog(pending, tuple(values))
                        if project:
                            preview_transcript = ToolLoopTranscript(transcript.current(final_limit))
                            preview_transcript.add_project_results(tuple(values), final_limit)
                            inputs = ((preview_transcript.preview_final(final_limit, catalog),)
                                      if budget is None else preview_transcript.preview_completion(
                                          final_limit, allocation("repair"), catalog))
                            preview = inputs[0]
                        elif budget is None:
                            preview = transcript.preview_tool_results_final(
                                calls, tuple(values), final_limit, catalog)
                            inputs = (preview,)
                        else:
                            inputs = transcript.preview_tool_results_completion(
                                calls, tuple(values), final_limit, allocation("repair"), catalog)
                            preview = inputs[0]
                        return (input_planner.fits(ledger.usage, *inputs)
                                and source_planner.fits(ledger.usage, preview, preview))

                    if fits(selected):
                        return tuple(selected)
                    floors = [project_tool_result(
                        item.result,
                        snapshot=snapshot,
                        maximum_bytes=512,
                    ) for item in pending]

                    def complete_omission(
                        item: _PendingToolProjection,
                        upper: ToolProjection,
                    ) -> ToolProjection:
                        low = 1
                        high = len(upper.content.encode("utf-8"))
                        best: ToolProjection | None = None
                        while low <= high:
                            middle = (low + high) // 2
                            try:
                                candidate = project_tool_result(
                                    item.result,
                                    snapshot=snapshot,
                                    maximum_bytes=middle,
                                )
                            except ValueError:
                                low = middle + 1
                                continue
                            best = candidate
                            high = middle - 1
                        if (best is None or best.source_spans or best.metadata_spans
                                or best.targets):
                            raise ServiceError("AI_BUDGET_EXCEEDED")
                        return best

                    if fits(floors):
                        selected = floors
                    else:
                        omissions = [complete_omission(item, floor)
                                     for item, floor in zip(pending, floors, strict=True)]
                        if not fits(omissions):
                            raise ServiceError("AI_BUDGET_EXCEEDED")
                        selected = omissions
                    order = sorted(
                        range(len(pending)),
                        key=lambda index: type(pending[index].result) is not ExcerptToolResult,
                    )
                    for index in order:
                        upper_projection = capped(pending[index])
                        low = len(selected[index].content.encode("utf-8"))
                        high = len(upper_projection.content.encode("utf-8"))
                        best = selected[index]
                        while low <= high:
                            middle = (low + high) // 2
                            try:
                                candidate = project_tool_result(
                                    pending[index].result,
                                    snapshot=snapshot,
                                    maximum_bytes=middle,
                                )
                            except ValueError:
                                low = middle + 1
                                continue
                            attempt = list(selected)
                            attempt[index] = candidate
                            if fits(attempt):
                                best = candidate
                                low = middle + 1
                            else:
                                high = middle - 1
                        selected[index] = best
                    return tuple(selected)

                def register_forwarded(
                    pending: tuple[_PendingToolProjection, ...],
                    projections: tuple[ToolProjection, ...],
                ) -> None:
                    for item, projection in zip(pending, projections, strict=True):
                        gap = projection_gap(projection)
                        if gap is not None and gap not in coverage_gaps:
                            coverage_gaps.append(gap)
                        if gap is not None and job.project_coverage is not None:
                            with self._lock:
                                job.project_coverage = job.project_coverage.with_gap("retrieval_gap")
                        result = retained_excerpt(item, projection)
                        if result is not None:
                            target = excerpt_target(item, result, tuple(trusted_targets))
                            session.bind_evidence(result.evidence_id, target)
                            proof = session.lookup_binding(result.evidence_id, target)
                            record = registry.lookup(
                                result.evidence_id,
                                scope=job.request.scope,
                                expected_file_hash=proof.content_hash,
                            )
                            failed_evidence[result.evidence_id] = (
                                session._failed_evidence_snapshot(
                                    result.evidence_id, target, proof, record))
                            previous = provided.get(result.evidence_id, target)
                            if previous != target:
                                raise ResponseError()
                            provided[result.evidence_id] = target
                        for candidate in projection.targets:
                            if all(existing.target != candidate.target
                                   for existing in trusted_targets):
                                trusted_targets.append(candidate)

                if job.request.purpose == "project_understanding" or job.request.understanding is not None:
                    if transcript is None:
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")

                    def retrieve_project() -> None:
                        plan = plan_project(snapshot, source_consent, session._checkpoint)
                        with self._lock:
                            job.project_coverage = plan.coverage
                        pending: list[_PendingToolProjection] = []
                        caller_spans: list[AnchorRecord] = []
                        call_witness_read = False
                        for representative in plan.representatives:
                            if ledger.usage.tool_calls + 3 > limits.max_tool_calls:
                                with self._lock:
                                    job.project_coverage = job.project_coverage.with_gap("retrieval_gap")
                                break
                            seed = session.dispatch("search_symbols", {
                                "query": representative.qualified_name, "limit": 20})
                            if type(seed) is not SymbolSearchResult:
                                raise ResponseError()
                            matches = tuple(hit for hit in seed.results
                                if hit.ref == EntityRef("symbol", representative.id)
                                and (hit.name, hit.qualified_name, hit.kind, hit.scope, hit.anchor)
                                == (representative.name, representative.qualified_name,
                                    representative.kind.value, representative.scope, representative.source_anchor))
                            if len(matches) != 1:
                                with self._lock:
                                    job.project_coverage = job.project_coverage.with_gap("unverified")
                                continue
                            pending.append(_PendingToolProjection(SymbolSearchResult(
                                matches, seed.total_count, seed.truncated)))
                            relations = session.dispatch("get_local_relations", {
                                "seed": {"kind": "symbol", "id": representative.id},
                                "hops": 1, "max_nodes": 8, "max_edges": 8})
                            if type(relations) is not LocalRelationsResult:
                                raise ResponseError()
                            pending.append(_PendingToolProjection(relations))
                            witness = (None if call_witness_read else
                                       cross_file_call_witness(relations, tuple(caller_spans)))
                            if witness is not None and ledger.usage.tool_calls + 2 <= limits.max_tool_calls:
                                relation, caller = witness
                                extra = session.dispatch("read_source_excerpt", {
                                    "file_path": caller.file_path, "start_line": caller.start_line,
                                    "end_line": caller.end_line, "start_column": caller.start_column,
                                    "end_column": caller.end_column})
                                if type(extra) is not ExcerptToolResult:
                                    raise ResponseError()
                                pending.append(_PendingToolProjection(extra, relation))
                                call_witness_read = True
                            anchor = representative_anchor(representative, relations)
                            excerpt = session.dispatch("read_source_excerpt", {
                                "file_path": anchor.file_path, "start_line": anchor.start_line,
                                "end_line": anchor.end_line, "start_column": anchor.start_column,
                                "end_column": anchor.end_column})
                            if type(excerpt) is not ExcerptToolResult:
                                raise ResponseError()
                            pending.append(_PendingToolProjection(excerpt))
                            caller_spans.append(excerpt.returned_anchor)
                        coverage_gaps.append(
                            "Project understanding used representative excerpts; full-project coverage is not established.")
                        if pending:
                            selected = fit_projections((), tuple(pending), project=True)
                            register_forwarded(tuple(pending), selected)
                            transcript.add_project_results(selected, allocation("final"))

                    await self._run_b(job, partial(_protocol_boundary, retrieve_project, "pre_send"))
                elif job.request.seed_search:
                    if transcript is None:
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                    query = " ".join(job.request.question.split())
                    seed = await self._run_b(job, lambda: session.dispatch("search_symbols", {"query": query, "limit": 20}))
                    if type(seed) is not SymbolSearchResult:
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                    if seed.results:
                        seed_call = ToolCall(
                            "seed-search", "search_symbols", {"query": query, "limit": 20})
                        seed_pending = (_PendingToolProjection(seed),)
                        def forward_seed() -> ProviderDraft:
                            seed_projections = fit_projections((seed_call,), seed_pending)
                            register_forwarded(seed_pending, seed_projections)
                            return transcript.add_tool_results(
                                (seed_call,), seed_projections,
                                job.request.draft.max_output_tokens)

                        _protocol_boundary(forward_seed, "pre_send")
                wire_had_file_dependencies = False
                factory = job.provider_factory or self._provider_factory
                job.provider_factory = None
                try:
                    provider = factory()
                finally:
                    del factory
                job.provider = provider

                async def send(current: ProviderDraft,
                               phase: Literal["ordinary", "final", "repair"]
                               ) -> tuple[tuple[ToolCall, ...], bytes | None,
                                          dict[str, object]]:
                    nonlocal wire_had_file_dependencies
                    if not input_planner.fits(ledger.usage, current):
                        raise ServiceError("AI_BUDGET_EXCEEDED")
                    send_policy.ensure_can_prepare()
                    prepared = await provider.prepare(current)
                    if (type(prepared) is not PreparedRequest or prepared.method != "POST"
                            or prepared.max_output_tokens != current.max_output_tokens
                            or prepared.deadline != job.request.draft.deadline):
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                    try:
                        reservation, files = send_policy.admit(prepared, phase)
                    except Exception:
                        sync_accounting()
                        raise
                    input_planner.observe(current, reservation.input_tokens)
                    admitted_id = prepared.preparation_id
                    wire_had_file_dependencies = (wire_had_file_dependencies or bool(files)
                                                  or bool(prepared.interpretation_occurrences))
                    try:
                        if (root_fragment is not None and root_wire is not None
                                and retrieval is not None):
                            verify_root_wire(prepared, root_fragment, *root_wire)
                            existing = provided.get(root_fragment.evidence_id, retrieval.target)
                            if existing != retrieval.target:
                                raise ResponseError()
                            provided[root_fragment.evidence_id] = retrieval.target
                        if phase != "ordinary":
                            await self._run_b(job, verify_catalog_commit)
                        prepared_origin, _ = _parse_url(prepared.endpoint)
                        if (prepared_origin != job.authority_origin
                                or prepared.endpoint != job.authority_endpoint):
                            raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                        frozen_fields = (
                            prepared.preparation_id, prepared.method, prepared.endpoint,
                            prepared.body, prepared.max_output_tokens, prepared.deadline,
                            prepared.input_token_upper_bound, prepared.source_occurrences,
                            prepared.metadata_occurrences, prepared.attribution_complete,
                            prepared.interpretation_occurrences,
                            *_estimated_accounting_fields(prepared),
                        )
                        await self._assert_fresh(job, ledger)
                        consent = source_consent

                        def checkpoint() -> None:
                            ledger.reserve(BudgetReservation())
                            with self._lock:
                                if (job.cancel_requested
                                        or self._source_consent is not consent
                                        or not self._job_eligible_locked(job)):
                                    raise ServiceError("AI_CANCELLED")

                        if files:
                            with self._lock:
                                authority = self._authority
                            fingerprint = hashlib.sha256(json.dumps(
                                consent.allowed_files, ensure_ascii=True,
                                separators=(",", ":")).encode("ascii")).hexdigest()
                            if authority is None or fingerprint != authority.scope_fingerprint:
                                raise ServiceError("AI_CONSENT_REQUIRED")
                            await self._run_b(job, lambda: _verify_wire_files(
                                job.analysis_snapshot, consent, job.request.scope,
                                prepared_origin, files, checkpoint))
                        with self._lock:
                            if (job.cancel_requested or job.dispatch_closed
                                    or not self._job_eligible_locked(job)
                                    or prepared_origin != job.authority_origin):
                                raise ServiceError("AI_CANCELLED")
                            if self._source_consent is not source_consent:
                                raise ServiceError("AI_CONSENT_REQUIRED")
                            job.prepared = prepared
                        await self._before_dispatch(prepared)
                        await self._assert_fresh(job, ledger)
                        if files:
                            await self._run_b(job, lambda: _verify_wire_files(
                                job.analysis_snapshot, consent, job.request.scope,
                                prepared_origin, files, checkpoint))
                        ledger.reserve(BudgetReservation())
                        if phase != "ordinary":
                            await self._run_b(job, verify_catalog_commit)
                        with self._lock:
                            current_fields = (
                                prepared.preparation_id, prepared.method, prepared.endpoint,
                                prepared.body, prepared.max_output_tokens, prepared.deadline,
                                prepared.input_token_upper_bound, prepared.source_occurrences,
                                prepared.metadata_occurrences, prepared.attribution_complete,
                                prepared.interpretation_occurrences,
                                *_estimated_accounting_fields(prepared),
                            )
                            if frozen_fields != current_fields:
                                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                            if (job.cancel_requested or job.dispatch_closed
                                    or job.prepared is not prepared
                                    or not self._job_eligible_locked(job)
                                    or (files and self._source_consent is not consent)):
                                raise ServiceError("AI_CANCELLED")
                            if self._source_consent is not source_consent:
                                raise ServiceError("AI_CONSENT_REQUIRED")
                            job.prepared = None
                            job.dispatched = True
                        send_policy.dispatch(admitted_id)
                        possibly_dispatched_files.update(files)

                        calls: list[ToolCall] = []
                        answer_parts = 0
                        answer_bytes = 0
                        answer_content: list[str] = []
                        answer_finished = False
                        usage_data: dict[str, object] | None = None
                        response_bytes = 0
                        async for event in provider.dispatch(prepared):
                            ledger.reserve(BudgetReservation())
                            if type(event) is not ProviderEvent or type(event.payload) is not bytes:
                                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                            response_bytes += len(event.payload)
                            if response_bytes > 256 * 1024:
                                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                            if event.kind == "text":
                                if (usage_data is not None or answer_finished
                                        or len(event.payload) > 8192):
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                try:
                                    event.payload.decode("utf-8")
                                except UnicodeError:
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE") from None
                                if job.public_identity is not None:
                                    with self._lock:
                                        if (job.cancel_requested
                                                or not self._job_eligible_locked(job)):
                                            raise ServiceError("AI_CANCELLED")
                                        text = event.payload.decode("utf-8")
                                        if text:
                                            project = job.request.expected_identity.project_key
                                            if (project is None
                                                    or job.conversation_revision is None):
                                                raise ServiceError("AI_CONSENT_REQUIRED")
                                            previous_text = cast(
                                                str, job.ring.poll(job.ring.seq)["text"])
                                            self._conversations._retain_provisional(
                                                project, job.request.conversation_id,
                                                job.conversation_revision, job.job_id,
                                                previous_text + text)
                                            self._drop_evicted_requests_locked()
                                            job.ring.append_text(text)
                                        self._sync_ring_locked(job)
                            elif event.kind == "tool":
                                data = _provider_payload(event)
                                if (phase != "ordinary" or usage_data is not None
                                        or answer_parts
                                        or set(data) != {"id", "name", "arguments"}
                                        or type(data["id"]) is not str or not data["id"]
                                        or len(data["id"]) > 512
                                        or not data["id"].isprintable()
                                        or any(call.call_id == data["id"] for call in calls)
                                        or type(data["name"]) is not str
                                        or data["name"] not in (
                                            "search_symbols", "read_source_excerpt",
                                            "get_local_relations")
                                        or (transcript is not None
                                            and data["name"] not in transcript.allowed_tools)
                                        or type(data["arguments"]) is not dict
                                        or len(calls) >= 8):
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                calls.append(ToolCall(
                                    data["id"], data["name"], data["arguments"]))
                            elif event.kind == "answer":
                                data = _provider_payload(event)
                                if (phase == "ordinary" or usage_data is not None or calls
                                        or answer_finished
                                        or set(data) != {"index", "last", "content"}
                                        or type(data["index"]) is not int
                                        or data["index"] != answer_parts
                                        or type(data["last"]) is not bool
                                        or type(data["content"]) is not str):
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                answer_parts += 1
                                answer_bytes += len(data["content"].encode("utf-8"))
                                if answer_bytes > 64 * 1024:
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                answer_finished = data["last"]
                                answer_content.append(data["content"])
                            elif event.kind == "usage":
                                data = _provider_payload(event)
                                complete = (phase == "ordinary" or
                                            (answer_finished and answer_bytes > 0))
                                if (usage_data is not None or not complete
                                        or set(data) != {
                                            "inputTokens", "outputTokens", "basis"}
                                        or data["basis"] not in (
                                            "actual", "estimated", "unknown")):
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                for count in (data["inputTokens"], data["outputTokens"]):
                                    if (count is not None
                                            and (type(count) is not int or count < 0)):
                                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                counts_known = all(
                                    data[key] is not None
                                    for key in ("inputTokens", "outputTokens"))
                                if counts_known != (data["basis"] != "unknown"):
                                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                                send_policy.observe(admitted_id, ObservedUsage(
                                    cast(int | None, data["inputTokens"]),
                                    cast(int | None, data["outputTokens"]),
                                    data["basis"],
                                ))
                                usage_data = data
                                sync_accounting()
                            else:
                                raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                        if (usage_data is None
                                or (phase != "ordinary" and not answer_finished)):
                            raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                        raw = (None if phase == "ordinary"
                               else "".join(answer_content).encode("utf-8"))
                        if job.project_coverage is not None:
                            with self._lock:
                                job.project_coverage = job.project_coverage.dispatched(prepared)
                        return tuple(calls), raw, usage_data
                    except Exception as error:
                        if job.validation_capture is not None:
                            try:
                                job.validation_capture.transport(phase, _sdk_delta_rejection(error))
                            except Exception:
                                pass
                        raise
                    finally:
                        try:
                            send_policy.finalize(admitted_id)
                        finally:
                            try:
                                sync_accounting()
                            finally:
                                with self._lock:
                                    if job.prepared is prepared:
                                        job.prepared = None

                def execute_tools(
                    calls: tuple[ToolCall, ...],
                ) -> tuple[_PendingToolProjection, ...]:
                    pending: list[_PendingToolProjection] = []
                    for call in calls:
                        result = _protocol_boundary(
                            partial(session.dispatch, call.name, call.arguments),
                            "result_validation")
                        if type(result) not in (
                                SymbolSearchResult, LocalRelationsResult, ExcerptToolResult):
                            raise ResponseError()
                        pending.append(_PendingToolProjection(result))
                    return tuple(pending)

                if transcript is not None and transcript.mode in ("text", "tools"):
                    ordinary_rounds = 4 if budget is None else budget.ordinary_rounds
                    for ordinary_index in range(ordinary_rounds):
                        if ((job.request.purpose == "project_understanding" or job.request.understanding is not None)
                                and ledger.usage.tool_calls >= limits.max_tool_calls):
                            coverage_gaps.append(
                                "Further project retrieval was skipped to preserve the final-answer budget.")
                            break
                        ordinary_limit = output_allocation(
                            ledger.usage,
                            "ordinary",
                            job.request.draft.max_output_tokens,
                            config=budget,
                        )
                        if ordinary_limit is None:
                            if "Further tool retrieval was skipped to preserve the final-answer budget." not in coverage_gaps:
                                coverage_gaps.append(
                                    "Further tool retrieval was skipped to preserve the final-answer budget.")
                            break
                        current = _protocol_boundary(
                            partial(transcript.current, ordinary_limit), "pre_send")
                        prospective_catalog = await self._run_b(job, guidance_catalog)
                        if budget is None:
                            final_preview = _protocol_boundary(
                                partial(transcript.preview_final, allocation("final"), prospective_catalog),
                                "pre_send")
                            completion_inputs: tuple[ProviderDraft, ...] = (final_preview,)
                        else:
                            completion_inputs = _protocol_boundary(
                                partial(transcript.preview_completion,
                                        allocation("final"), allocation("repair"), prospective_catalog),
                                "pre_send")
                            final_preview = completion_inputs[0]
                        if (not input_planner.fits(ledger.usage, current, *completion_inputs)
                                or not source_planner.fits(
                                    ledger.usage, current, final_preview, final_preview)):
                            if "Further tool retrieval was skipped to preserve the final-answer budget." not in coverage_gaps:
                                coverage_gaps.append(
                                    "Further tool retrieval was skipped to preserve the final-answer budget.")
                            break
                        calls, _ignored, _usage = await send(current, "ordinary")
                        if ((job.request.purpose == "project_understanding" or job.request.understanding is not None)
                                and ledger.usage.tool_calls + len(calls) > limits.max_tool_calls):
                            coverage_gaps.append(
                                "Further project retrieval was skipped to preserve the final-answer budget.")
                            with self._lock:
                                if job.project_coverage is not None:
                                    job.project_coverage = job.project_coverage.with_gap("retrieval_gap")
                            break
                        if calls:
                            pending = await self._run_b(
                                job, partial(execute_tools, calls))
                            def forward_results(
                                bound_calls: tuple[ToolCall, ...] = calls,
                                bound_pending: tuple[_PendingToolProjection, ...] = pending,
                            ) -> ProviderDraft:
                                projections = fit_projections(bound_calls, bound_pending)
                                register_forwarded(bound_pending, projections)
                                return transcript.add_tool_results(
                                    bound_calls, projections, allocation("final"))

                            current = _protocol_boundary(forward_results, "pre_send")
                        if not calls:
                            break
                        if ordinary_index == ordinary_rounds - 1:
                            gap = "Further tool retrieval was skipped to preserve the final-answer budget."
                            if gap not in coverage_gaps:
                                coverage_gaps.append(gap)
                            break
                if transcript is not None:
                    frozen_catalog = await self._run_b(job, guidance_catalog)
                    if budget is None:
                        final_inputs: tuple[ProviderDraft, ...] = (_protocol_boundary(
                            lambda: transcript.preview_final(allocation("final"), frozen_catalog),
                            "pre_send"),)
                    else:
                        final_inputs = _protocol_boundary(
                            lambda: transcript.preview_completion(
                                allocation("final"), allocation("repair"), frozen_catalog),
                            "pre_send")
                    if (not input_planner.fits(ledger.usage, *final_inputs)
                            or not source_planner.fits(ledger.usage, *final_inputs)):
                        raise ServiceError("AI_BUDGET_EXCEEDED")
                    final_draft = _protocol_boundary(
                        lambda: transcript.final(allocation("final"), frozen_catalog), "pre_send")
                elif (transcript is None and budget is not None
                      and draft.max_output_tokens > 2_000):
                    # Never present the initial 4K tools draft as a final send.
                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                else:
                    final_draft = draft

                _calls, raw_answer, usage_data = await send(final_draft, "final")
                if raw_answer is None:
                    raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")

                def validated_answer(
                    raw: bytes, phase: str,
                ) -> tuple[Answer | None, tuple[str, ...]]:
                    capture = job.validation_capture
                    attempt = None if capture is None else capture.begin(phase)
                    with observation(attempt):
                        try:
                            model = parse_model_answer(raw)
                        except ResponseError:
                            if attempt is not None:
                                attempt.finish("parse", (("invalid_schema", None, None),))
                            return None, ("invalid_schema",)
                        if attempt is not None:
                            attempt.stage = "evidence"
                        resolver = ActualEvidenceResolver(session, registry, snapshot,
                                                          job.request.scope, provided)
                        checked_at = datetime.now(timezone.utc).isoformat()
                        result = validate_model_answer(model, resolver=resolver,
                                                       checked_at=checked_at,
                                                       incomplete=bool(coverage_gaps),
                                                       coverage_gaps=tuple(coverage_gaps))
                        if attempt is not None:
                            attempt.finish("evidence", tuple(
                                (issue.code, issue.section_index, issue.claim_index)
                                for issue in result.issues))
                        if result.answer is None:
                            return None, tuple(dict.fromkeys(issue.code for issue in result.issues))
                        return result.answer, ()

                answer, repair_codes = await self._run_b(
                    job, lambda: validated_answer(raw_answer, "final"))
                if answer is None:
                    if transcript is None:
                        raise ResponseError()
                    gap = "Repair condensed the answer; coverage is incomplete."
                    if gap not in coverage_gaps:
                        coverage_gaps.append(gap)
                    repair_draft = _protocol_boundary(
                        lambda: transcript.repair(repair_codes, allocation("repair"),
                                                  () if frozen_catalog is None else frozen_catalog),
                        "pre_send")
                    try:
                        _calls, repaired_raw, usage_data = await send(repair_draft, "repair")
                    except AgentError:
                        raise
                    except ServiceError as repair_error:
                        if (repair_error.code != "AI_PROTOCOL_INCOMPATIBLE"
                                or repair_error.diagnostic is not None):
                            raise
                        raise ResponseError() from None
                    except ValueError as repair_error:
                        if _sdk_worker_error(repair_error) is not None:
                            raise
                        raise ResponseError() from None
                    if repaired_raw is None:
                        raise ServiceError("AI_PROTOCOL_INCOMPATIBLE")
                    answer, _repair_codes = await self._run_b(
                        job, lambda: validated_answer(repaired_raw, "repair"))
                    if answer is None:
                        raise ResponseError()
                original_answer = answer
                answer_json = json.dumps(answer_to_public(original_answer), ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False)
                ledger.reserve(BudgetReservation())
                close_started = True
                await provider.aclose()
                provider_closed = True
                job.provider_released = True
                await self._assert_fresh(job, ledger)
                retained_context = None
                if provided:
                    with self._lock:
                        if self._source_consent is not source_consent:
                            raise ServiceError("AI_CONSENT_REQUIRED")
                        retained_identity = self._retained_identity_locked(job.request.conversation_id)
                    retained_context = await self._run_b(job, lambda: RetainedEvidenceContext.create(
                        session, registry, snapshot, job.request.scope, provided, retained_identity))
                    await self._assert_fresh(job, ledger)
                else:
                    session.close()
                    job.tool_session = None
                with self._lock:
                    job.dispatch_closed = True
                    eligible = self._job_eligible_locked(job)
                    if not job.cancel_requested and eligible:
                        if self._source_consent is not source_consent:
                            raise ServiceError("AI_CONSENT_REQUIRED")
                        authority = self._authority
                        if authority is None or job.conversation_revision is None:
                            raise ServiceError("AI_CONSENT_REQUIRED")
                        # Input wire offsets cannot authorize arbitrary generated reply text.
                        source_tags: list[dict[str, object]] = []
                        dependencies = []
                        for path in sorted(possibly_dispatched_files):
                            file = snapshot.get_file(path)
                            if file is None or file.content_hash is None:
                                raise ServiceError("AI_EVIDENCE_INVALID")
                            dependencies.append((path, file.content_hash))
                        answer_origin = AnswerOriginManifest(snapshot.binding,
                            (authority.project_key, job.request.conversation_id, job.job_id),
                            job.conversation_revision, job.request.config_revision, job.request.consent_revision,
                            job.authority_origin, job.request.normalized_scope,
                            hashlib.sha256(answer_json.encode("utf-8")).hexdigest(), tuple(dependencies))
                        project_reference = None
                        if job.request.purpose == "project_understanding":
                            if job.project_coverage is None:
                                raise ServiceError("AI_RESULT_UNAVAILABLE")
                            project_reference = create_reference(job.request.scope,
                                job.request.conversation_id, job.conversation_revision,
                                job.job_id, job.request.config_revision, job.request.consent_revision,
                                job.project_coverage)
                        self._conversations.reserve_append(
                            authority.project_key, job.request.conversation_id,
                            job.conversation_revision,
                            messages=({"role": "assistant", "content": answer_json,
                                       "jobId": job.job_id,
                                       "scope": {"projectEpoch": job.request.scope.project_epoch,
                                                 "origin": job.authority_origin,
                                                 "generation": job.request.scope.analysis_generation,
                                                 "consentRevision": job.request.consent_revision,
                                                 "configRevision": job.request.config_revision,
                                                 "normalizedScope": job.request.normalized_scope},
                                       "sourceTags": source_tags,
                                       "sourceFree": not wire_had_file_dependencies},),
                            results={job.job_id: {"answer": answer_json, "usage": usage_data}},
                            _replace_provisional_job_id=job.job_id,
                            _delivery_job_id=job.job_id,
                            _project_understanding=project_reference,
                            _answer_origin=answer_origin,
                            retained=None if retained_context is None else {job.job_id: retained_context})
                        job.tool_session = None  # ConversationStore now owns the original session.
                        self._drop_evicted_requests_locked()
                        job.validated_answer = original_answer
                        job.status = JobStatus.COMPLETED
                        self._sync_ring_locked(job)
                        if project_reference is not None:
                            self._invalidate_ineligible_jobs_locked()
                            self._invalidate_navigation_locked()
                        if not job.b_submitting and job.b_future is None:
                            job.resources_released = True
                            self._sync_ring_locked(job)
                            self._retire_job_locked(job)
                    else:
                        if not job.cancel_requested:
                            job.status = JobStatus.STALE
                            self._sync_ring_locked(job)
                        if not job.b_submitting and job.b_future is None:
                            job.resources_released = True
                            self._sync_ring_locked(job)
                            if not job.cancel_requested:
                                self._retire_job_locked(job)
            self._drain_pending_closures()
        except asyncio.CancelledError:
            return
        except Exception as error:
            with self._lock:
                if job.cancel_requested:
                    return  # The loop's cancellation command owns teardown.
                sdk_error = _sdk_worker_error(error)
                service_diagnostic = (
                    error.diagnostic
                    if type(error) is ServiceError
                    and error.code == "AI_PROTOCOL_INCOMPATIBLE"
                    and type(error.diagnostic) is ProtocolDiagnostic
                    else None
                )
                error_code = (
                    cast(ServiceError | ResponseError, error).code
                    if type(error) in (ServiceError, ResponseError)
                    else sdk_error[0] if sdk_error is not None
                    else "AI_BUDGET_EXCEEDED"
                    if type(error) is AgentError
                    and error.code in ("budget_exceeded", "timeout")
                    else "AI_PROTOCOL_INCOMPATIBLE"
                )
                job.issue = issue_for_code(
                    error_code,
                    sdk_error[1] if sdk_error is not None else service_diagnostic,
                )
                job.dispatch_closed = True
                job.prepared = None
                if self._failure_status_locked(job, error) is JobStatus.FAILED:
                    self._retain_failed_evidence_locked(
                        job, source_consent, failed_evidence)
                self._sync_ring_locked(job)
            if close_started and not provider_closed:
                with self._lock:
                    job.status = self._failure_status_locked(job, error)
                    self._sync_ring_locked(job)
                return  # A failed close needs an explicit later teardown attempt.
            try:
                if not provider_closed and job.provider is not None:
                    await job.provider.aclose()
                    job.provider_released = True
            except Exception:
                with self._lock:
                    job.status = self._failure_status_locked(job, error)
                    self._sync_ring_locked(job)
                return  # Keep the slot until a later cleanup actually releases it.
            with self._lock:
                if not job.cancel_requested:
                    job.status = self._failure_status_locked(job, error)
                    self._sync_ring_locked(job)
            # Either this call observes the completed B lease, or its callback
            # observes the published terminal status. Both use the same owner.
            self._release_terminal_job(job)

    async def _close_job(self, job: _Job) -> None:
        with self._lock:
            if job.cancel_accepted or job.closing:
                return
            if job.resources_released:
                if job.cancel_requested:
                    job.cancel_accepted = True
                    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                        job.status = JobStatus.CANCELLED
                    self._sync_ring_locked(job)
                    self._retire_job_locked(job)
                return
            job.closing = True
            job.dispatch_closed = True
            job.prepared = None
            job.provider_factory = None
            self._sync_ring_locked(job)
            task = job.task
        try:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            while True:
                with self._lock:
                    submitting = job.b_submitting
                    raw = job.b_future
                if raw is not None:
                    raw.cancel()
                    try:
                        await asyncio.shield(asyncio.wrap_future(raw))
                    except (asyncio.CancelledError, Exception):
                        pass  # The raw future is done; its result is not a release condition.
                elif not submitting:
                    break
                else:
                    await asyncio.sleep(0.01)
            with self._lock:
                released = job.resources_released
                provider = job.provider
            if provider is not None and not released:
                await provider.cancel()
                await provider.aclose()
                job.provider_released = True
            if job.tool_session is not None:
                job.tool_session.close()
                job.tool_session = None
        except Exception:
            return  # Cleanup failed; never claim release or confirmation.
        finally:
            with self._lock:
                job.closing = False
        with self._lock:
            if job.b_submitting or job.b_future is not None:
                return
            job.resources_released = True
            job.cancel_accepted = job.cancel_requested
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                job.status = JobStatus.CANCELLED
            self._sync_ring_locked(job)
            self._retire_job_locked(job)

    def _prepare_close(self) -> None:
        """Release AI work after editor approval without making a failed destroy fatal."""
        with self._lock:
            # Runtime owns credentials/grant revocation. Preparing this worker
            # alone must remain reusable if native destroy fails; do not erase
            # an independently installed authority from this private drain.
            self._clear_runtime_memory_locked(all_history=True)
            self._conversations._revoke_retained_except({})
            pending = tuple(job for job in self._jobs.values()
                            if not job.resources_released)
            connections = tuple(self._connection_jobs.values())
            navigation = tuple(self._navigation_jobs.values())
            for check in navigation:
                self._request_navigation_close_locked(check)
            for connection in connections:
                self._request_connection_close_locked(connection)
        for job in pending:
            self.cancel(job.job_id)

        async def drain() -> None:
            for check in navigation:
                await self._close_navigation(check)
            for connection in connections:
                await self._close_connection(connection)
            for job in pending:
                await self._close_job(job)
            while True:
                with self._lock:
                    remaining = [job for job in pending if not job.resources_released]
                    closing = any(job.closing for job in remaining)
                    connection_remaining = [job for job in connections
                                            if not job.resources_released]
                    closing = closing or any(job.closing for job in connection_remaining)
                    navigation_remaining = [job for job in navigation if not job.resources_released]
                    closing = closing or any(job.closing for job in navigation_remaining)
                if not (remaining or connection_remaining or navigation_remaining) or not closing:
                    return
                await asyncio.sleep(0.01)

        try:
            asyncio.run_coroutine_threadsafe(drain(), self._loop).result(timeout=3)
        except Exception:
            raise ServiceError("AI_CLEANUP_INCOMPLETE") from None
        with self._lock:
            if (self._b_outstanding or any(not job.resources_released
                                           for job in self._jobs.values())
                    or self._connection_jobs or self._navigation_jobs):
                raise ServiceError("AI_CLEANUP_INCOMPLETE")
        self._drain_pending_closures()
        # A revoked lease still owns its session until its last release.
        if self._conversations._has_retained_resources():
            raise ServiceError("AI_CLEANUP_INCOMPLETE")

    def shutdown(self) -> None:
        self._prepare_close()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise ServiceError("AI_CLEANUP_INCOMPLETE")
        self._b_executor.shutdown(wait=True)


class _AssistantRuntime:
    """Memory-only application owner. Construction never starts optional work.

    This private composition is not the production disclosure gate. The desktop
    binds it only once the remaining job adapters have passed their acceptance.
    """

    def __init__(self, *, provider_factory: Callable[[ProviderConfig, MemoryKey],
                                                    ProviderAdapter] | None = None,
                 before_dispatch: Callable[[PreparedRequest], Awaitable[None]] = _ready,
                 trusted_input_profiles: tuple[TrustedEstimatedProfile, ...] = (),
                 trusted_connection_profiles: tuple[TrustedConnectionProfile, ...] = (),
                 explanation_budget: ExplanationBudgetConfig = EXPLANATION_BUDGET,
                 _validation_diagnostics: DiagnosticsSink | None = None) -> None:
        if provider_factory is not None and not callable(provider_factory):
            raise ServiceError("AI_INVALID_INPUT")
        if type(explanation_budget) is not ExplanationBudgetConfig:
            raise ServiceError("AI_INVALID_INPUT")
        try:
            frozen_budget = replace(explanation_budget)
        except (AgentError, TypeError, ValueError):
            raise ServiceError("AI_INVALID_INPUT") from None
        self._trusted_input_profiles = _validated_trusted_profiles(trusted_input_profiles)
        self._trusted_connection_profiles = _validated_trusted_connection_profiles(trusted_connection_profiles)
        self._explanation_budget = frozen_budget
        self._validation_diagnostics = _validation_diagnostics
        self._connection_provider_factory = provider_factory
        self._connection_before_dispatch = before_dispatch
        self._lock = Lock()
        self._shutdown_lock = Lock()
        self._engine_creation_lock = Lock()
        self._conversations = ConversationStore()
        self._authority: AuthorityState | None = None
        self._source_consent: SourceConsent | None = None
        self._memory_revision = 0
        self._config: ProviderConfig | None = None
        self._key: MemoryKey | None = None
        self._enabled = False
        self._config_revision = 0
        self._consents = ConsentStore()
        self._grant_partitions: set[tuple[str, str]] = set()
        self._identity: PublicationIdentity | None = None
        self._snapshot: AnalysisSnapshot | None = None
        self._engine: AssistantService | None = None
        self._start_index: StartContextIndex | None = None
        self._closing = False
        self._closed = False
        self._finalizing = False

    def _settings_locked(self) -> dict[str, object]:
        return {"available": not self._closed and not self._finalizing, "enabled": self._enabled,
                "baseUrl": self._config.base_url if self._config is not None else "",
                "model": self._config.model if self._config is not None else "",
                "keySet": self._key is not None, "configRevision": self._config_revision,
                "issue": None}

    def get_settings(self) -> dict[str, object]:
        with self._lock:
            return self._settings_locked()

    @staticmethod
    def _context_identity(ctx: AssistantContext) -> PublicationIdentity:
        if type(ctx) is not AssistantContext:
            raise ServiceError("AI_INVALID_INPUT")
        return PublicationIdentity(ctx.publication_revision, ctx.project_epoch,
            ctx.analysis_generation, ctx.project_key, ctx.root, ctx.ignore_paths, ctx.status, ctx.eligible)

    def _require_context_locked(self, epoch: str, identity: PublicationIdentity,
                                snapshot: AnalysisSnapshot | None) -> str:
        if self._closed or self._finalizing:
            raise ServiceError("AI_DISABLED")
        if (identity != self._identity or epoch != identity.project_epoch
                or identity.project_key is None or identity.root is None
                or snapshot is not self._snapshot):
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        return identity.project_key

    def _refresh_authority(self) -> None:
        """Prepare immutable authority outside locks, then compare-and-install."""
        while True:
            with self._lock:
                version, identity, config = self._memory_revision, self._identity, self._config
                source = self._source_consent
                grant = (None if identity is None or identity.project_key is None or config is None
                         else self._consents.get(project_key=identity.project_key, origin=config.origin))
                revisions = (() if identity is None or identity.project_key is None else
                             self._conversations._project_revisions(identity.project_key))
                usable = (self._enabled and self._key is not None and not self._closing
                          and identity is not None and identity.eligible and self._snapshot is not None
                          and source is not None and source.binding == self._snapshot.binding
                          and grant is not None and config is not None
                          and self._consents.is_current(grant, config=config))
            authority = (AuthorityState.from_trusted(config, grant, revisions)
                         if usable and config is not None and grant is not None else None)
            with self._lock:
                if version != self._memory_revision:
                    continue
                self._authority = authority
                engine = self._engine
                if engine is not None:
                    engine._replace_runtime_authority(authority, source if authority is not None else None)
                return

    def new_conversation(self, request: ProjectRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not ProjectRequest:
            raise ServiceError("AI_INVALID_INPUT")
        parse_project({"projectEpoch": request.project_epoch})
        identity = self._context_identity(ctx)
        conversation_id = uuid.uuid4().hex
        with self._lock:
            project = self._require_context_locked(request.project_epoch, identity, ctx.snapshot)
            engine = self._engine
            with engine._lock if engine is not None else nullcontext():
                evicted = self._conversations.register(project, conversation_id, 0)
                if engine is not None:
                    engine._drop_evicted_requests_locked()
            self._memory_revision += 1
        self._refresh_authority()
        self._drain_pending_closures()
        return {"conversationId": conversation_id, "evictedConversationIds": list(evicted)}

    def clear_conversation(self, request: ClearRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not ClearRequest:
            raise ServiceError("AI_INVALID_INPUT")
        parse_clear({"projectEpoch": request.project_epoch, "conversationId": request.conversation_id})
        identity = self._context_identity(ctx)
        with self._lock:
            project = self._require_context_locked(request.project_epoch, identity, ctx.snapshot)
            engine = self._engine
            with engine._lock if engine is not None else nullcontext():
                revision = self._conversations.revision_of(project, request.conversation_id)
                if revision is None:
                    raise ServiceError("AI_RESULT_UNAVAILABLE")
                if revision >= 2**53 - 1:
                    raise ServiceError("AI_REQUEST_LIMIT")
                keys = self._conversations.request_keys(project, request.conversation_id)
                self._conversations.clear(project, request.conversation_id, revision + 1)
                if engine is not None:
                    for job in engine._jobs.values():
                        if (job.request.expected_identity.project_key == project
                                and job.request.conversation_id == request.conversation_id):
                            job.cancel_requested = True
                            engine._sync_ring_locked(job)
                            engine._pending_closures.add(job.job_id)
                    for key in keys:
                        entry = engine._requests.get(key)
                        retired = None if entry is None else engine._completed.get(entry[1])
                        if retired is not None and retired.snapshot.status is JobStatus.COMPLETED:
                            engine._completed[retired.snapshot.job_id] = replace(retired,
                                snapshot=replace(retired.snapshot, status=JobStatus.STALE),
                                seq=retired.seq + 1, events=())
            self._memory_revision += 1
        self._refresh_authority()
        self._drain_pending_closures()
        return {"cleared": True, "conversationRevision": revision + 1}

    def get_consent_candidates(self, request: ConsentCandidatesRequest, ctx: AssistantContext) -> dict[str, object]:
        """Project-scope selection draft from the immutable publication, no disk I/O."""
        if type(request) is not ConsentCandidatesRequest:
            raise ServiceError("AI_INVALID_INPUT")
        parse_consent_candidates({"projectEpoch": request.project_epoch,
                                  "analysisGeneration": request.analysis_generation})
        identity = self._context_identity(ctx)
        snapshot = ctx.snapshot
        if (not ctx.eligible or snapshot is None or ctx.status.value not in ("Ready", "Partial")
                or request.analysis_generation != ctx.analysis_generation
                or snapshot.binding.project_epoch != ctx.project_epoch
                or snapshot.binding.analysis_generation != ctx.analysis_generation
                or snapshot.binding.project_root != str(ctx.root)
                or snapshot.binding.ignore_paths != ctx.ignore_paths):
            raise ServiceError("AI_ANALYSIS_REQUIRED")
        candidates = sorted({record.relative_path for record in snapshot.file_inventory
            if record.was_parsed and record.content_hash is not None
            and record.relative_path.endswith(".py") and not is_protected_source_path(record.relative_path)
            and not any(record.relative_path == ignored or record.relative_path.startswith(ignored + "/")
                        for ignored in ctx.ignore_paths)})
        counts: dict[str, int] = {}
        for path in candidates:
            counts[path.casefold()] = counts.get(path.casefold(), 0) + 1
        files = [path for path in candidates if counts[path.casefold()] == 1]
        if len(json.dumps(files).encode("utf-8")) > 1024 * 1024:
            raise ServiceError("AI_CAPACITY")
        with self._lock:
            self._require_context_locked(request.project_epoch, identity, snapshot)
        return {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation, "files": files}

    def grant_consent(self, request: ConsentRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not ConsentRequest or type(request.allowed_files) is not tuple:
            raise ServiceError("AI_INVALID_INPUT")
        parsed = parse_consent({"projectEpoch": request.project_epoch,
            "analysisGeneration": request.analysis_generation, "configRevision": request.config_revision,
            "allowedFiles": list(request.allowed_files), "confirmed": True})
        if parsed != request:
            raise ServiceError("AI_INVALID_INPUT")
        identity = self._context_identity(ctx)
        snapshot = ctx.snapshot
        if (not ctx.eligible or snapshot is None or ctx.status.value not in ("Ready", "Partial")
                or snapshot.binding.project_root != str(ctx.root)
                or snapshot.binding.project_epoch != ctx.project_epoch
                or snapshot.binding.analysis_generation != ctx.analysis_generation
                or snapshot.binding.ignore_paths != ctx.ignore_paths
                or request.analysis_generation != ctx.analysis_generation):
            raise ServiceError("AI_ANALYSIS_REQUIRED")
        for path in request.allowed_files:
            record = snapshot.get_file(path)
            if (record is None or not record.was_parsed or record.content_hash is None
                    or len(record.content_hash) != 64
                    or any(character not in "0123456789abcdef" for character in record.content_hash)
                    or is_protected_source_path(path)
                    or any(path == ignored or path.startswith(ignored + "/") for ignored in ctx.ignore_paths)):
                raise ServiceError("AI_SOURCE_BLOCKED")
        with self._lock:
            project = self._require_context_locked(request.project_epoch, identity, snapshot)
            config = self._config
            if not self._enabled or self._closing:
                raise ServiceError("AI_DISABLED")
            if config is None or self._key is None or config.revision != request.config_revision:
                raise ServiceError("AI_NOT_CONFIGURED")
        source = SourceConsent(snapshot.binding, uuid.uuid4().hex, config.origin, request.allowed_files)
        with self._lock:
            self._require_context_locked(request.project_epoch, identity, snapshot)
            if self._config is not config or self._closing or not self._enabled or self._key is None:
                raise ServiceError("AI_NOT_CONFIGURED")
            if self._engine is not None:
                self._engine._invalidate_runtime_memory()
            grant = self._consents.grant(project_key=project, config=config,
                                         allowed_files=request.allowed_files, confirmed=True)
            self._grant_partitions.add((project, config.origin))
            self._source_consent = source
            self._memory_revision += 1
        self._refresh_authority()
        self._drain_pending_closures()
        return {"consentRevision": grant.revision, "origin": grant.origin,
                "allowedFiles": list(grant.allowed_files), "enabled": grant.enabled}

    def get_state(self, request: ProjectRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not ProjectRequest:
            raise ServiceError("AI_INVALID_INPUT")
        parse_project({"projectEpoch": request.project_epoch})
        identity = self._context_identity(ctx)
        with self._lock:
            project = self._require_context_locked(request.project_epoch, identity, ctx.snapshot)
            config = self._config
            grant = None if config is None else self._consents.get(project_key=project, origin=config.origin)
            if grant is not None and config is not None and not self._consents.is_current(grant, config=config):
                grant = None
            config_revision = self._config_revision
            engine = self._engine
            live: dict[str, tuple[PollState, str]] = {}
            project_jobs: set[str] = set()
            cursors: dict[str, tuple[int, JobStatus]] = {}
            unavailable: set[str] = set()
            with engine._lock if engine is not None else nullcontext():
                records = self._conversations._project_capture(project)
                if engine is not None:
                    unavailable = engine._recovery_unavailable.copy()
                    for job in engine._jobs.values():
                        if job.request.expected_identity.project_key != project:
                            continue
                        state = PollState(PollIdentity("explanation", job.job_id,
                            job.request.scope.project_epoch, job.request.scope.analysis_generation,
                            job.request.conversation_id, job.conversation_revision),
                            job.snapshot(), job.request.config_revision, job.request.consent_revision,
                            job.ring.seq)
                        text = cast(str, job.ring.poll(job.ring.seq)["text"])
                        live[job.job_id] = (state, text)
                        if job.request.purpose == "project_understanding":
                            project_jobs.add(job.job_id)
                    for check in engine._navigation_jobs.values():
                        if check.publication.project_key == project:
                            live[check.job_id] = (engine._navigation_state_locked(check), "")
                    completed_maps = (engine._completed,
                                      *(completed for _requests, completed in engine._retired_maps))
                    for completed in completed_maps:
                        for job_id, retired in completed.items():
                            if retired.publication.project_key == project:
                                cursors[job_id] = (retired.seq, retired.snapshot.status)
        public_grant: dict[str, object] | None = (None if grant is None else {
            "consentRevision": grant.revision, "origin": grant.origin,
            "allowedFiles": list(grant.allowed_files), "enabled": grant.enabled})
        try:
            _id(ctx.project_epoch)
            if ctx.analysis_generation is not None:
                _natural(ctx.analysis_generation, 1)
            _natural(config_revision)
            if len(records) > 10:
                raise ValueError()
            conversations = [self._state_conversation(record, live, cursors, unavailable,
                identity, config_revision, public_grant) for record in records]
            active = [{**cancel_receipt_to_public(state),
                       **({"purpose": "project_understanding"}
                          if state.identity.kind == "explanation" and state.identity.job_id in project_jobs else {})}
                      for state, _text in live.values()
                      if state.identity.project_epoch == ctx.project_epoch
                      and (state.lifecycle.status in (JobStatus.QUEUED, JobStatus.RUNNING)
                           or not state.lifecycle.resources_released)]
            if len(active) > 2:
                raise ValueError()
            result: dict[str, object] = {
                "projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
                "configRevision": config_revision, "conversations": conversations,
                "grant": public_grant, "activeJobs": active}
            references = [record["projectUnderstanding"] for record in records
                          if "projectUnderstanding" in record]
            if len(references) > 1:
                raise ValueError()
            if references:
                reference = effective_reference(references[0], conversations)
                if reference is not None:
                    result["projectUnderstanding"] = reference
            envelope = {"ok": True, "schemaVersion": 1, "data": result}
            if len(ConversationStore._encoded(envelope)) > 2 * 1024 * 1024:
                raise ValueError()
            return result
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ServiceError("AI_CAPACITY") from None

    @staticmethod
    def _state_conversation(record: dict[str, object], live: dict[str, tuple[PollState, str]],
                            cursors: dict[str, tuple[int, JobStatus]], unavailable: set[str],
                            identity: PublicationIdentity, config_revision: int,
                            grant: dict[str, object] | None) -> dict[str, object]:
        name = _id(record["id"])
        revision = _natural(record["revision"])
        source = cast(tuple[dict[str, object], ...], record["messages"])
        if len(source) > 40:
            raise ValueError()
        recovery = cast(dict[str, dict[str, object]], record["recovery"])
        results = cast(dict[str, dict[str, object]], record["results"])
        output: list[dict[str, object]] = []
        questions: dict[str, dict[str, object]] = {}
        assistants: set[str] = set()
        grant_scope = (None if grant is None else hashlib.sha256(json.dumps(
            grant["allowedFiles"], ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest())
        for message in source:
            if type(message) is not dict or message.get("role") not in ("user", "assistant"):
                raise ValueError()
            job_id = _id(message.get("jobId"))
            if job_id in unavailable:
                raise ValueError()
            captured = live.get(job_id)
            binding = dict(recovery.get(job_id, {}))
            if captured is not None:
                state = captured[0]
                binding = {"projectEpoch": state.identity.project_epoch,
                    "analysisGeneration": state.identity.analysis_generation,
                    "conversationRevision": state.identity.conversation_revision,
                    "configRevision": state.config_revision, "consentRevision": state.consent_revision,
                    "seq": state.seq, "status": state.lifecycle.status.value}
                if state.identity.conversation_id != name:
                    raise ValueError()
            elif job_id in cursors:
                binding.update(seq=cursors[job_id][0], status=cursors[job_id][1].value)
            _id(binding.get("projectEpoch"))
            _natural(binding.get("analysisGeneration"), 1)
            for key in ("conversationRevision", "configRevision", "consentRevision", "seq"):
                _natural(binding.get(key))
            status_value = binding.get("status")
            if type(status_value) is not str:
                raise ValueError()
            status = JobStatus(status_value)
            if binding["conversationRevision"] != revision:
                raise ValueError()
            scope = message.get("scope")
            if type(scope) is not dict or any(scope.get(left) != binding[right] for left, right in (
                    ("projectEpoch", "projectEpoch"), ("generation", "analysisGeneration"),
                    ("configRevision", "configRevision"), ("consentRevision", "consentRevision"))):
                raise ValueError()
            stale = (not identity.eligible or identity.project_epoch != binding["projectEpoch"]
                     or identity.analysis_generation != binding["analysisGeneration"]
                     or config_revision != binding["configRevision"] or grant is None
                     or grant["consentRevision"] != binding["consentRevision"]
                     or grant["origin"] != scope.get("origin") or not grant["enabled"]
                     or grant_scope != scope.get("normalizedScope") or status is JobStatus.STALE)
            common = {key: binding[key] for key in ("projectEpoch", "analysisGeneration",
                "conversationRevision", "configRevision", "consentRevision", "seq")}
            common.update(jobId=job_id, stale=stale)
            if message["role"] == "user":
                text = message.get("content")
                if job_id in questions or type(text) is not str or not 1 <= len(text) <= 2000:
                    raise ValueError()
                text.encode("utf-8")
                item = {**common, "type": "question", "text": text}
                questions[job_id] = item
                output.append(item)
            else:
                if job_id not in questions or job_id in assistants:
                    raise ValueError()
                if message.get("provisional") is True:
                    text = message.get("content")
                    if type(text) is not str or len(text.encode("utf-8")) > 64 * 1024:
                        raise ValueError()
                    output.append({**common, "type": "provisional", "text": text})
                    assistants.add(job_id)
                    continue
                if status not in (JobStatus.COMPLETED, JobStatus.STALE):
                    raise ValueError()
                answer_raw = results.get(job_id, {}).get("answer")
                if (type(answer_raw) is not str or answer_raw != message.get("content")
                        or len(answer_raw.encode("utf-8")) > 64 * 1024):
                    raise ValueError()
                # Only locally validated retained results can supply this field;
                # neither provider events nor arbitrary assistant text is decoded.
                answer = json.loads(answer_raw)
                if type(answer) is not dict or set(answer) != {
                        "sections", "evidence", "relationFacts", "checkedAt", "incomplete", "coverageGaps"}:
                    raise ValueError()
                output.append({**common, "type": "validated_answer", "answer": answer})
                assistants.add(job_id)
        if len(output) > 40:
            raise ValueError()
        return {"conversationId": name, "conversationRevision": revision, "messages": output}

    def _invalidate_locked(self) -> None:
        self._authority, self._source_consent = None, None
        self._memory_revision += 1
        if self._engine is not None:
            self._engine._replace_runtime_authority(None, None)
            # Older trusted lifecycle fakes implement only project authority.
            replace_config = getattr(self._engine, "_replace_connection_config", None)
            if replace_config is not None:
                replace_config(None)
        for project, origin in self._grant_partitions:
            self._consents.revoke(project_key=project, origin=origin)
        projects = {project for project, _origin in self._grant_partitions}
        engine = self._engine
        slots = self._conversations._memory_slots(projects)
        if slots:
            with engine._lock if engine is not None else nullcontext():
                for project, conversation_id, revision in slots:
                    self._conversations.clear(project, conversation_id, revision + 1)
        self._grant_partitions.clear()

    def configure(self, request: ConfigureRequest) -> dict[str, object]:
        if type(request) is not ConfigureRequest:
            raise ServiceError("AI_INVALID_INPUT")
        # Revalidate the trusted parsed value before changing any live authority.
        proposed = ProviderConfig.from_base_url(request.base_url, request.model, 0)
        if (type(request.enabled) is not bool or request.key_action not in ("keep", "clear", "set")
                or (request.key_action == "set") != (type(request.key) is MemoryKey)):
            raise ServiceError("AI_INVALID_INPUT")
        if request.key is not None:
            request.key.__post_init__()
        with self._lock:
            if self._closed or self._finalizing:
                raise ServiceError("AI_DISABLED")
            old = self._config
            destination_changed = old is not None and old.endpoint != proposed.endpoint
            if destination_changed and request.key_action == "keep":
                raise ServiceError("AI_INVALID_INPUT")
            if (old is not None and old.base_url == proposed.base_url and old.model == proposed.model
                    and self._enabled == request.enabled and request.key_action == "keep"
                    and not self._closing):
                return self._settings_locked()
            revision = self._config_revision + 1
            if revision > 2**53 - 1:
                raise ServiceError("AI_REQUEST_LIMIT")
            config = ProviderConfig.from_base_url(proposed.base_url, proposed.model, revision)
            self._invalidate_locked()
            key = self._key if request.key_action == "keep" else request.key
            self._config, self._key = config, key
            self._config_revision, self._enabled = revision, request.enabled
            if self._engine is not None:
                self._engine._replace_connection_config(
                    config if self._enabled and self._key is not None else None)
            self._closing = False
            result = self._settings_locked()
        self._drain_pending_closures()
        return result

    def start(self, request: StartRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not StartRequest:
            raise ServiceError("AI_INVALID_INPUT")
        try:
            selection = request.context
            if type(selection) is QuestionContext:
                context_payload: dict[str, object] = {"kind": "question"}
            elif type(selection) is ProjectContext:
                context_payload = {"kind": "project"}
            elif type(selection) is UnderstandingContext:
                context_payload = {"kind": "understanding", "artifactId": selection.artifact_id,
                                   "revision": selection.revision, "itemId": selection.item_id}
            elif type(selection) is NodeContext:
                context_payload = {"kind": "node", "viewKind": selection.view_kind.value,
                    "visibleNodeId": selection.visible_node_id, "rootId": selection.root_id,
                    "detailSourceId": selection.detail_source_id}
            elif type(selection) is SourceContext:
                anchor = selection.anchor
                context_payload = {"kind": "source", "filePath": selection.file_path, "mode": selection.mode,
                    "anchor": None if anchor is None else {"filePath": anchor.file_path,
                        "startLine": anchor.start_line, "startColumn": anchor.start_column,
                        "endLine": anchor.end_line, "endColumn": anchor.end_column}}
            else:
                raise ValueError()
            checked = parse_start({"projectEpoch": request.scope.project_epoch,
                "analysisGeneration": request.scope.analysis_generation, "requestId": request.scope.request_id,
                "conversationId": request.conversation_id, "configRevision": request.config_revision,
                "consentRevision": request.consent_revision, "question": request.question, "context": context_payload})
            if checked != request:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise ServiceError("AI_INVALID_INPUT") from None
        identity = self._context_identity(ctx)
        with self._lock:
            project = self._require_context_locked(request.scope.project_epoch, identity, ctx.snapshot)
            if (not ctx.eligible or ctx.snapshot is None or ctx.status.value not in ("Ready", "Partial")
                    or request.scope.analysis_generation != ctx.analysis_generation):
                raise ServiceError("AI_ANALYSIS_REQUIRED")
            if not self._enabled or self._closing:
                raise ServiceError("AI_DISABLED")
            config, key = self._config, self._key
            if config is None or key is None or config.revision != request.config_revision:
                raise ServiceError("AI_NOT_CONFIGURED")
            grant = self._consents.get(project_key=project, origin=config.origin)
            source = self._source_consent
            if (grant is None or not self._consents.is_current(grant, config=config)
                    or grant.revision != request.consent_revision or source is None
                    or source.binding != ctx.snapshot.binding or source.allowed_files != grant.allowed_files
                    or source.provider_origin != config.origin):
                raise ServiceError("AI_CONSENT_REQUIRED")
            revision = self._conversations.revision_of(project, request.conversation_id)
            if revision is None:
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            understanding = None
            understanding_owner = None
            understanding_owner_revision = None
            if type(selection) is UnderstandingContext:
                understanding = UnderstandingRef(selection.artifact_id, selection.revision, selection.item_id)
                engine = self._engine
                if engine is None:
                    raise ServiceError("AI_RESULT_UNAVAILABLE")
                with engine._lock:
                    reference, _answer = engine._capture_understanding_item_locked(understanding)
                understanding_owner = (project, cast(str, reference["conversationId"]), cast(str, reference["jobId"]))
                understanding_owner_revision = cast(int, reference["conversationRevision"])
            factory = self._connection_provider_factory
            if factory is None:
                raise ServiceError("AI_DEPENDENCY_MISSING")
            version = self._memory_revision
            revisions = self._conversations._project_revisions(project)
            index = self._start_index
        authority = AuthorityState.from_trusted(config, grant, revisions)
        history = self._conversations._select_eligible_history(project, request.conversation_id, revision,
            project_epoch=request.scope.project_epoch, generation=request.scope.analysis_generation,
            origin=config.origin, config_revision=config.revision, consent_revision=grant.revision,
            normalized_scope=authority.scope_fingerprint)
        if type(selection) in (QuestionContext, ProjectContext, UnderstandingContext):
            root = None
        else:
            if index is None or index.snapshot is not ctx.snapshot:
                raise ServiceError("AI_EVIDENCE_INVALID")
            try:
                root = index.resolve(selection, ctx, grant.allowed_files)
            except ContextSelectionError:
                raise ServiceError("AI_EVIDENCE_INVALID") from None
        digest_context = hashlib.sha256(json.dumps({"selection": context_payload,
            "root": None if root is None else root.canonical()}, ensure_ascii=True,
            sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        purpose: Literal["explanation", "project_understanding"] = (
            "project_understanding" if type(selection) is ProjectContext else "explanation")
        draft = explanation_draft(
            request.question,
            history,
            monotonic() + self._explanation_budget.timeout_seconds,
            self._explanation_budget.final_output_tokens,
            purpose=purpose,
        )
        job_request = JobRequest(request.scope, request.conversation_id, request.question,
            digest_context, config.model, config.revision, grant.revision, authority.scope_fingerprint,
            draft, identity, root, seed_search=type(selection) is QuestionContext,
            budget=replace(self._explanation_budget), purpose=purpose,
            understanding=understanding, understanding_owner=understanding_owner,
            understanding_owner_revision=understanding_owner_revision)
        digest = job_request.digest()
        engine = self._ensure_engine(config, key)
        admitted_at, job_id = monotonic(), uuid.uuid4().hex
        with self._lock:
            self._require_context_locked(request.scope.project_epoch, identity, ctx.snapshot)
            if (version != self._memory_revision or self._config is not config or self._key is not key
                    or not self._enabled or self._closing or not self._consents.is_current(grant, config=config)
                    or self._conversations._project_revisions(project) != revisions):
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            self._authority = authority
            engine._replace_runtime_authority(authority, source)
            snapshot, admitted = engine._admit_job(job_request,
                provider_factory=partial(factory, config, key), expected_conversation_revision=revision,
                prepared_digest=digest, prepared_at=admitted_at, prepared_job_id=job_id)
        if admitted is not None:
            engine._schedule_admitted(admitted)
        return {"jobId": snapshot.job_id, "status": snapshot.status.value, "kind": "explanation",
                "conversationId": request.conversation_id, "scope": {
                    "projectEpoch": request.scope.project_epoch,
                    "analysisGeneration": request.scope.analysis_generation, "requestId": request.scope.request_id}}

    def resolve_evidence(self, request: ResolveRequest, ctx: AssistantContext) -> dict[str, object]:
        if type(request) is not ResolveRequest:
            raise ServiceError("AI_INVALID_INPUT")
        if request.understanding is not None and type(request.understanding) is not UnderstandingRef:
            raise ServiceError("AI_INVALID_INPUT")
        checked = parse_resolve({"projectEpoch": request.project_epoch,
            "analysisGeneration": request.analysis_generation, "conversationId": request.conversation_id,
            "evidenceId": request.evidence_id, "action": request.action,
            **({"understanding": request.understanding.to_public()} if request.understanding is not None else {})})
        if checked != request:
            raise ServiceError("AI_INVALID_INPUT")
        publication = self._context_identity(ctx)
        with self._lock:
            project = self._require_context_locked(request.project_epoch, publication, ctx.snapshot)
            if not ctx.eligible or ctx.snapshot is None or ctx.analysis_generation != request.analysis_generation:
                raise ServiceError("AI_ANALYSIS_REQUIRED")
            engine = self._engine
            if not self._enabled or self._closing or engine is None:
                raise ServiceError("AI_RESULT_UNAVAILABLE")
            version = self._memory_revision
            try:
                with engine._lock:
                    retained = engine._retained_identity_locked(request.conversation_id)
                    owner, context = self._conversations._capture_evidence_owner(
                        project, request.conversation_id, request.evidence_id)
                    if not engine._understanding_matches_locked(request.understanding, owner,
                            request.evidence_id, retained):
                        raise ServiceError("AI_EVIDENCE_INVALID")
                    replay = engine._navigation_replay_locked(request, publication, retained)
                    if replay is not None:
                        return engine._navigation_receipt(replay)
            except AgentError:
                raise ServiceError("AI_EVIDENCE_INVALID") from None
        # RetainedEvidenceContext.borrow allocates a ledger and samples a clock.
        # Neither it nor release/freshness may run under publication/state locks.
        try:
            lease = context.borrow(retained)
        except AgentError:
            raise ServiceError("AI_EVIDENCE_INVALID") from None
        admitted: _NavigationJob | None = None
        try:
            job_id = uuid.uuid4().hex
            with self._lock:
                self._require_context_locked(request.project_epoch, publication, ctx.snapshot)
                if version != self._memory_revision or not self._enabled or self._closing or self._engine is not engine:
                    raise ServiceError("AI_RESULT_UNAVAILABLE")
                with engine._lock:
                    try:
                        current = engine._retained_identity_locked(request.conversation_id)
                        current_owner, current_context = self._conversations._capture_evidence_owner(
                            project, request.conversation_id, request.evidence_id)
                    except AgentError:
                        raise ServiceError("AI_EVIDENCE_INVALID") from None
                    if (current != retained or current_owner != owner or current_context is not context
                            or engine._identity != publication or engine._snapshot is not ctx.snapshot):
                        raise ServiceError("AI_RESULT_UNAVAILABLE")
                    if not engine._understanding_matches_locked(request.understanding, owner,
                            request.evidence_id, current):
                        raise ServiceError("AI_EVIDENCE_INVALID")
                    replay = engine._navigation_replay_locked(request, publication, retained)
                    if replay is not None:
                        return engine._navigation_receipt(replay)
                    if (len(engine._active_job_ids) + len(engine._connection_slots)
                            + len(engine._navigation_jobs) >= 2
                            or any(job.publication.project_key == project for job in engine._navigation_jobs.values())):
                        raise ServiceError("AI_CAPACITY")
                    admitted = _NavigationJob(job_id, context._scope, publication,
                        PollIdentity("navigation", job_id, request.project_epoch, request.analysis_generation,
                            request.conversation_id, retained.conversation_revision),
                        retained, owner, request.evidence_id, request.action, ctx, context, lease,
                        understanding=request.understanding)
                    engine._navigation_jobs[job_id] = admitted
                    result = engine._navigation_receipt(admitted)
        finally:
            if admitted is None:
                lease.release()
        engine._schedule_navigation(admitted)
        return result

    def test_connection(self, request: ConnectionRequest) -> dict[str, object]:
        if type(request) is not ConnectionRequest:
            raise ServiceError("AI_INVALID_INPUT")
        try:
            parse_connection({"configRevision": request.config_revision,
                              "requestId": request.request_id})
        except ValueError:
            raise ServiceError("AI_INVALID_INPUT") from None
        with self._lock:
            if self._closed or self._finalizing or self._closing or not self._enabled:
                raise ServiceError("AI_DISABLED")
            config, key = self._config, self._key
            if config is None or key is None or request.config_revision != config.revision:
                raise ServiceError("AI_NOT_CONFIGURED")
            factory = self._connection_provider_factory
            if factory is None:
                raise ServiceError("AI_DEPENDENCY_MISSING")
            frozen_factory = partial(factory, config, key)
        engine = self._ensure_engine(config, key)
        return engine._start_connection(request, frozen_factory)

    def _ensure_engine(self, config: ProviderConfig, key: MemoryKey) -> AssistantService:
        """Serialize lazy construction outside state locks, then reconcile before exposure."""
        with self._engine_creation_lock:
            with self._lock:
                if (self._config is not config or self._key is not key or not self._enabled
                        or self._closed or self._closing or self._finalizing):
                    raise ServiceError("AI_NOT_CONFIGURED")
                if self._engine is not None:
                    return self._engine
            engine = AssistantService(_unavailable_provider,
                before_dispatch=self._connection_before_dispatch, conversations=self._conversations,
                trusted_input_profiles=self._trusted_input_profiles,
                trusted_connection_profiles=self._trusted_connection_profiles,
                explanation_budget=self._explanation_budget,
                _validation_diagnostics=self._validation_diagnostics)
            with self._lock:
                current = (self._config is config and self._key is key and self._enabled
                           and not (self._closed or self._closing or self._finalizing))
                if current:
                    if self._identity is not None:
                        engine._replace_publication(self._identity, self._snapshot)
                    engine._replace_connection_config(config)
                    engine._replace_runtime_authority(self._authority,
                        self._source_consent if self._authority is not None else None)
                    self._engine = engine
            if not current:
                # No job or resource has been admitted. Do not clear the
                # borrowed application store when discarding this unused loop.
                engine._loop.call_soon_threadsafe(engine._loop.stop)
                engine._thread.join(timeout=3)
                engine._b_executor.shutdown(wait=True)
                if engine._thread.is_alive():
                    raise ServiceError("AI_CLEANUP_INCOMPLETE")
                raise ServiceError("AI_NOT_CONFIGURED")
            return engine

    def poll(self, request: PollRequest) -> dict[str, object]:
        if type(request) is not PollRequest:
            raise ServiceError("AI_INVALID_INPUT")
        try:
            parse_poll({"jobId": request.job_id, "projectEpoch": request.project_epoch,
                        "afterSeq": request.after_seq})
        except ValueError:
            raise ServiceError("AI_INVALID_INPUT") from None
        with self._lock:
            engine = self._engine
        if engine is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with engine._lock:
            navigation = request.job_id in engine._navigation_jobs or request.job_id in engine._navigation_completed
        if navigation:
            return engine._poll_navigation(request)
        return (engine._poll_connection(request) if request.project_epoch is None
                else engine._poll_explanation(request))

    def cancel(self, request: CancelRequest) -> dict[str, object]:
        if type(request) is not CancelRequest:
            raise ServiceError("AI_INVALID_INPUT")
        try:
            parse_cancel({"jobId": request.job_id, "projectEpoch": request.project_epoch})
        except ValueError:
            raise ServiceError("AI_INVALID_INPUT") from None
        with self._lock:
            engine = self._engine
        if engine is None:
            raise ServiceError("AI_RESULT_UNAVAILABLE")
        with engine._lock:
            navigation = request.job_id in engine._navigation_jobs or request.job_id in engine._navigation_completed
        if navigation:
            return engine._cancel_navigation(request)
        return (engine._cancel_connection(request) if request.project_epoch is None
                else engine._cancel_explanation(request))

    def replace_current(self, identity: PublicationIdentity) -> None:
        self._replace_publication(identity, None)

    def _replace_publication(self, identity: PublicationIdentity,
                             snapshot: AnalysisSnapshot | None) -> None:
        # Controller owns validation. This mirror performs only bounded swaps
        # and revocation; the controller drains cleanup after its lock is free.
        with self._lock:
            old = self._identity
            self._identity = identity
            self._memory_revision += 1
            if old != identity:
                self._authority = None
            self._snapshot = (self._snapshot if old is not None and old.eligible and identity.eligible
                              and AssistantService._dependency_identity(old)
                              == AssistantService._dependency_identity(identity) and snapshot is None
                              else snapshot)
            if self._engine is not None:
                self._engine._replace_publication(identity, self._snapshot)

    def _drain_pending_closures(self) -> None:
        with self._lock:
            engine = self._engine
            snapshot, index = self._snapshot, self._start_index
        if snapshot is not None and (index is None or index.snapshot is not snapshot):
            prepared_index = StartContextIndex.build(snapshot)
            with self._lock:
                if self._snapshot is snapshot:
                    self._start_index = prepared_index
        self._conversations._drain_retained_closures()
        if engine is not None:
            engine._drain_pending_closures()

    def revoke_consent(self, project_epoch: str) -> dict[str, object]:
        with self._lock:
            identity, config = self._identity, self._config
            if (identity is None or identity.project_epoch != project_epoch
                    or identity.project_key is None):
                raise ServiceError("AI_ANALYSIS_REQUIRED")
            if self._engine is not None:
                self._engine._replace_runtime_authority(None, None)
            self._authority, self._source_consent = None, None
            self._memory_revision += 1
            engine = self._engine
            with engine._lock if engine is not None else nullcontext():
                for project, conversation_id, current_revision in self._conversations._memory_slots({identity.project_key}):
                    self._conversations.clear(project, conversation_id, current_revision + 1)
            revision = (0 if config is None else self._consents.revoke(
                project_key=identity.project_key, origin=config.origin))
            if config is not None:
                self._grant_partitions.discard((identity.project_key, config.origin))
        self._drain_pending_closures()
        return {"revoked": True, "consentRevision": revision}

    def prepare_close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._invalidate_locked()
            self._enabled, self._key, self._closing = False, None, True
            engine = self._engine
            if engine is None:
                for project, conversation_id, revision in self._conversations._memory_slots():
                    self._conversations.clear(project, conversation_id, revision + 1)
        self._conversations._drain_retained_closures()
        if engine is not None:
            engine._prepare_close()

    def shutdown(self) -> None:
        # Both native closed and the launcher fallback may enter. Serialize the
        # final drain and reject configuration before dropping authority/key.
        with self._shutdown_lock:
            with self._lock:
                if self._closed:
                    return
                self._finalizing = True
            self.prepare_close()
            with self._lock:
                engine = self._engine
            if engine is not None:
                engine.shutdown()
            with self._lock:
                self._engine, self._snapshot, self._identity = None, None, None
                self._start_index = None
                self._closed = True
