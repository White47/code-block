"""Explicit C7 snapshots, separate from internal rings, jobs and provider DTOs."""

import json
from dataclasses import dataclass, field
from typing import Literal

from ._connection_values import ConnectionCapabilities, _copy_connection_capabilities
from ._usage_values import InputAccountingSnapshot, ObservedUsage
from .answer_public import answer_to_public
from .bridge_requests import _closed, _id, _natural, _text
from .budget import BudgetUsage
from .contracts import JobStatus
from .issues import SafeIssue
from .jobs import JobSnapshot
from .navigation import NavigationTarget
from .navigation_public import navigation_target_to_public
from .response import Answer


class PollProjectionError(ValueError):
    code = "AI_PROTOCOL_INCOMPATIBLE"

    def __init__(self) -> None:
        super().__init__("Invalid assistant poll state.")


@dataclass(frozen=True, slots=True)
class PollIdentity:
    kind: Literal["explanation", "connection", "navigation"]
    job_id: str
    project_epoch: str | None
    analysis_generation: int | None
    conversation_id: str | None
    conversation_revision: int | None


@dataclass(frozen=True, slots=True)
class PollState:
    identity: PollIdentity
    lifecycle: JobSnapshot
    config_revision: int
    consent_revision: int | None
    seq: int
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    observed: ObservedUsage = field(default_factory=ObservedUsage)
    display_text: str = field(default="", repr=False)
    answer: Answer | None = field(default=None, repr=False)
    connection_supported: bool = False
    evidence_id: str | None = None
    action: Literal["source", "graph"] | None = None
    target: NavigationTarget | None = field(default=None, repr=False)
    input_accounting: InputAccountingSnapshot | None = None
    connection_capabilities: ConnectionCapabilities | None = None


@dataclass(frozen=True, slots=True)
class DisplayEvent:
    seq: int
    kind: Literal["status", "display_text", "usage", "result", "lifecycle"]
    payload: dict[str, object] = field(repr=False)


def _validated_usage(value: object, kind: object) -> dict[str, object]:
    if kind not in ("explanation", "connection"):
        raise ValueError()
    usage = _closed(value, {"reserved", "observed"}) if (
        type(value) is dict and value.keys() == {"reserved", "observed"}
    ) else _closed(value, {"reserved", "observed", "inputAccounting"})
    reserved = _closed(usage["reserved"], {
        "inputTokens", "outputTokens", "modelRequests", "toolCalls",
        "sourceCharacters", "fileCount",
    })
    observed = _closed(usage["observed"], {"inputTokens", "outputTokens", "basis"})
    for counter in reserved.values():
        _natural(counter)
    if observed["basis"] == "unknown":
        if observed["inputTokens"] is not None or observed["outputTokens"] is not None:
            raise ValueError()
    elif type(observed["basis"]) is str and observed["basis"] in ("actual", "estimated"):
        _natural(observed["inputTokens"])
        _natural(observed["outputTokens"])
    else:
        raise ValueError()
    public: dict[str, object] = {
        "reserved": dict(reserved),
        "observed": dict(observed),
    }
    if "inputAccounting" not in usage:
        return public
    accounting = _closed(usage["inputAccounting"], {
        "basis", "method", "limitTokens", "upperBound",
        "knownActualInputTokens", "blockedReason",
    })
    expected_limits = (20_000, 100_000) if kind == "explanation" else (1024,)
    if (type(accounting["basis"]) is not str or accounting["basis"] != "estimated"
            or type(accounting["method"]) is not str
            or accounting["method"] != "wire-bytes-v1"
            or type(accounting["limitTokens"]) is not int
            or accounting["limitTokens"] not in expected_limits
            or type(accounting["upperBound"]) is not bool
            or accounting["upperBound"] is not False):
        raise ValueError()
    limit = accounting["limitTokens"]
    known_actual = _natural(accounting["knownActualInputTokens"])
    blocked_reason = accounting["blockedReason"]
    if (blocked_reason is not None
            and (type(blocked_reason) is not str
                 or blocked_reason not in (
                     "estimated_limit", "observed_overrun", "usage_unknown"
                 ))):
        raise ValueError()
    if observed["basis"] == "actual" and known_actual != observed["inputTokens"]:
        raise ValueError()
    if blocked_reason == "usage_unknown" and observed["basis"] != "unknown":
        raise ValueError()
    if (blocked_reason == "observed_overrun"
            and (observed["basis"] != "actual" or known_actual <= limit)):
        raise ValueError()
    public["inputAccounting"] = dict(accounting)
    return public


