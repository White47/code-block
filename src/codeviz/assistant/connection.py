"""Pure, source-free connection probe with explicit resource ownership."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, NoReturn, TypeVar, cast

from ._connection_values import ConnectionCapabilities, _copy_connection_capabilities
from ._usage_values import InputAccountingSnapshot, ObservedUsage
from ._wire_accounting import (
    TrustedEstimatedProfile,
    WireAccountingError,
    WireAccountingPolicy,
)
from .budget import BudgetLedger, BudgetLimits, BudgetReservation, BudgetUsage
from .config import ProviderError
from .connection_profile import (
    ConnectionStage,
    TrustedConnectionProfile,
    _copy_connection_profile,
    _probe_json_instructions,
    _probe_output_limit,
    connection_draft,
    connection_schema,
)
from .contracts import AgentError
from .issues import ProtocolBoundary, ProtocolDiagnostic
from .provider import (
    EstimatedInputAccounting,
    PreparedRequest,
    ProviderAdapter,
    ProviderEvent,
)

__all__ = ["ConnectionProbeResult", "ConnectionProtocolError", "_ConnectionResources", "run_connection"]

_BODY_CAP = 256 * 1024
_EVENT_CAP = 8 * 1024
_ANSWER_CAP = 64 * 1024
_T = TypeVar("_T")
_FROZEN_FIELDS = (
    "preparation_id", "method", "endpoint", "body", "max_output_tokens", "deadline",
    "input_token_upper_bound", "source_occurrences", "metadata_occurrences",
    "attribution_complete", "profile_id", "accounting_method",
    "estimated_input_tokens", "body_sha256",
)


class ConnectionProtocolError(ProviderError):
    """Closed runner proof failure with an application-owned boundary."""

    def __init__(self, boundary: ProtocolBoundary) -> None:
        self.diagnostic = ProtocolDiagnostic(boundary)
        super().__init__("AI_PROTOCOL_INCOMPATIBLE")


def _protocol(boundary: ProtocolBoundary = "pre_send") -> NoReturn:
    raise ConnectionProtocolError(boundary) from None


def _accounting(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except WireAccountingError as error:
        if error.code == "AI_BUDGET_EXCEEDED":
            raise AgentError("budget_exceeded") from None
        _protocol()


def _trusted_profiles(
    profiles: tuple[TrustedEstimatedProfile, ...],
) -> tuple[TrustedEstimatedProfile, ...]:
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
        _protocol()


def _finite(value: object) -> float:
    if type(value) not in (int, float):
        raise AgentError()
    number = float(cast(int | float, value))
    if not math.isfinite(number):
        raise AgentError()
    return number


class _SourceFreeLedger:
    """Connection probes have no source allowance, unlike the shared defaults."""

    def __init__(
        self,
        limits: BudgetLimits,
        *,
        clock: Callable[[], float],
    ) -> None:
        self._inner = BudgetLedger(limits, clock=clock)

    @property
    def usage(self) -> BudgetUsage:
        return self._inner.usage

    @property
    def accounting_ledger(self) -> BudgetLedger:
        return self._inner

    def cancel(self) -> None:
        self._inner.cancel()

    def reserve(self, reservation: BudgetReservation) -> BudgetUsage:
        if (type(reservation) is not BudgetReservation
                or reservation.source_fragments):
            raise AgentError("budget_exceeded")
        return self._inner.reserve(reservation)


@dataclass(frozen=True, slots=True)
class ConnectionProbeResult:
    usage: BudgetUsage
    observed: ObservedUsage
    input_accounting: InputAccountingSnapshot | None = None
    connection_capabilities: ConnectionCapabilities | None = None

    def __post_init__(self) -> None:
        if (type(self.usage) is not BudgetUsage or type(self.observed) is not ObservedUsage
                or (self.input_accounting is not None
                    and type(self.input_accounting) is not InputAccountingSnapshot)):
            raise ValueError("Invalid connection probe result.")
        if self.connection_capabilities is not None:
            _copy_connection_capabilities(self.connection_capabilities)


class _ConnectionResources:
    """Own one probe's provider/capture references until confirmed cleanup."""

    def __init__(self, *, admitted_at: float, clock: Callable[[], float], deadline: float) -> None:
        admitted = _finite(admitted_at)
        final_deadline = _finite(deadline)
        if not callable(clock) or final_deadline != admitted + 15.0:
            raise AgentError()
        first_clock = True

        def admission_clock() -> float:
            nonlocal first_clock
            if first_clock:
                first_clock = False
                return admitted
            return clock()

        self._clock = clock
        self._deadline = final_deadline
        self._ledger = _SourceFreeLedger(BudgetLimits(
            max_files=1,
            max_source_characters=1,
            max_tool_calls=1,
            max_model_requests=2,
            max_input_tokens=1024,
            max_output_tokens=128,
            timeout_seconds=15.0,
        ), clock=admission_clock)
        self._provider: ProviderAdapter | None = None
        self._prepared: PreparedRequest | None = None
        self._policy: WireAccountingPolicy | None = None
        self._observed = ObservedUsage()
        self._input_accounting: InputAccountingSnapshot | None = None
        self._resources_released = False
        self._cancel_succeeded = False
        self._cleanup_lock = asyncio.Lock()

    @property
    def ledger(self) -> _SourceFreeLedger:
        return self._ledger

    @property
    def provider(self) -> ProviderAdapter | None:
        return self._provider

    @property
    def prepared(self) -> PreparedRequest | None:
        return self._prepared

    @property
    def observed(self) -> ObservedUsage:
        return self._observed

    @property
    def input_accounting(self) -> InputAccountingSnapshot | None:
        return self._input_accounting

    @property
    def resources_released(self) -> bool:
        return self._resources_released

    def _remaining(self) -> float:
        try:
            remaining = self._deadline - _finite(self._clock())
        except Exception:
            raise AgentError() from None
        if remaining <= 0:
            raise AgentError("timeout")
        return remaining

    def _adopt_provider(self, provider: ProviderAdapter) -> None:
        if (self._resources_released or self._provider is not None
                or any(not callable(getattr(provider, name, None))
                       for name in ("prepare", "dispatch", "cancel", "aclose"))):
            _protocol()
        self._provider = provider

    def _adopt_policy(self, policy: WireAccountingPolicy) -> None:
        if (self._resources_released or self._policy is not None
                or type(policy) is not WireAccountingPolicy):
            _protocol()
        self._policy = policy
        self._sync_policy()

    def _sync_policy(self) -> None:
        policy = self._policy
        if policy is None:
            _protocol()
        self._observed = _accounting(lambda: policy.observed)
        self._input_accounting = _accounting(lambda: policy.snapshot)

    def _hold_prepared(self, prepared: PreparedRequest) -> None:
        if self._resources_released or self._provider is None or self._prepared is not None:
            _protocol()
        self._prepared = prepared

    def _drop_prepared(self, prepared: PreparedRequest) -> None:
        if self._prepared is prepared:
            self._prepared = None

    async def cancel_and_close(self) -> None:
        async with self._cleanup_lock:
            if self._resources_released:
                return
            provider = self._provider
            if provider is None:
                self._prepared = None
                self._resources_released = True
                return
            if not self._cancel_succeeded:
                await provider.cancel()
                self._cancel_succeeded = True
            await provider.aclose()
            self._prepared = None
            self._provider = None
            self._resources_released = True


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    if len(pairs) > 1000:
        raise ValueError()
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _tree(value: object) -> None:
    if type(value) is dict:
        if len(cast(dict[object, object], value)) > 1000:
            raise ValueError()
        for key, child in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise ValueError()
            key.encode("utf-8", "strict")
            _tree(child)
    elif type(value) is list:
        if len(cast(list[object], value)) > 1000:
            raise ValueError()
        for child in cast(list[object], value):
            _tree(child)
    elif type(value) is str:
        value.encode("utf-8", "strict")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError()


