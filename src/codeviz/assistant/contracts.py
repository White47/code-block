"""Immutable request contracts and safe validation shared by foundations."""

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from codeviz.core import SourceAnchor

ErrorCode = Literal[
    "invalid_input", "budget_exceeded", "cancelled", "timeout",
    "evidence_unavailable", "registry_closed", "registry_full", "id_unavailable",
]
_MESSAGES: dict[ErrorCode, str] = {
    "invalid_input": "Invalid assistant input.",
    "budget_exceeded": "Assistant budget exceeded.",
    "cancelled": "Assistant request cancelled.",
    "timeout": "Assistant deadline reached.",
    "evidence_unavailable": "Evidence is unavailable.",
    "registry_closed": "Evidence registry is closed.",
    "registry_full": "Evidence registry capacity exceeded.",
    "id_unavailable": "Evidence identifier unavailable.",
}
_SCOPE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class AgentError(ValueError):
    """Expose only fixed messages, never paths, source text or caller input."""

    def __init__(self, code: ErrorCode = "invalid_input") -> None:
        if not isinstance(code, str) or code not in _MESSAGES:
            code = "invalid_input"
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


def _integer(value: object, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise AgentError()


def _finite_number(value: int | float) -> float:
    if type(value) not in (int, float):
        raise AgentError()
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise AgentError() from None
    if not math.isfinite(number):
        raise AgentError()
    return number


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise AgentError() from None


def _relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise AgentError()
    _utf8(value)
    try:
        return SourceAnchor(value, 1).file_path
    except (ValueError, TypeError):
        raise AgentError() from None


@dataclass(frozen=True, slots=True)
class RequestScope:
    project_epoch: str
    analysis_generation: int
    request_id: str

    def __post_init__(self) -> None:
        for value in (self.project_epoch, self.request_id):
            if not isinstance(value, str) or _SCOPE_ID.fullmatch(value) is None:
                raise AgentError()
        _integer(self.analysis_generation)
        if self.analysis_generation > 2**63 - 1:
            raise AgentError()


class JobStatus(StrEnum):
    QUEUED = "Queued"
    RUNNING = "Running"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    FAILED = "Failed"
    STALE = "Stale"