def _usage(reserved: BudgetUsage, observed: ObservedUsage,
           accounting: InputAccountingSnapshot | None, kind: object) -> dict[str, object]:
    if type(reserved) is not BudgetUsage or type(observed) is not ObservedUsage:
        raise ValueError()
    if type(reserved.files) is not tuple or len(reserved.files) > 8:
        raise ValueError()
    for path in reserved.files:
        _text(path, 4096)
    if len(set(reserved.files)) != len(reserved.files):
        raise ValueError()
    public_reserved = {
        "inputTokens": _natural(reserved.input_tokens), "outputTokens": _natural(reserved.output_tokens),
        "modelRequests": _natural(reserved.model_requests), "toolCalls": _natural(reserved.tool_calls),
        "sourceCharacters": _natural(reserved.source_characters), "fileCount": len(reserved.files),
    }
    if observed.basis not in ("actual", "estimated", "unknown"):
        raise ValueError()
    if observed.basis == "unknown":
        if observed.input_tokens is not None or observed.output_tokens is not None:
            raise ValueError()
    else:
        _natural(observed.input_tokens)
        _natural(observed.output_tokens)
    public: dict[str, object] = {"reserved": public_reserved, "observed": {
        "inputTokens": observed.input_tokens, "outputTokens": observed.output_tokens,
        "basis": observed.basis}}
    if accounting is not None:
        if type(accounting) is not InputAccountingSnapshot:
            raise ValueError()
        accounting.__post_init__()
        public["inputAccounting"] = {
            "basis": "estimated",
            "method": accounting.method,
            "limitTokens": accounting.limit_tokens,
            "upperBound": False,
            "knownActualInputTokens": accounting.known_actual_input_tokens,
            "blockedReason": accounting.blocked_reason,
        }
    return _validated_usage(public, kind)


def _common_projection(state: PollState) -> dict[str, object]:
    if type(state) is not PollState or type(state.identity) is not PollIdentity or type(state.lifecycle) is not JobSnapshot:
        raise ValueError()
    identity, lifecycle = state.identity, state.lifecycle
    _id(identity.job_id)
    _natural(state.seq)
    _natural(state.config_revision)
    if identity.kind not in ("explanation", "connection", "navigation") or lifecycle.job_id != identity.job_id:
        raise ValueError()
    if type(lifecycle.status) is not JobStatus:
        raise ValueError()
    for flag in (lifecycle.cancel_requested, lifecycle.cancel_accepted,
                 lifecycle.dispatch_closed, lifecycle.resources_released, state.connection_supported):
        if type(flag) is not bool:
            raise ValueError()
    terminal = lifecycle.status not in (JobStatus.QUEUED, JobStatus.RUNNING)
    if (terminal and not lifecycle.dispatch_closed
            or lifecycle.resources_released and not lifecycle.dispatch_closed
            or lifecycle.cancel_accepted and not (lifecycle.cancel_requested and lifecycle.dispatch_closed
                                                  and lifecycle.resources_released)
            or lifecycle.status is JobStatus.CANCELLED and not lifecycle.cancel_accepted
            or lifecycle.status is JobStatus.COMPLETED and lifecycle.cancel_requested):
        raise ValueError()
    if identity.kind == "connection":
        if any(value is not None for value in (identity.project_epoch, identity.analysis_generation,
                                               identity.conversation_id, identity.conversation_revision,
                                               state.consent_revision)):
            raise ValueError()
    else:
        _id(identity.project_epoch)
        _natural(identity.analysis_generation)
        _id(identity.conversation_id)
        _natural(identity.conversation_revision)
        _natural(state.consent_revision)
    issue = lifecycle.issue
    if issue is not None and type(issue) is not SafeIssue:
        raise ValueError()
    public: dict[str, object] = {
        "kind": identity.kind, "jobId": identity.job_id, "projectEpoch": identity.project_epoch,
        "analysisGeneration": identity.analysis_generation, "conversationId": identity.conversation_id,
        "conversationRevision": identity.conversation_revision, "status": lifecycle.status.value,
        "configRevision": state.config_revision, "consentRevision": state.consent_revision,
        "cancelRequested": lifecycle.cancel_requested, "cancelAccepted": lifecycle.cancel_accepted,
        "cancellation": ("confirmed" if lifecycle.cancel_accepted else
                         "requested" if lifecycle.cancel_requested else "none"),
        "dispatchClosed": lifecycle.dispatch_closed,
        "resources": ("released" if lifecycle.resources_released else "closing"
                      if lifecycle.dispatch_closed or lifecycle.cancel_requested else "held"),
        "resourcesReleased": lifecycle.resources_released,
        "issue": None if issue is None else issue.to_public(),
    }
    return public


