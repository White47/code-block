"""Fixed source-free C2 probes; no caller text or project identity is accepted."""

import json
import math
from dataclasses import dataclass
from typing import Literal

from .config import ProviderConfig, _validate_config
from .provider import ProviderDraft

ConnectionStage = Literal["tools", "final"]
_STRICT_OUTPUT_LIMITS = (32, 96)
_DEEPSEEK_OUTPUT_LIMITS = (96, 32)


def _probe_output_limit(stage: ConnectionStage, *, deepseek: bool) -> int:
    """Callers select only after validating their application-owned profile."""
    if stage not in ("tools", "final") or type(deepseek) is not bool:
        raise ValueError("Invalid connection allocation.")
    limits = _DEEPSEEK_OUTPUT_LIMITS if deepseek else _STRICT_OUTPUT_LIMITS
    return limits[0 if stage == "tools" else 1]


@dataclass(frozen=True, slots=True)
class TrustedConnectionProfile:
    profile_id: str
    endpoint: str
    model: str
    protocol: str

    def __post_init__(self) -> None:
        from ._deepseek_admission import (
            _CONNECTION_PROTOCOL,
            _ENDPOINT,
            _MODEL,
            _PROFILE_ID,
        )

        fields = (self.profile_id, self.endpoint, self.model, self.protocol)
        if (any(type(value) is not str for value in fields)
                or fields != (_PROFILE_ID, _ENDPOINT, _MODEL, _CONNECTION_PROTOCOL)):
            raise ValueError("Invalid connection profile.")


def _copy_connection_profile(profile: object) -> TrustedConnectionProfile:
    if type(profile) is not TrustedConnectionProfile:
        raise ValueError("Invalid connection profile.")
    profile.__post_init__()
    return TrustedConnectionProfile(profile.profile_id, profile.endpoint, profile.model, profile.protocol)


def _copy_connection_profiles(
    profiles: tuple[TrustedConnectionProfile, ...],
) -> tuple[TrustedConnectionProfile, ...]:
    if type(profiles) is not tuple:
        raise ValueError("Invalid connection profiles.")
    copied = tuple(_copy_connection_profile(profile) for profile in profiles)
    if len({profile.profile_id for profile in copied}) != len(copied):
        raise ValueError("Invalid connection profiles.")
    return copied


def _select_connection_profile(
    config: ProviderConfig, profiles: tuple[TrustedConnectionProfile, ...],
) -> TrustedConnectionProfile | None:
    _validate_config(config)
    matches = tuple(profile for profile in _copy_connection_profiles(profiles)
                    if profile.endpoint == config.endpoint and profile.model == config.model)
    return matches[0] if matches else None


def connection_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _probe_json_instructions() -> str:
    from ._deepseek_admission import _PROBE_JSON_TEMPLATE

    return _PROBE_JSON_TEMPLATE.format(schema=json.dumps(connection_schema()))


def _logical(stage: ConnectionStage) -> dict[str, object]:
    if stage not in ("tools", "final"):
        raise ValueError("Invalid connection stage.")
    return {"version": 1, "mode": stage,
            "messages": [{"role": "user", "content": "Call p." if stage == "tools" else "Return {}."}],
            "tools": [{"name": "p", "description": "", "parameters": connection_schema()}]
            if stage == "tools" else [],
            "answerSchema": connection_schema() if stage == "final" else None,
            "sourceTags": []}


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def connection_stage(value: object) -> ConnectionStage | None:
    """Recognize the entire immutable profile, not just its output-schema slot."""
    if type(value) is not dict:
        return None
    try:
        encoded = _encode(value)
        for stage in ("tools", "final"):
            if encoded == _encode(_logical(stage)):
                return stage
    except (TypeError, ValueError, UnicodeError, RecursionError):
        pass
    return None


def connection_draft(stage: ConnectionStage, *, deadline: float,
                     connection_profile: TrustedConnectionProfile | None = None) -> ProviderDraft:
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("Invalid connection deadline.")
    if connection_profile is not None:
        _copy_connection_profile(connection_profile)
    limit = _probe_output_limit(stage, deepseek=connection_profile is not None)
    return ProviderDraft(_encode(_logical(stage)), limit, deadline)
