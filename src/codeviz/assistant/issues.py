"""Closed assistant issue values, separate from internal exception diagnostics."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

ProtocolBoundary = Literal["pre_send", "dispatch", "result_validation"]
ProtocolCheckSite = Literal[
    "response_headers", "sse_syntax", "chunk_shape", "usage_shape", "delta_shape",
    "terminal_finish", "stream_eof", "sdk_decode", "context_exit", "cleanup",
]
ProtocolFinishReason = Literal["length", "content_filter", "insufficient_system_resource", "aborted"]
_CHECK_SITES = (
    "response_headers", "sse_syntax", "chunk_shape", "usage_shape", "delta_shape",
    "terminal_finish", "stream_eof", "sdk_decode", "context_exit", "cleanup",
)
_FINISH_REASONS = ("length", "content_filter", "insufficient_system_resource", "aborted")

AIErrorCode = Literal[
    "AI_NOT_CONFIGURED", "AI_DISABLED", "AI_DEPENDENCY_MISSING", "AI_ANALYSIS_REQUIRED",
    "AI_ANALYSIS_BUSY", "AI_ANALYSIS_STALE", "AI_CONSENT_REQUIRED", "AI_SOURCE_BLOCKED",
    "AI_AUTH_FAILED", "AI_RATE_LIMITED", "AI_PROTOCOL_INCOMPATIBLE", "AI_NETWORK_TIMEOUT",
    "AI_EVIDENCE_INVALID", "AI_CANCELLED", "AI_BUDGET_EXCEEDED", "AI_CAPACITY",
    "AI_REQUEST_CONFLICT", "AI_REQUEST_LIMIT", "AI_RESULT_UNAVAILABLE", "AI_INVALID_INPUT",
]

_MESSAGES = MappingProxyType({
    "AI_NOT_CONFIGURED": "Configure the assistant before sending a request.",
    "AI_DISABLED": "The assistant is disabled.",
    "AI_DEPENDENCY_MISSING": "The assistant runtime is unavailable.",
    "AI_ANALYSIS_REQUIRED": "Analyze the project before asking the assistant.",
    "AI_ANALYSIS_BUSY": "Project analysis is in progress.",
    "AI_ANALYSIS_STALE": "Project analysis has changed. Analyze the project again.",
    "AI_CONSENT_REQUIRED": "Confirm the assistant source access scope first.",
    "AI_SOURCE_BLOCKED": "The requested source is unavailable to the assistant.",
    "AI_AUTH_FAILED": "Assistant authentication failed.",
    "AI_RATE_LIMITED": "The assistant provider rate limit was reached.",
    "AI_PROTOCOL_INCOMPATIBLE": "The assistant response could not be accepted.",
    "AI_NETWORK_TIMEOUT": "The assistant request timed out.",
    "AI_EVIDENCE_INVALID": "Assistant evidence validation failed.",
    "AI_CANCELLED": "The assistant request was cancelled.",
    "AI_BUDGET_EXCEEDED": "The assistant request budget was reached.",
    "AI_CAPACITY": "The assistant is busy. Wait for an active request to finish.",
    "AI_REQUEST_CONFLICT": "The request identifier is already in use for another request.",
    "AI_REQUEST_LIMIT": "The assistant request limit was reached.",
    "AI_RESULT_UNAVAILABLE": "The assistant result is no longer available.",
    "AI_INVALID_INPUT": "The assistant request is invalid.",
})

_ALIASES = MappingProxyType({
    "AI_STALE": "AI_ANALYSIS_STALE",
    "AI_JOB_NOT_FOUND": "AI_RESULT_UNAVAILABLE",
    "AI_CONVERSATION_CLEARED": "AI_RESULT_UNAVAILABLE",
    "AI_BUSY": "AI_CAPACITY",
    "AI_CONVERSATION_CAPACITY": "AI_CAPACITY",
    "AI_PUBLICATION_BACKLOG": "AI_CAPACITY",
})


@dataclass(frozen=True, slots=True)
class ProtocolDiagnostic:
    boundary: ProtocolBoundary
    http_status: int | None = None
    check_site: ProtocolCheckSite | None = None
    finish_reason: ProtocolFinishReason | None = None

    def __post_init__(self) -> None:
        if (type(self.boundary) is not str
                or self.boundary not in ("pre_send", "dispatch", "result_validation")
                or (self.http_status is not None
                    and (self.boundary != "dispatch"
                         or type(self.http_status) is not int
                         or not 100 <= self.http_status <= 599))
                or (self.check_site is not None
                    and (self.boundary != "dispatch" or self.http_status is not None
                         or type(self.check_site) is not str or self.check_site not in _CHECK_SITES))
                or (self.finish_reason is not None
                    and (self.check_site != "terminal_finish" or type(self.finish_reason) is not str
                         or self.finish_reason not in _FINISH_REASONS))):
            raise ValueError("Invalid protocol diagnostic.")

    def to_public(self) -> dict[str, object]:
        self.__post_init__()
        value: dict[str, object] = {"boundary": self.boundary}
        if self.http_status is not None:
            value["httpStatus"] = self.http_status
        if self.check_site is not None:
            value["checkSite"] = self.check_site
        if self.finish_reason is not None:
            value["finishReason"] = self.finish_reason
        return value


@dataclass(frozen=True, slots=True)
class SafeIssue:
    code: AIErrorCode
    diagnostic: ProtocolDiagnostic | None = None

    def __post_init__(self) -> None:
        if (type(self.code) is not str or self.code not in _MESSAGES
                or (self.diagnostic is not None
                    and (self.code != "AI_PROTOCOL_INCOMPATIBLE"
                         or type(self.diagnostic) is not ProtocolDiagnostic))):
            raise ValueError("Invalid assistant issue.")
        if self.diagnostic is not None:
            self.diagnostic.__post_init__()

    @property
    def message(self) -> str:
        self.__post_init__()
        return _MESSAGES[self.code]

    def to_public(self) -> dict[str, object]:
        value: dict[str, object] = {"code": self.code, "message": self.message}
        if self.diagnostic is not None:
            value["diagnostic"] = self.diagnostic.to_public()
        return value


def issue_for_code(
    code: object,
    diagnostic: ProtocolDiagnostic | None = None,
) -> SafeIssue:
    """Translate an explicit internal code without formatting arbitrary errors."""
    canonical = (_ALIASES.get(code, code) if type(code) is str
                 else "AI_PROTOCOL_INCOMPATIBLE")
    if canonical not in _MESSAGES:
        canonical = "AI_PROTOCOL_INCOMPATIBLE"
    trusted = (diagnostic if canonical == "AI_PROTOCOL_INCOMPATIBLE"
               and type(diagnostic) is ProtocolDiagnostic else None)
    return SafeIssue(cast(AIErrorCode, canonical), trusted)
