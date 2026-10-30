"""Atomic, cumulative reservations; retries charge again and never refund."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from .contracts import AgentError, _finite_number, _integer, _relative_path

_COUNTERS = ("tool_calls", "model_requests", "input_tokens", "output_tokens")


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_files: int = 8
    max_source_characters: int = 12000
    max_tool_calls: int = 8
    max_model_requests: int = 6
    max_input_tokens: int = 20000
    max_output_tokens: int = 2000
    timeout_seconds: float = 90

    def __post_init__(self) -> None:
        for name in ("max_files", "max_source_characters", *(
            "max_" + counter for counter in _COUNTERS
        )):
            _integer(getattr(self, name), minimum=1)
        if _finite_number(self.timeout_seconds) <= 0:
            raise AgentError()


@dataclass(frozen=True, slots=True)
class ExplanationBudgetConfig:
    """One immutable production contract for the explanation pipeline."""

    max_files: int
    max_source_characters: int
    max_tool_calls: int
    max_model_requests: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    ordinary_rounds: int
    ordinary_output_tokens: int
    final_output_tokens: int
    repair_output_tokens: int

    def __post_init__(self) -> None:
        for name in (
            "max_files",
            "max_source_characters",
            "max_tool_calls",
            "max_model_requests",
            "max_input_tokens",
            "max_output_tokens",
            "ordinary_rounds",
            "ordinary_output_tokens",
            "final_output_tokens",
            "repair_output_tokens",
        ):
            _integer(getattr(self, name), minimum=1)
        if (_finite_number(self.timeout_seconds) <= 0
                or self.max_output_tokens > 8_192
                or self.final_output_tokens > 4_096
                or self.repair_output_tokens > 2_000
                or self.ordinary_rounds + 2 > self.max_model_requests
                or self.ordinary_rounds * self.ordinary_output_tokens
                + self.final_output_tokens + self.repair_output_tokens
                > self.max_output_tokens):
            raise AgentError()

    def canonical(self) -> tuple[int, int, int, int, int, int, float, int, int, int, int]:
        """Return every frozen value used by admission and request identity."""
        self.__post_init__()
        return (
            self.max_files,
            self.max_source_characters,
            self.max_tool_calls,
            self.max_model_requests,
            self.max_input_tokens,
            self.max_output_tokens,
            float(self.timeout_seconds),
            self.ordinary_rounds,
            self.ordinary_output_tokens,
            self.final_output_tokens,
            self.repair_output_tokens,
        )

    def limits(self) -> BudgetLimits:
        return BudgetLimits(
            max_files=self.max_files,
            max_source_characters=self.max_source_characters,
            max_tool_calls=self.max_tool_calls,
            max_model_requests=self.max_model_requests,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
        )


EXPLANATION_BUDGET = ExplanationBudgetConfig(
    max_files=8,
    max_source_characters=12_000,
    max_tool_calls=16,
    max_model_requests=10,
    max_input_tokens=100_000,
    max_output_tokens=8_192,
    timeout_seconds=180,
    ordinary_rounds=8,
    ordinary_output_tokens=512,
    final_output_tokens=2_000,
    repair_output_tokens=2_000,
)


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    tool_calls: int = 0
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    source_fragments: tuple[tuple[str, str], ...] = ()
    derived_characters: int = 0
    derived_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in _COUNTERS:
            _integer(getattr(self, name))
        if type(self.source_fragments) is not tuple:
            raise AgentError()
        normalized: list[tuple[str, str]] = []
        for fragment in self.source_fragments:
            if type(fragment) is not tuple or len(fragment) != 2:
                raise AgentError()
            path, text = fragment
            if not isinstance(text, str):
                raise AgentError()
            normalized.append((_relative_path(path), text))
        object.__setattr__(self, "source_fragments", tuple(normalized))
        _integer(self.derived_characters)
        if type(self.derived_files) is not tuple:
            raise AgentError()
        object.__setattr__(self, "derived_files", tuple(_relative_path(path) for path in self.derived_files))


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Conservative reserved amounts, not actual consumption or billing."""

    files: tuple[str, ...] = ()
    source_characters: int = 0
    tool_calls: int = 0
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("source_characters", *_COUNTERS):
            _integer(getattr(self, name))
        if type(self.files) is not tuple:
            raise AgentError()
        object.__setattr__(self, "files", tuple(sorted({
            _relative_path(path) for path in self.files
        })))


class BudgetLedger:
    def __init__(
        self, limits: BudgetLimits | None = None, *, clock: Callable[[], float] = monotonic,
    ) -> None:
        if limits is None:
            limits = BudgetLimits()
        if not isinstance(limits, BudgetLimits) or not callable(clock):
            raise AgentError()
        self._limits = limits
        self._clock = clock
        self._lock = Lock()
        self._usage = BudgetUsage()
        self._cancelled = False
        self._expired = False
        self._deadline = _finite_number(self._now() + limits.timeout_seconds)

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise AgentError() from None
        return _finite_number(value)

    @property
    def usage(self) -> BudgetUsage:
        with self._lock:
            return self._usage

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def reserve(self, reservation: BudgetReservation) -> BudgetUsage:
        with self._lock:
            if not isinstance(reservation, BudgetReservation):
                raise AgentError()
            if self._cancelled:
                raise AgentError("cancelled")
            if self._expired:
                raise AgentError("timeout")
        # Injected clocks may read usage or cancel; never call them under the lock.
        now = self._now()
        with self._lock:
            if self._cancelled:
                raise AgentError("cancelled")
            if self._expired or now >= self._deadline:
                self._expired = True
                raise AgentError("timeout")
            before = self._usage
            files = tuple(sorted(set(before.files) | {
                path for path, _ in reservation.source_fragments
            } | set(reservation.derived_files)))
            source_characters = before.source_characters + sum(
                len(text) for _, text in reservation.source_fragments
            ) + reservation.derived_characters
            counts = {name: getattr(before, name) + getattr(reservation, name)
                      for name in _COUNTERS}
            if (len(files) > self._limits.max_files
                    or source_characters > self._limits.max_source_characters
                    or any(count > getattr(self._limits, "max_" + name)
                           for name, count in counts.items())):
                raise AgentError("budget_exceeded")
            candidate = BudgetUsage(files=files, source_characters=source_characters, **counts)
            self._usage = candidate
            return candidate