def _project(state: PollState) -> dict[str, object]:
    public = _common_projection(state)
    identity, lifecycle = state.identity, state.lifecycle
    completed = lifecycle.status is JobStatus.COMPLETED
    capabilities = (None if state.connection_capabilities is None else
                    _copy_connection_capabilities(state.connection_capabilities))
    if type(state.display_text) is not str or len(state.display_text.encode("utf-8")) > 64 * 1024:
        raise ValueError()
    if state.answer is not None and type(state.answer) is not Answer:
        raise ValueError()
    if identity.kind != "explanation" and (state.answer is not None or state.display_text):
        raise ValueError()
    if identity.kind != "connection" and state.connection_supported:
        raise ValueError()
    if identity.kind != "connection" and capabilities is not None:
        raise ValueError()
    if identity.kind != "navigation" and (state.evidence_id is not None or state.action is not None or state.target is not None):
        raise ValueError()
    if identity.kind == "explanation":
        if completed and state.answer is None:
            raise ValueError()
        public.update(displayText=state.display_text,
                      usage=_usage(state.usage, state.observed,
                                   state.input_accounting, identity.kind),
                      answer=answer_to_public(state.answer) if completed and state.answer is not None else None)
    elif identity.kind == "connection":
        if completed and not state.connection_supported:
            raise ValueError()
        if not completed and capabilities is not None:
            raise ValueError()
        connection_result = None
        if completed:
            connection_result = (
                {"toolSupported": True, "structuredOutputSupported": True}
                if capabilities is None else {
                    "toolSupported": capabilities.tool_supported,
                    "structuredOutputSupported": capabilities.structured_output_supported,
                    "jsonObjectSupported": capabilities.json_object_supported,
                    "outputValidation": capabilities.output_validation,
                }
            )
        public.update(usage=_usage(state.usage, state.observed,
                                   state.input_accounting, identity.kind), connectionResult=(
            connection_result))
    else:
        if state.input_accounting is not None:
            raise ValueError()
        _text(state.evidence_id, 512)
        if state.action not in ("source", "graph"):
            raise ValueError()
        target = None
        if completed:
            if state.target is None:
                raise ValueError()
            target = navigation_target_to_public(state.target)
            scope = target["scope"]
            if (type(scope) is not dict or scope["projectEpoch"] != identity.project_epoch
                    or scope["analysisGeneration"] != identity.analysis_generation
                    or target["evidenceId"] != state.evidence_id or target["action"] != state.action
                    or target["conversationId"] != identity.conversation_id
                    or target["conversationRevision"] != identity.conversation_revision
                    or target["configRevision"] != state.config_revision
                    or target["consentRevision"] != state.consent_revision):
                raise ValueError()
        public.update(evidenceId=state.evidence_id, action=state.action, target=target)
    if len(json.dumps(public, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")) > 160 * 1024:
        raise ValueError()
    return public


def snapshot_to_public(state: PollState) -> dict[str, object]:
    """Project only a trusted captured snapshot; authority checks stay in service."""
    try:
        return _project(state)
    except Exception:
        pass
    raise PollProjectionError() from None


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _event(event: DisplayEvent, identity: dict[str, object]) -> dict[str, object]:
    if type(event) is not DisplayEvent or type(event.kind) is not str:
        raise ValueError()
    _natural(event.seq, 1)
    data: dict[str, object]
    if event.kind == "status":
        data = _closed(event.payload, {"status"})
        if type(data["status"]) is not str or data["status"] not in {s.value for s in JobStatus}:
            raise ValueError()
    elif event.kind == "display_text":
        data = _closed(event.payload, {"text"})
        if identity["kind"] != "explanation" or type(data["text"]) is not str or not data["text"]:
            raise ValueError()
        data["text"].encode("utf-8")
    elif event.kind == "result":
        data = _closed(event.payload, {"available"})
        if type(data["available"]) is not bool:
            raise ValueError()
    elif event.kind == "lifecycle":
        data = _closed(event.payload, {"cancelRequested", "cancelAccepted", "dispatchClosed", "resourcesReleased"})
        if (any(type(flag) is not bool for flag in data.values())
                or data["cancelAccepted"] and not (data["cancelRequested"] and data["dispatchClosed"] and data["resourcesReleased"])
                or data["resourcesReleased"] and not data["dispatchClosed"]):
            raise ValueError()
    elif event.kind == "usage":
        data = _closed(event.payload, {"usage"})
        if identity["kind"] == "navigation":
            raise ValueError()
        data = {"usage": _validated_usage(data["usage"], identity["kind"])}
    else:
        raise ValueError()
    public = {**identity, "seq": event.seq, "type": event.kind, **data}
    if len(_encoded(public)) > 8192:
        raise ValueError()
    return public


def _delivery(state: PollState, after_seq: int, events: tuple[DisplayEvent, ...],
              reset: bool, evicted: tuple[str, ...]) -> dict[str, object]:
    public = _project(state)
    _natural(after_seq)
    if (after_seq > state.seq or type(reset) is not bool or type(events) is not tuple
            or len(events) > 256 or reset and events or type(evicted) is not tuple
            or len(evicted) > 10 or len(set(evicted)) != len(evicted)):
        raise ValueError()
    for identifier in evicted:
        _id(identifier)
    selected: list[dict[str, object]] = []
    public.update(seq=state.seq, reset=reset, events=selected, evictedConversationIds=list(evicted))
    if len(_encoded(public)) > 160 * 1024:
        raise ValueError()
    identity = {key: public[key] for key in ("kind", "jobId", "projectEpoch", "analysisGeneration",
                                            "conversationId", "conversationRevision")}
    previous = after_seq
    full = False
    for event in events:
        candidate = _event(event, identity)
        if not previous < event.seq <= state.seq:
            raise ValueError()
        previous = event.seq
        if not full:
            trial = [*selected, candidate]
            if (len(trial) <= 32 and len(_encoded(trial)) <= 64 * 1024
                    and len(_encoded({**public, "events": trial})) <= 160 * 1024):
                selected.append(candidate)
            else:
                full = True
    return public


def poll_to_public(state: PollState, *, after_seq: int, events: tuple[DisplayEvent, ...] = (),
                   reset: bool = False, evicted: tuple[str, ...] = ()) -> dict[str, object]:
    """Bound a captured C7 delta; current seq/snapshot recover omitted display events."""
    try:
        return _delivery(state, after_seq, events, reset, evicted)
    except Exception:
        pass
    raise PollProjectionError() from None


def cancel_receipt_to_public(state: PollState) -> dict[str, object]:
    try:
        return {**_common_projection(state), "seq": state.seq}
    except Exception:
        pass
    raise PollProjectionError() from None
