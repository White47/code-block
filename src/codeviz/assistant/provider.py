"""Stdlib provider ports; the optional SDK is selected only by the factory."""

import importlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from .config import (
    ProviderConfig,
    ProviderError,
    _fail,
    _parse_url,
    _printable,
    _validate_config,
)

__all__ = [
    "EstimatedInputAccounting", "MemoryKey", "PreparedRequest", "ProviderAdapter",
    "ProviderDraft", "ProviderError", "ProviderEvent", "TransportPolicy",
    "WireOccurrence", "create_provider", "validate_response_status", "validate_transport_target",
]


@dataclass(frozen=True, slots=True, repr=False)
class MemoryKey:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _printable(self.value, 8192)


@dataclass(frozen=True, slots=True)
class ProviderDraft:
    body: bytes = field(repr=False)
    max_output_tokens: int
    deadline: float


@dataclass(frozen=True, slots=True)
class WireOccurrence:
    pointer: str
    start: int
    end: int
    file: str


@dataclass(frozen=True, slots=True)
class InterpretationOccurrence:
    pointer: str
    start: int
    end: int
    origin_id: str


@dataclass(frozen=True, slots=True)
class EstimatedInputAccounting:
    profile_id: str
    method: Literal["wire-bytes-v1"]
    estimated_input_tokens: int
    body_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class PreparedRequest:
    preparation_id: str
    method: Literal["POST"]
    endpoint: str
    body: bytes = field(repr=False)
    max_output_tokens: int
    deadline: float
    input_token_upper_bound: int | None
    source_occurrences: tuple[WireOccurrence, ...] = field(repr=False)
    metadata_occurrences: tuple[WireOccurrence, ...] = field(repr=False)
    attribution_complete: bool
    estimated_input_accounting: EstimatedInputAccounting | None = None
    interpretation_occurrences: tuple[InterpretationOccurrence, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    kind: Literal["text", "tool", "answer", "usage"]
    payload: bytes = field(repr=False)


class ProviderAdapter(Protocol):
    async def prepare(self, draft: ProviderDraft) -> PreparedRequest: ...
    def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]: ...
    async def cancel(self) -> None: ...
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    follow_redirects: bool = False
    trust_env: bool = False
    verify_tls: bool = True
    max_retries: int = 0

    def __post_init__(self) -> None:
        if (self.follow_redirects is not False or self.trust_env is not False
                or self.verify_tls is not True or type(self.max_retries) is not int
                or self.max_retries != 0):
            _fail()


def validate_transport_target(config: ProviderConfig, *, method: str, endpoint: str) -> None:
    _validate_config(config)
    if type(method) is not str or method != "POST":
        _fail()
    origin, path = _parse_url(endpoint)
    if origin != config.origin or origin + path != config.endpoint:
        _fail()


def validate_response_status(status_code: int) -> None:
    if type(status_code) is not int or not 200 <= status_code <= 299:
        _fail("AI_PROTOCOL_INCOMPATIBLE")


def create_provider(config: ProviderConfig, key: MemoryKey | None) -> ProviderAdapter:
    if key is None:
        _fail("AI_NOT_CONFIGURED")
    _validate_config(config)
    if type(key) is not MemoryKey:
        _fail()
    key.__post_init__()
    missing = False
    try:
        sdk = importlib.import_module(".sdk_provider", package=__package__)
        adapter = cast(ProviderAdapter, sdk.create_provider(config, key))
    except ImportError:
        missing = True
    if missing:
        # Raise outside the handler so the original exception cannot retain secrets.
        _fail("AI_DEPENDENCY_MISSING")
    return adapter