def _strict_json(
    raw: bytes,
    cap: int,
    boundary: ProtocolBoundary = "pre_send",
) -> object:
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= cap or raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError()
        text = raw.decode("utf-8", "strict")
        depth = 0
        quoted = False
        escaped = False
        for character in text:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "[{":
                depth += 1
                if depth > 16:
                    raise ValueError()
            elif character in "]}":
                depth -= 1
                if depth < 0:
                    raise ValueError()
        if quoted or depth != 0:
            raise ValueError()
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        _tree(value)
        return value
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        _protocol(boundary)


def _same(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if type(value) is dict:
        actual_dict = cast(dict[object, object], value)
        expected_dict = cast(dict[object, object], expected)
        return (set(actual_dict) == set(expected_dict)
                and all(_same(actual_dict[key], expected_dict[key]) for key in expected_dict))
    if type(value) is list:
        actual_list = cast(list[object], value)
        expected_list = cast(list[object], expected)
        return (len(actual_list) == len(expected_list)
                and all(_same(actual, wanted)
                        for actual, wanted in zip(actual_list, expected_list, strict=True)))
    return value == expected


def _wire_profile(body: bytes, stage: ConnectionStage) -> None:
    wire = _strict_json(body, _BODY_CAP)
    if type(wire) is not dict:
        _protocol()
    data = cast(dict[str, object], wire)
    required = {"model", "messages", "stream", "max_completion_tokens"}
    allowed = required | {
        "parallel_tool_calls", "tool_choice", "stream_options", "tools", "response_format",
    }
    expected_message = [{"role": "user", "content": "Call p." if stage == "tools" else "Return {}."}]
    model = data.get("model")
    if (not required <= set(data) <= allowed
            or type(model) is not str or not 1 <= len(model) <= 128
            or not model.isprintable()
            or not _same(data.get("messages"), expected_message)
            or data.get("stream") is not True
            or type(data.get("max_completion_tokens")) is not int
            or data["max_completion_tokens"] != _probe_output_limit(stage, deepseek=False)
            or ("stream_options" in data
                and not _same(data["stream_options"], {"include_usage": True}))
            or ("parallel_tool_calls" in data and data["parallel_tool_calls"] is not False)):
        _protocol()
    try:
        model.encode("utf-8", "strict")
    except UnicodeError:
        _protocol()
    schema = connection_schema()
    if stage == "tools":
        expected_tools = [{"type": "function", "function": {
            "name": "p", "description": "", "parameters": schema, "strict": True,
        }}]
        if (not _same(data.get("tools"), expected_tools)
                or data.get("tool_choice", "auto") != "auto"
                or "response_format" in data):
            _protocol()
    else:
        expected_format = {"type": "json_schema", "json_schema": {
            "name": "p", "strict": True, "schema": schema,
        }}
        if ("tools" in data or data.get("tool_choice", "none") != "none"
                or not _same(data.get("response_format"), expected_format)):
            _protocol()


def _connection_binding(profile: TrustedConnectionProfile | None) -> tuple[str, ...] | None:
    if profile is None:
        return None
    invalid = False
    try:
        copied = _copy_connection_profile(profile)
    except (AttributeError, TypeError, ValueError):
        invalid = True
    if invalid:
        _protocol()
    return copied.profile_id, copied.endpoint, copied.model, copied.protocol


def _deepseek_wire_profile(body: bytes, stage: ConnectionStage, profile: TrustedConnectionProfile) -> None:
    expected: dict[str, object] = {
        "model": profile.model,
        "messages": [{"role": "user", "content": "Call p." if stage == "tools" else "Return {}."}],
        "stream": True, "max_tokens": _probe_output_limit(stage, deepseek=True),
        "thinking": {"type": "disabled"}, "stream_options": {"include_usage": True},
    }
    if stage == "tools":
        expected["tools"] = [{"type": "function", "function": {
            "name": "p", "description": "", "parameters": connection_schema(),
        }}]
        expected["tool_choice"] = "auto"
    else:
        expected["messages"] = [{"role": "system", "content": _probe_json_instructions()},
                                {"role": "user", "content": "Return {}."}]
        expected["response_format"] = {"type": "json_object"}
    if not _same(_strict_json(body, _BODY_CAP), expected):
        _protocol()


def _frozen(prepared: PreparedRequest) -> tuple[object, ...]:
    values = tuple(getattr(prepared, name) for name in _FROZEN_FIELDS[:-4])
    accounting = prepared.estimated_input_accounting
    if accounting is None:
        return (*values, None, None, None, None)
    return (
        *values,
        accounting.profile_id,
        accounting.method,
        accounting.estimated_input_tokens,
        accounting.body_sha256,
    )


def _validate_prepared(prepared: PreparedRequest, stage: ConnectionStage,
                       deadline: float, *, connection_profile: TrustedConnectionProfile | None = None) -> None:
    _connection_binding(connection_profile)
    output_limit = _probe_output_limit(stage, deepseek=connection_profile is not None)
    if (type(prepared) is not PreparedRequest
            or type(prepared.preparation_id) is not str
            or not 1 <= len(prepared.preparation_id) <= 512
            or not prepared.preparation_id.isprintable()
            or prepared.method != "POST"
            or type(prepared.endpoint) is not str or not prepared.endpoint
            or type(prepared.body) is not bytes
            or not 1 <= len(prepared.body) <= _BODY_CAP
            or prepared.max_output_tokens != output_limit
            or prepared.deadline != deadline
            or prepared.source_occurrences != ()
            or prepared.metadata_occurrences != ()
            or prepared.attribution_complete is not True):
        _protocol()
    strict = (type(prepared.input_token_upper_bound) is int
              and prepared.estimated_input_accounting is None)
    estimated = (prepared.input_token_upper_bound is None
                 and prepared.estimated_input_accounting is not None)
    if (strict == estimated
            or (strict and cast(int, prepared.input_token_upper_bound) < len(prepared.body))):
        _protocol()
    if connection_profile is None:
        _wire_profile(prepared.body, stage)
    else:
        _connection_binding(connection_profile)
        accounting = prepared.estimated_input_accounting
        if (type(prepared.method) is not str or prepared.method != "POST"
                or prepared.endpoint != connection_profile.endpoint
                or type(prepared.max_output_tokens) is not int
                or type(prepared.deadline) not in (int, float)
                or type(prepared.source_occurrences) is not tuple
                or type(prepared.metadata_occurrences) is not tuple
                or prepared.input_token_upper_bound is not None
                or type(accounting) is not EstimatedInputAccounting
                or type(accounting.profile_id) is not str
                or accounting.profile_id != connection_profile.profile_id
                or type(accounting.method) is not str or accounting.method != "wire-bytes-v1"):
            _protocol()
        _deepseek_wire_profile(prepared.body, stage, connection_profile)


def _event_data(event: ProviderEvent, total: int) -> tuple[dict[str, object], int]:
    if type(event) is not ProviderEvent or type(event.payload) is not bytes:
        _protocol("result_validation")
    total += len(event.payload)
    if total > _BODY_CAP:
        _protocol("result_validation")
    value = _strict_json(event.payload, _EVENT_CAP, "result_validation")
    if type(value) is not dict:
        _protocol("result_validation")
    return cast(dict[str, object], value), total


def _usage(data: dict[str, object], _reservation: BudgetReservation) -> ObservedUsage:
    if set(data) != {"inputTokens", "outputTokens", "basis"}:
        _protocol("result_validation")
    basis = data["basis"]
    input_tokens = data["inputTokens"]
    output_tokens = data["outputTokens"]
    if basis == "unknown":
        if input_tokens is not None or output_tokens is not None:
            _protocol("result_validation")
        return ObservedUsage()
    if (basis not in ("actual", "estimated")
            or type(input_tokens) is not int or input_tokens < 0
            or type(output_tokens) is not int or output_tokens < 0):
        _protocol("result_validation")
    return ObservedUsage(input_tokens, output_tokens, basis)


async def _read_tools(
    events: object,
    reservation: BudgetReservation,
    observe: Callable[[ObservedUsage], None],
) -> ObservedUsage:
    if not hasattr(events, "__aiter__"):
        _protocol("result_validation")
    call_seen = False
    usage: ObservedUsage | None = None
    total = 0
    async for event in cast(Any, events):
        data, total = _event_data(event, total)
        if event.kind == "tool":
            if (call_seen or usage is not None or set(data) != {"id", "name", "arguments"}
                    or type(data["id"]) is not str or not 1 <= len(data["id"]) <= 512
                    or not data["id"].isprintable()
                    or data["name"] != "p" or not _same(data["arguments"], {})):
                _protocol("result_validation")
            call_seen = True
        elif event.kind == "usage":
            if not call_seen or usage is not None:
                _protocol("result_validation")
            usage = _usage(data, reservation)
            observe(usage)
        else:
            _protocol("result_validation")
    if not call_seen or usage is None:
        _protocol("result_validation")
    return usage


async def _read_final(
    events: object,
    reservation: BudgetReservation,
    observe: Callable[[ObservedUsage], None],
) -> ObservedUsage:
    if not hasattr(events, "__aiter__"):
        _protocol("result_validation")
    parts: list[str] = []
    answer_bytes = 0
    finished = False
    usage: ObservedUsage | None = None
    total = 0
    async for event in cast(Any, events):
        data, total = _event_data(event, total)
        if event.kind == "answer":
            content = data.get("content")
            if (usage is not None or finished or set(data) != {"index", "last", "content"}
                    or type(data["index"]) is not int or data["index"] != len(parts)
                    or type(data["last"]) is not bool or type(content) is not str):
                _protocol("result_validation")
            try:
                encoded = content.encode("utf-8", "strict")
            except UnicodeError:
                _protocol("result_validation")
            answer_bytes += len(encoded)
            if answer_bytes > _ANSWER_CAP:
                _protocol("result_validation")
            parts.append(content)
            finished = data["last"]
        elif event.kind == "usage":
            if not finished or not parts or usage is not None:
                _protocol("result_validation")
            usage = _usage(data, reservation)
            observe(usage)
        else:
            _protocol("result_validation")
    if not finished or usage is None:
        _protocol("result_validation")
    answer = _strict_json(
        "".join(parts).encode("utf-8"),
        _ANSWER_CAP,
        "result_validation",
    )
    if not _same(answer, {}):
        _protocol("result_validation")
    return usage


async def run_connection(
    resources: _ConnectionResources,
    provider_factory: Callable[[], ProviderAdapter],
    before_dispatch: Callable[[PreparedRequest], Awaitable[None]],
    current_config_guard: Callable[[PreparedRequest], None],
    *,
    trusted_profiles: tuple[TrustedEstimatedProfile, ...] = (),
    connection_profile: TrustedConnectionProfile | None = None,
) -> ConnectionProbeResult:
    """Run exactly one tools probe and one final-schema probe, without retries."""
    if (type(resources) is not _ConnectionResources or not callable(provider_factory)
            or not callable(before_dispatch) or not callable(current_config_guard)):
        raise ValueError("Invalid connection runner inputs.")
    profiles = _trusted_profiles(trusted_profiles)
    frozen_binding = _connection_binding(connection_profile)
    binding = None if connection_profile is None else _copy_connection_profile(connection_profile)

    def check_binding(boundary: ProtocolBoundary = "pre_send") -> None:
        if (_connection_binding(connection_profile) != frozen_binding
                or _connection_binding(binding) != frozen_binding):
            _protocol(boundary)

    timeout = resources._remaining()
    policy = WireAccountingPolicy(
        resources.ledger.accounting_ledger,
        input_limit=1024,
        trusted_profiles=profiles,
    )
    resources._adopt_policy(policy)
    async with asyncio.timeout(timeout):
        try:
            provider = provider_factory()
        except ProviderError:
            raise
        except Exception:
            _protocol("pre_send")
        resources._adopt_provider(provider)
        check_binding()
        for stage in ("tools", "final"):
            _accounting(policy.ensure_can_prepare)
            draft = connection_draft(stage, deadline=resources._deadline, connection_profile=binding)
            prepared = await provider.prepare(draft)
            check_binding()
            _validate_prepared(prepared, stage, resources._deadline, connection_profile=binding)
            resources._hold_prepared(prepared)
            frozen = _frozen(prepared)
            current_config_guard(prepared)
            check_binding()
            if resources.prepared is not prepared or not _same(list(_frozen(prepared)), list(frozen)):
                _protocol()
            admitted = False
            try:
                phase: Literal["ordinary", "final"] = (
                    "ordinary" if stage == "tools" else "final")
                reservation, files = _accounting(
                    partial(policy.admit, prepared, phase))
                admitted = True
                admitted_id = prepared.preparation_id
                if files:
                    _protocol()
                if stage == "tools":
                    resources.ledger.reserve(BudgetReservation(tool_calls=1))
                await before_dispatch(prepared)
                check_binding()
                if resources.prepared is not prepared or not _same(list(_frozen(prepared)), list(frozen)):
                    _protocol()
                current_config_guard(prepared)
                check_binding()
                if resources.prepared is not prepared or not _same(list(_frozen(prepared)), list(frozen)):
                    _protocol()
                _accounting(partial(policy.dispatch, admitted_id))

                def observe_usage(
                    usage: ObservedUsage,
                    *,
                    _id: str = admitted_id,
                ) -> None:
                    try:
                        _accounting(partial(policy.observe, _id, usage))
                    finally:
                        resources._sync_policy()

                events = provider.dispatch(prepared)
                if stage == "tools":
                    await _read_tools(events, reservation, observe_usage)
                else:
                    await _read_final(events, reservation, observe_usage)
                check_binding("result_validation")
            finally:
                try:
                    if admitted:
                        _accounting(partial(policy.finalize, admitted_id))
                finally:
                    try:
                        resources._sync_policy()
                    finally:
                        resources._drop_prepared(prepared)
    return ConnectionProbeResult(
        resources.ledger.usage,
        resources.observed,
        resources.input_accounting,
        ConnectionCapabilities(True, False, True, "local-json-schema") if binding is not None else None,
    )
