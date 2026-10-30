"""Application-owned offline DeepSeek profile; no runtime registration or SDK import."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._validation_diagnostics import DiagnosticsSink
    from ._wire_accounting import TrustedEstimatedProfile
    from .budget import ExplanationBudgetConfig
    from .config import ProviderConfig
    from .connection_profile import TrustedConnectionProfile
    from .provider import MemoryKey
    from .sdk_provider import SDKProviderAdapter
    from .service import _AssistantRuntime

_PROFILE_ID = "deepseek-flash-sdk-wire-v1"
_ENDPOINT = "https://api.deepseek.com:443/v1/chat/completions"
_MODEL = "deepseek-flash"
_METHOD = "POST"
_CONNECTION_PROTOCOL = "deepseek-json-object-v1"
_JSON_TEMPLATE = (
    "Return only a JSON object matching the following local answer schema. "
    "Do not include Markdown fences.\nExample: {{\"sections\":[]}}\nJSON schema:\n{schema}"
)
_PROBE_JSON_TEMPLATE = (
    "Return only a JSON object matching the following local answer schema. "
    "Do not include Markdown fences.\nExample: {{}}\nJSON schema:\n{schema}"
)


def _deepseek_explanation_budget() -> ExplanationBudgetConfig:
    """Return the closed application-owned DeepSeek explanation contract."""
    from .budget import ExplanationBudgetConfig

    return ExplanationBudgetConfig(
        max_files=8,
        max_source_characters=12_000,
        max_tool_calls=16,
        max_model_requests=10,
        max_input_tokens=100_000,
        max_output_tokens=8_192,
        timeout_seconds=180,
        ordinary_rounds=4,
        ordinary_output_tokens=512,
        final_output_tokens=4_096,
        repair_output_tokens=2_000,
    )


def _trusted_input_profiles() -> tuple[TrustedEstimatedProfile, ...]:
    """Return a fresh application-owned accounting allow-list."""
    from ._wire_accounting import TrustedEstimatedProfile

    return (TrustedEstimatedProfile(_PROFILE_ID, _ENDPOINT, _MODEL),)


def _trusted_connection_profiles() -> tuple[TrustedConnectionProfile, ...]:
    """Return a fresh application-owned output-protocol allow-list."""
    from .connection_profile import TrustedConnectionProfile

    return (TrustedConnectionProfile(
        _PROFILE_ID, _ENDPOINT, _MODEL, _CONNECTION_PROTOCOL,
    ),)


def _production_provider_factory(
    config: ProviderConfig,
    key: MemoryKey | None,
) -> SDKProviderAdapter:
    """Create only the closed DeepSeek adapter, loading optional SDK code last."""
    from .sdk_provider import (
        _create_deepseek_provider as create,
    )
    from .sdk_provider import (
        _load_sdk_modules,
        _new_secure_transport,
    )

    loaded_modules: object | None = None

    def load() -> object:
        nonlocal loaded_modules
        loaded_modules = _load_sdk_modules()
        return loaded_modules

    def transport() -> object:
        if loaded_modules is None:
            from .config import ProviderError

            raise ProviderError("AI_PROTOCOL_INCOMPATIBLE")
        return _new_secure_transport(loaded_modules)

    return create(config, key, sdk_loader=load, transport_factory=transport)


def _create_application_runtime(
    *, _validation_diagnostics: DiagnosticsSink | None = None,
) -> _AssistantRuntime:
    """Construct the trusted application runtime without importing its SDK."""
    from .service import _AssistantRuntime

    return _AssistantRuntime(
        provider_factory=_production_provider_factory,
        trusted_input_profiles=_trusted_input_profiles(),
        trusted_connection_profiles=_trusted_connection_profiles(),
        explanation_budget=_deepseek_explanation_budget(),
        _validation_diagnostics=_validation_diagnostics,
    )


def _create_deepseek_provider(
    config: ProviderConfig,
    key: MemoryKey | None,
    *,
    sdk_loader: Callable[[], object],
    transport_factory: Callable[[], object],
    clock: Callable[[], float] = time.monotonic,
) -> SDKProviderAdapter:
    from .sdk_provider import _create_deepseek_provider as create

    return create(config, key, sdk_loader=sdk_loader,
                  transport_factory=transport_factory, clock=clock)
