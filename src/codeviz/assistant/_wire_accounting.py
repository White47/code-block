"""Pure final-wire accounting validation without service or transport dependencies."""

import hashlib
import json
import re
from dataclasses import dataclass
from threading import Lock
from typing import Literal, NoReturn

from ._usage_values import InputAccountingSnapshot, ObservedUsage
from .budget import BudgetLedger, BudgetReservation, ExplanationBudgetConfig
from .interpretation_origin import PriorInterpretationOrigin, interpretation_footprint
from .provider import EstimatedInputAccounting, PreparedRequest, WireOccurrence
from .source_policy import validate_source_path

_ESTIMATE_METHOD = "wire-bytes-v1"
_MAX_SAFE_INTEGER = 2**53 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROTOCOL_ERROR: Literal["AI_PROTOCOL_INCOMPATIBLE"] = "AI_PROTOCOL_INCOMPATIBLE"
_BUDGET_ERROR: Literal["AI_BUDGET_EXCEEDED"] = "AI_BUDGET_EXCEEDED"

WireAccountingErrorCode = Literal["AI_BUDGET_EXCEEDED", "AI_PROTOCOL_INCOMPATIBLE"]
BlockedReason = Literal["estimated_limit", "observed_overrun", "usage_unknown"]


class WireAccountingError(ValueError):
    """Fixed accounting refusal without request, source, or dependency details."""

    code: WireAccountingErrorCode = _PROTOCOL_ERROR

    def __init__(self, code: WireAccountingErrorCode = _PROTOCOL_ERROR) -> None:
        self.code = _BUDGET_ERROR if type(code) is str and code == _BUDGET_ERROR else _PROTOCOL_ERROR
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class TrustedEstimatedProfile:
    profile_id: str
    endpoint: str
    model: str
    method: Literal["wire-bytes-v1"] = "wire-bytes-v1"


@dataclass(frozen=True, slots=True)
class WireReservation:
    reservation: BudgetReservation
    files: tuple[str, ...]
    mode: Literal["strict", "estimated"]
    profile_id: str | None
    method: str | None


def _fail(code: WireAccountingErrorCode = _PROTOCOL_ERROR) -> NoReturn:
    raise WireAccountingError(code) from None


def _printable(value: object, maximum: int) -> bool:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.isprintable():
        return False
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return True


def _profiles(
    trusted_profiles: tuple[TrustedEstimatedProfile, ...],
) -> dict[str, TrustedEstimatedProfile]:
    if type(trusted_profiles) is not tuple:
        raise ValueError("invalid profiles")
    profiles: dict[str, TrustedEstimatedProfile] = {}
    for profile in trusted_profiles:
        if (type(profile) is not TrustedEstimatedProfile
                or not _printable(profile.profile_id, 128)
                or not _printable(profile.endpoint, 2048)
                or not _printable(profile.model, 128)
                or type(profile.method) is not str
                or profile.method != _ESTIMATE_METHOD
                or profile.profile_id in profiles):
            raise ValueError("invalid profile")
        profiles[profile.profile_id] = profile
    return profiles


def _mode(
    prepared: PreparedRequest,
    profiles: dict[str, TrustedEstimatedProfile],
) -> tuple[Literal["strict", "estimated"], int, TrustedEstimatedProfile | None]:
    strict = type(prepared.input_token_upper_bound) is int
    estimated = type(prepared.estimated_input_accounting) is EstimatedInputAccounting
    if strict == estimated:
        raise ValueError("invalid accounting mode")
    if strict:
        input_token_upper_bound = prepared.input_token_upper_bound
        if (type(input_token_upper_bound) is not int
                or prepared.estimated_input_accounting is not None):
            raise ValueError("invalid strict accounting")
        return "strict", input_token_upper_bound, None

    accounting = prepared.estimated_input_accounting
    if prepared.input_token_upper_bound is not None or accounting is None:
        raise ValueError("invalid estimated accounting")
    if (not _printable(accounting.profile_id, 128)
            or type(accounting.method) is not str
            or accounting.method != _ESTIMATE_METHOD
            or type(accounting.estimated_input_tokens) is not int
            or accounting.estimated_input_tokens <= 0
            or type(accounting.body_sha256) is not str
            or _SHA256.fullmatch(accounting.body_sha256) is None
            or accounting.estimated_input_tokens != len(prepared.body)
            or accounting.body_sha256 != hashlib.sha256(prepared.body).hexdigest()):
        raise ValueError("invalid estimate")
    profile = profiles.get(accounting.profile_id)
    if (profile is None or profile.method != accounting.method
            or profile.endpoint != prepared.endpoint):
        raise ValueError("untrusted estimate")
    return "estimated", accounting.estimated_input_tokens, profile


