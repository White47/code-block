"""Dependency-light immutable usage values shared by accounting and projection."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ObservedUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    basis: Literal["actual", "estimated", "unknown"] = "unknown"


@dataclass(frozen=True, slots=True)
class InputAccountingSnapshot:
    method: Literal["wire-bytes-v1"]
    limit_tokens: int
    known_actual_input_tokens: int
    blocked_reason: Literal[
        "estimated_limit", "observed_overrun", "usage_unknown"
    ] | None

    def __post_init__(self) -> None:
        if (type(self.method) is not str or self.method != "wire-bytes-v1"
                or type(self.limit_tokens) is not int
                or self.limit_tokens not in (20_000, 100_000, 1024)
                or type(self.known_actual_input_tokens) is not int
                or not 0 <= self.known_actual_input_tokens <= 2**53 - 1
                or (self.blocked_reason is not None
                    and (type(self.blocked_reason) is not str
                         or self.blocked_reason not in (
                             "estimated_limit", "observed_overrun", "usage_unknown"
                         )))):
            raise ValueError("Invalid input accounting snapshot.")