def _wire(prepared: PreparedRequest) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    wire = json.loads(
        prepared.body.decode("utf-8", "strict"),
        object_pairs_hook=unique,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if type(wire) is not dict or type(wire.get("messages")) is not list:
        raise ValueError("invalid wire")
    messages = wire["messages"]
    if not 1 <= len(messages) <= 40:
        raise ValueError("invalid messages")
    return wire


def _sources(
    prepared: PreparedRequest,
    wire: dict[str, object],
    *, trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = (),
    _structural_only: bool = False,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    strings: dict[str, str] = {}

    def walk(value: object, pointer: str) -> None:
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise ValueError("invalid key")
                escaped = key.replace("~", "~0").replace("/", "~1")
                strings[pointer + "/" + escaped + "#key"] = key
                walk(child, pointer + "/" + escaped)
        elif type(value) is list:
            for index, child in enumerate(value):
                walk(child, pointer + f"/{index}")
        elif type(value) is str:
            strings[pointer] = value

    walk(wire, "")
    approved: set[tuple[str, int, int]] = set()
    fragments: list[tuple[str, str]] = []
    files: set[str] = set()
    tagged_texts: set[str] = set()
    for group, source in (
        (prepared.source_occurrences, True),
        (prepared.metadata_occurrences, False),
    ):
        for item in group:
            if (type(item) is not WireOccurrence or type(item.pointer) is not str
                    or not item.pointer.startswith("/messages/")
                    or type(item.start) is not int or type(item.end) is not int
                    or type(item.file) is not str):
                raise ValueError("invalid occurrence")
            content = strings.get(item.pointer)
            location = (item.pointer, item.start, item.end)
            if (content is None or not 0 <= item.start < item.end <= len(content)
                    or location in approved):
                raise ValueError("unverified occurrence")
            path = validate_source_path(item.file)
            approved.add(location)
            files.add(path)
            text = content[item.start:item.end]
            tagged_texts.add(text)
            if source:
                fragments.append((path, text))
    _count, derived_files, derived_locations = interpretation_footprint(
        prepared.interpretation_occurrences, strings,
        None if _structural_only else trusted_interpretations, approved)
    files.update(derived_files)
    approved.update(derived_locations)
    tagged_texts.update(strings[pointer][start:end] for pointer, start, end in derived_locations)
    if len(files) > 8 or len({path.casefold() for path in files}) != len(files):
        raise ValueError("file limit")
    for tagged in tagged_texts:
        for pointer, content in strings.items():
            offset = 0
            while (start := content.find(tagged, offset)) >= 0:
                if (pointer, start, start + len(tagged)) not in approved:
                    raise ValueError("unattributed occurrence")
                offset = start + 1
    return tuple(fragments), tuple(sorted(files))


def inspect_wire(
    prepared: PreparedRequest,
    *,
    trusted_profiles: tuple[TrustedEstimatedProfile, ...] = (),
    trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = (),
) -> WireReservation:
    """Validate accounting and source proof against the immutable final request body."""
    try:
        if (type(prepared) is not PreparedRequest or type(prepared.body) is not bytes
                or not 1 <= len(prepared.body) <= 256 * 1024
                or type(prepared.source_occurrences) is not tuple
                or type(prepared.metadata_occurrences) is not tuple
                or prepared.attribution_complete is not True
                or len(prepared.source_occurrences) + len(prepared.metadata_occurrences) > 1000):
            raise ValueError("invalid prepared request")
        profiles = _profiles(trusted_profiles)
        mode, input_tokens, profile = _mode(prepared, profiles)
        wire = _wire(prepared)
        if mode == "strict":
            if input_tokens < len(prepared.body):
                raise ValueError("unproven input bound")
        elif profile is None or wire.get("model") != profile.model:
            raise ValueError("untrusted model")
        fragments, files = _sources(prepared, wire, trusted_interpretations=trusted_interpretations)
        derived_files = tuple(sorted({path for item in prepared.interpretation_occurrences
            for origin in trusted_interpretations if origin.origin_id == item.origin_id
            for path, _hash in origin.manifest.dependency_files}))
        reservation = BudgetReservation(
            model_requests=1,
            input_tokens=input_tokens,
            output_tokens=prepared.max_output_tokens,
            source_fragments=fragments,
            derived_characters=sum(item.end - item.start for item in prepared.interpretation_occurrences),
            derived_files=derived_files,
        )
        return WireReservation(
            reservation,
            files,
            mode,
            None if profile is None else profile.profile_id,
            None if profile is None else profile.method,
        )
    except WireAccountingError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError,
            RecursionError, OverflowError):
        _fail()


def wire_reservation(
    prepared: PreparedRequest,
    *,
    trusted_profiles: tuple[TrustedEstimatedProfile, ...] = (),
) -> tuple[BudgetReservation, tuple[str, ...]]:
    """Return the legacy two-tuple after the shared final-wire inspection."""
    inspected = inspect_wire(prepared, trusted_profiles=trusted_profiles)
    return inspected.reservation, inspected.files


def _validated_usage(usage: ObservedUsage) -> ObservedUsage:
    if type(usage) is not ObservedUsage or type(usage.basis) is not str:
        _fail()
    if usage.basis == "unknown":
        if usage.input_tokens is not None or usage.output_tokens is not None:
            _fail()
    elif usage.basis in ("actual", "estimated"):
        if (type(usage.input_tokens) is not int
                or not 0 <= usage.input_tokens <= _MAX_SAFE_INTEGER
                or type(usage.output_tokens) is not int
                or not 0 <= usage.output_tokens <= _MAX_SAFE_INTEGER):
            _fail()
    else:
        _fail()
    return usage


def _legacy_usage(value: dict[str, object]) -> ObservedUsage:
    if (type(value) is not dict
            or set(value) != {"inputTokens", "outputTokens", "basis"}):
        _fail()
    return _validated_usage(ObservedUsage(
        value["inputTokens"],  # type: ignore[arg-type]
        value["outputTokens"],  # type: ignore[arg-type]
        value["basis"],  # type: ignore[arg-type]
    ))


class WireAccountingPolicy:
    """Per-job admission and observed-usage state for one serialized send at a time."""

    def __init__(
        self,
        ledger: BudgetLedger,
        *,
        input_limit: int = 20_000,
        trusted_profiles: tuple[TrustedEstimatedProfile, ...] = (),
        budget: ExplanationBudgetConfig | None = None,
        trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = (),
    ) -> None:
        if (type(ledger) is not BudgetLedger or type(input_limit) is not int or input_limit <= 0
                or (budget is not None and type(budget) is not ExplanationBudgetConfig)
                or (budget is not None and input_limit != 20_000)):
            _fail()
        try:
            _profiles(trusted_profiles)
        except (AttributeError, TypeError, ValueError, UnicodeError):
            _fail()
        self._ledger = ledger
        self._input_limit = input_limit if budget is None else budget.max_input_tokens
        self._output_limit = 2_000 if budget is None else budget.max_output_tokens
        self._ordinary_output_limit = 512 if budget is None else budget.ordinary_output_tokens
        self._final_output_limit = 2_000 if budget is None else budget.final_output_tokens
        self._repair_output_limit = 2_000 if budget is None else budget.repair_output_tokens
        self._exact_phase_allocations = budget is not None
        self._ordinary_round_limit = None if budget is None else budget.ordinary_rounds
        self._ordinary_admissions = 0
        self._trusted_profiles = trusted_profiles
        self._trusted_interpretations = trusted_interpretations
        self._lock = Lock()
        self._files: tuple[str, ...] = ()
        self._final_seen = False
        self._repair_seen = False
        self._prepared_ids: set[str] = set()
        self._finalized_ids: set[str] = set()
        self._accounting_mode: Literal["strict", "estimated"] | None = None
        self._profile_id: str | None = None
        self._accounting_method: Literal["wire-bytes-v1"] | None = None
        self._reserved_estimate = 0
        self._known_actual_input_tokens = 0
        self._known_actual_output_tokens = 0
        self._actual_observations = 0
        self._actual_complete = True
        self._observed = ObservedUsage()
        self._blocked_reason: BlockedReason | None = None
        self._protocol_blocked = False
        self._pending_admission: object | None = None
        self._active_id: str | None = None
        self._active_reservation: BudgetReservation | None = None
        self._active_dispatched = False
        self._active_observed = False
        self._active_has_actual = False

    @property
    def disclosed_files(self) -> tuple[str, ...]:
        with self._lock:
            return self._files

    @property
    def observed(self) -> ObservedUsage:
        with self._lock:
            return self._observed

    @property
    def snapshot(self) -> InputAccountingSnapshot | None:
        with self._lock:
            if self._accounting_mode != "estimated":
                return None
            method = self._accounting_method
            if method != _ESTIMATE_METHOD:
                _fail()
            return InputAccountingSnapshot(
                method,
                self._input_limit,
                self._known_actual_input_tokens,
                self._blocked_reason,
            )

    def _reserve(self, reservation: BudgetReservation) -> None:
        self._ledger.reserve(reservation)

    def _block(self, reason: BlockedReason) -> NoReturn:
        if self._blocked_reason is None:
            self._blocked_reason = reason
        _fail(_BUDGET_ERROR)

    def _ensure_open_locked(self) -> None:
        if self._protocol_blocked:
            _fail()
        if self._blocked_reason is not None:
            _fail(_BUDGET_ERROR)
        if self._pending_admission is not None or self._active_id is not None:
            _fail()

    def ensure_can_prepare(self) -> None:
        self._reserve(BudgetReservation())
        with self._lock:
            self._ensure_open_locked()

    def admit(
        self,
        prepared: PreparedRequest,
        phase: Literal["ordinary", "final", "repair"],
    ) -> tuple[BudgetReservation, tuple[str, ...]]:
        self._reserve(BudgetReservation())
        if type(phase) is not str or phase not in ("ordinary", "final", "repair"):
            _fail()
        if (type(prepared) is not PreparedRequest
                or type(prepared.max_output_tokens) is not int
                or prepared.max_output_tokens < 1):
            _fail(_BUDGET_ERROR)
        if type(prepared.preparation_id) is not str or not prepared.preparation_id:
            _fail()
        inspected = inspect_wire(prepared, trusted_profiles=self._trusted_profiles,
                                 trusted_interpretations=self._trusted_interpretations)
        reservation, files = inspected.reservation, inspected.files
        accounting_method: Literal["wire-bytes-v1"] | None = None
        if inspected.mode == "estimated":
            if inspected.method != _ESTIMATE_METHOD:
                _fail()
            accounting_method = _ESTIMATE_METHOD
        ticket = object()

        with self._lock:
            self._ensure_open_locked()
            if ((self._final_seen and phase != "repair")
                    or (phase == "repair" and (not self._final_seen or self._repair_seen))):
                _fail()
            if (phase == "ordinary" and self._ordinary_round_limit is not None
                    and self._ordinary_admissions >= self._ordinary_round_limit):
                _fail(_BUDGET_ERROR)
            remaining = self._output_limit - self._ledger.usage.output_tokens
            phase_limit = (self._ordinary_output_limit if phase == "ordinary" else
                           self._final_output_limit if phase == "final" else
                           self._repair_output_limit)
            if self._exact_phase_allocations:
                if remaining < phase_limit or prepared.max_output_tokens != phase_limit:
                    _fail(_BUDGET_ERROR)
            elif prepared.max_output_tokens > min(phase_limit, remaining):
                _fail(_BUDGET_ERROR)
            if prepared.preparation_id in self._prepared_ids:
                _fail()
            identity = (inspected.mode, inspected.profile_id, inspected.method)
            if (self._accounting_mode is not None
                    and identity != (self._accounting_mode, self._profile_id,
                                     self._accounting_method)):
                _fail()
            distinct = tuple(sorted(
                set(self._files) | set(self._ledger.usage.files) | set(files)
            ))
            if len(distinct) > 8 or len({path.casefold() for path in distinct}) != len(distinct):
                _fail(_BUDGET_ERROR)
            if inspected.mode == "estimated":
                if not self._actual_complete:
                    self._block("usage_unknown")
                estimate = reservation.input_tokens
                base = max(self._reserved_estimate, self._known_actual_input_tokens)
                if base + estimate > self._input_limit:
                    self._block("estimated_limit")
            self._pending_admission = ticket

        try:
            self._reserve(reservation)
        except Exception:
            with self._lock:
                if self._pending_admission is ticket:
                    self._pending_admission = None
            raise

        with self._lock:
            if self._pending_admission is not ticket:
                _fail()
            self._pending_admission = None
            self._files = distinct
            self._prepared_ids.add(prepared.preparation_id)
            if self._accounting_mode is None:
                self._accounting_mode = inspected.mode
                self._profile_id = inspected.profile_id
                self._accounting_method = accounting_method
            if inspected.mode == "estimated":
                self._reserved_estimate += reservation.input_tokens
            if phase == "final":
                self._final_seen = True
            elif phase == "repair":
                self._repair_seen = True
            else:
                self._ordinary_admissions += 1
            self._active_id = prepared.preparation_id
            self._active_reservation = reservation
            self._active_dispatched = False
            self._active_observed = False
            self._active_has_actual = False
            return reservation, files

    def dispatch(self, preparation_id: str) -> None:
        with self._lock:
            if (type(preparation_id) is not str or self._active_id != preparation_id
                    or self._active_dispatched):
                _fail()
            self._active_dispatched = True

    def _refresh_observed_locked(self) -> None:
        if self._actual_complete and self._actual_observations:
            self._observed = ObservedUsage(
                self._known_actual_input_tokens,
                self._known_actual_output_tokens,
                "actual",
            )
        else:
            self._observed = ObservedUsage()

    def _record_usage_locked(
        self,
        usage: ObservedUsage,
        reservation: BudgetReservation,
    ) -> bool:
        violation = False
        if usage.basis == "actual":
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            if type(input_tokens) is not int or type(output_tokens) is not int:
                _fail()
            self._known_actual_input_tokens += input_tokens
            self._known_actual_output_tokens += output_tokens
            self._actual_observations += 1
            self._active_has_actual = True
            if (self._accounting_mode == "estimated"
                    and self._known_actual_input_tokens > self._input_limit
                    and self._blocked_reason is None):
                self._blocked_reason = "observed_overrun"
        else:
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            self._actual_complete = False
            if self._accounting_mode == "estimated" and self._blocked_reason is None:
                self._blocked_reason = "usage_unknown"
        self._refresh_observed_locked()
        if usage.basis != "unknown":
            if type(output_tokens) is not int or output_tokens > reservation.output_tokens:
                violation = True
            if (self._accounting_mode == "strict"
                    and (type(input_tokens) is not int
                         or input_tokens > reservation.input_tokens)):
                violation = True
        return violation

    def observe(self, preparation_id: str, usage: ObservedUsage) -> None:
        validated = _validated_usage(usage)
        with self._lock:
            reservation = self._active_reservation
            if (type(preparation_id) is not str or self._active_id != preparation_id
                    or reservation is None or not self._active_dispatched
                    or self._active_observed):
                _fail()
            self._active_observed = True
            if self._record_usage_locked(validated, reservation):
                self._protocol_blocked = True
                _fail()

    def _finish_active_locked(self) -> None:
        active_id = self._active_id
        if active_id is None:
            _fail()
        self._finalized_ids.add(active_id)
        self._active_id = None
        self._active_reservation = None
        self._active_dispatched = False
        self._active_observed = False
        self._active_has_actual = False

    def finalize(self, preparation_id: str) -> None:
        with self._lock:
            if type(preparation_id) is not str:
                _fail()
            if self._active_id is None:
                if preparation_id in self._finalized_ids:
                    return
                _fail()
            if self._active_id != preparation_id:
                if preparation_id in self._finalized_ids:
                    return
                _fail()
            if self._active_dispatched and not self._active_has_actual:
                self._actual_complete = False
                self._refresh_observed_locked()
                if self._accounting_mode == "estimated" and self._blocked_reason is None:
                    self._blocked_reason = "usage_unknown"
            self._finish_active_locked()

    def report_usage(
        self,
        reservation: BudgetReservation,
        usage: dict[str, object],
    ) -> None:
        validated = _legacy_usage(usage)
        with self._lock:
            if (self._accounting_mode != "strict"
                    or self._active_reservation is not reservation
                    or self._active_dispatched):
                _fail()
            violation = self._record_usage_locked(validated, reservation)
            self._finish_active_locked()
            if violation:
                self._protocol_blocked = True
                _fail()
