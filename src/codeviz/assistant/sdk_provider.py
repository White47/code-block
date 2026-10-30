"""Optional low-level SDK adapter; dispatch is a trusted service operation.

Preparation suspends the original SDK request on a response future. Only the
dispatch consumer enters the owned transport, without scheduling the SDK to send.
This module imports no optional dependency and owns no consent or evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import ssl
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from ._delta_reasons import DeltaRejection, validated_rejection
from ._wire_accounting import _sources as _inspect_wire_sources
from .config import ProviderConfig, ProviderError, _validate_config
from .connection_profile import (
    _probe_json_instructions,
    _probe_output_limit,
    connection_schema,
    connection_stage,
)
from .interpretation_origin import (
    interpretation_footprint,
    interpretation_tags,
    strings_in,
)
from .issues import (
    ProtocolBoundary,
    ProtocolCheckSite,
    ProtocolDiagnostic,
    ProtocolFinishReason,
)
from .provider import (
    EstimatedInputAccounting,
    InterpretationOccurrence,
    MemoryKey,
    PreparedRequest,
    ProviderDraft,
    ProviderEvent,
    WireOccurrence,
    validate_transport_target,
)

__all__ = ["SDKAdapterError", "SDKProviderAdapter", "create_provider"]

_CAP = 262144
_DEEPSEEK_SSE_RAW_CAP = 1048576
_DEEPSEEK_SSE_FRAME_COUNT = 4096
_DEEPSEEK_FINAL_SSE_RAW_CAP = 2097152
_DEEPSEEK_FINAL_SSE_FRAME_COUNT = 8192
_DEEPSEEK_TOOL_HISTORY_LIMIT = 16
_NAMES = {"search_symbols", "read_source_excerpt", "get_local_relations"}
_MODULES = {
    "httpx2": "httpx2", "openai": "openai", "models": "pydantic_ai.models",
    "messages": "pydantic_ai.messages", "sdk": "pydantic_ai.models.openai",
    "providers": "pydantic_ai.providers.openai", "profiles": "pydantic_ai.profiles.openai",
    "output": "pydantic_ai.output", "tools": "pydantic_ai.tools",
}
_ERRORS = {
    "AI_INVALID_INPUT": "Invalid assistant input.",
    "AI_PROTOCOL_INCOMPATIBLE": "Assistant protocol is incompatible.",
    "AI_AUTH_FAILED": "Assistant authentication failed.",
    "AI_RATE_LIMITED": "Assistant rate limit exceeded.",
    "AI_NETWORK_TIMEOUT": "Assistant network request timed out.",
    "AI_BUDGET_EXCEEDED": "Assistant budget exceeded.",
}


class SDKAdapterError(ValueError):
    """Fixed public error, with no retained original exception or SDK value."""

    def __init__(
        self,
        code: str = "AI_INVALID_INPUT",
        diagnostic: ProtocolDiagnostic | None = None,
        *, rejection: DeltaRejection | None = None,
    ) -> None:
        self.code = code if code in _ERRORS else "AI_INVALID_INPUT"
        if (diagnostic is not None
                and (self.code != "AI_PROTOCOL_INCOMPATIBLE"
                     or type(diagnostic) is not ProtocolDiagnostic)):
            raise ValueError("Invalid SDK diagnostic.")
        self.diagnostic = diagnostic
        if rejection is not None and validated_rejection(rejection) is None:
            raise ValueError("Invalid private rejection.")
        self._delta_rejection = rejection
        super().__init__(_ERRORS[self.code])


def _reject(
    code: str = "AI_INVALID_INPUT",
    diagnostic: ProtocolDiagnostic | None = None,
    *, rejection: DeltaRejection | None = None,
) -> NoReturn:
    raise SDKAdapterError(code, diagnostic, rejection=rejection)


def _private_rejection(error: Exception | None) -> DeltaRejection | None:
    return (validated_rejection(vars(error).get("_delta_rejection"))
            if type(error) is SDKAdapterError else None)


def _carry_rejection(error: SDKAdapterError, value: DeltaRejection | None) -> SDKAdapterError:
    first = validated_rejection(value) or _private_rejection(error)
    return SDKAdapterError(error.code, error.diagnostic, rejection=first)


def _check(condition: bool, code: str = "AI_INVALID_INPUT") -> None:
    if not condition:
        _reject(code)


def _reject_finish(finish: object) -> NoReturn:
    reason = (cast(ProtocolFinishReason, finish) if type(finish) is str and finish in
              ("length", "content_filter", "insufficient_system_resource", "aborted") else None)
    _reject("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic(
        "dispatch", check_site="terminal_finish", finish_reason=reason))


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _string(value: Any, maximum: int = _CAP, *, empty: bool = False,
            printable: bool = False, code: str = "AI_INVALID_INPUT") -> str:
    _check(type(value) is str and (empty or bool(value)) and len(value) <= maximum, code)
    _check(not printable or value.isprintable(), code)
    value.encode("utf-8", "strict")
    return str(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    _check(len(pairs) <= 1000)
    result: dict[str, Any] = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _tree(value: Any) -> None:
    if type(value) in (dict, list):
        _check(len(value) <= 1000)
        if type(value) is dict:
            for key in value:
                key.encode("utf-8", "strict")
        for item in value.values() if type(value) is dict else value:
            _tree(item)
    elif type(value) is str:
        value.encode("utf-8", "strict")
    elif type(value) is float:
        _check(math.isfinite(value))


def _parse(raw: bytes, cap: int = _CAP) -> Any:
    _check(type(raw) is bytes and 1 <= len(raw) <= cap and not raw.startswith(b"\xef\xbb\xbf"))
    text = raw.decode("utf-8", "strict")
    depth, quoted, escaped = 0, False, False
    members: list[int] = []
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _check(depth <= 16)
            members.append(1)
        elif char == "," and members:
            members[-1] += 1
            _check(members[-1] <= 1000)
        elif char in "]}":
            depth -= 1
            _check(depth >= 0)
            members.pop()
    value = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: _reject())
    _tree(value)
    return value


def _closed(value: Any, keys: set[str]) -> None:
    _check(type(value) is dict and set(value) == keys)


def _remote_json(raw: bytes, cap: int = _CAP) -> Any:
    """Remote malformed JSON is a protocol failure, never caller input."""
    invalid = False
    try:
        value = _parse(raw, cap)
    except ValueError:
        invalid = True
    if invalid:
        _reject("AI_PROTOCOL_INCOMPATIBLE")
    return value


def _equal(left: Any, right: Any) -> bool:
    """Structural equality without JSON bool/int coercion."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(_equal(value, right[key]) for key, value in left.items())
    if type(left) in (list, tuple):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    return bool(left == right)


def _schema() -> dict[str, Any]:
    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    claim = obj({"text": {"type": "string"},
                 "basis": {"type": "string", "enum": ["static", "inference", "unknown"]},
                 "evidenceIds": {"type": "array", "items": {"type": "string"}},
                 "relationEvidenceId": {"type": ["string", "null"]}})
    section = obj({"kind": {"type": "string", "enum": ["summary", "entry", "steps", "branches",
                                                        "relations", "evidence", "unknowns"]},
                   "claims": {"type": "array", "items": claim}})
    return obj({"sections": {"type": "array", "items": section}})


def _local_schema(value: Any) -> None:
    if type(value) is dict:
        _check(not any(key in value for key in ("$ref", "$dynamicRef", "$id", "$schema")))
        for child in value.values():
            _local_schema(child)
    elif type(value) is list:
        for child in value:
            _local_schema(child)


def _draft(draft: ProviderDraft, now: float, *,
           _wire_profile: _SDKWireProfile | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if _wire_profile is not None:
        _check(type(_wire_profile) is _SDKWireProfile)
        _wire_profile.__post_init__()
    _check(type(draft) is ProviderDraft)
    _check(type(draft.max_output_tokens) is int and draft.max_output_tokens >= 1)
    _check(type(draft.deadline) in (int, float) and math.isfinite(draft.deadline)
           and draft.deadline > now)
    data = _parse(draft.body)
    _check(type(data) is dict and
           {"version", "mode", "messages", "tools", "answerSchema", "sourceTags"}
           <= set(data) <= {"version", "mode", "messages", "tools", "answerSchema",
                            "sourceTags", "metadataTags", "interpretationTags"})
    _check(type(data["version"]) is int and data["version"] == 1)
    _check(type(data["mode"]) is str and data["mode"] in ("text", "tools", "final"))
    connection = connection_stage(data)
    history_limit = (_DEEPSEEK_TOOL_HISTORY_LIMIT
                     if _wire_profile is not None and connection is None else 8)
    output_limit = (4_096 if _wire_profile is not None and connection is None
                    and data["mode"] == "final" else 2_000)
    _check(draft.max_output_tokens <= output_limit)
    if connection is not None:
        _check(draft.max_output_tokens == _probe_output_limit(
            connection, deepseek=_wire_profile is not None))
    allowed_names = ("p",) if connection == "tools" else _NAMES
    messages = data["messages"]
    _check(type(messages) is list and 1 <= len(messages) <= 40)
    outstanding: dict[str, str] = {}
    seen: set[str] = set()
    wire: list[dict[str, Any]] = []
    slots: dict[str, str] = {}
    for index, message in enumerate(messages):
        _check(type(message) is dict)
        role = message.get("role")
        _check(type(role) is str)
        pointer = f"/messages/{index}"
        if role in ("system", "user"):
            _closed(message, {"role", "content"})
            _check(role != "system" or index == 0)
            content = _string(message["content"])
            wire.append({"role": role, "content": content})
            slots[pointer + "/content"] = content
        elif role == "assistant":
            _closed(message, {"role", "content", "toolCalls"})
            content = message["content"]
            if content is not None:
                slots[pointer + "/content"] = _string(content, empty=True)
            calls = message["toolCalls"]
            _check(type(calls) is list and len(calls) <= 8 and (content is not None or bool(calls)))
            entry: dict[str, Any] = {"role": role, "content": content}
            wire_calls = []
            for number, call in enumerate(calls):
                _closed(call, {"id", "name", "arguments"})
                call_id = _string(call["id"], 512, printable=True)
                _check(call_id not in seen and len(seen) < history_limit)
                name = _string(call["name"], 128)
                _check(name in allowed_names)
                arguments = _string(call["arguments"])
                _check(type(_parse(arguments.encode(), 8192)) is dict)
                seen.add(call_id)
                outstanding[call_id] = name
                slots[f"{pointer}/toolCalls/{number}/arguments"] = arguments
                wire_calls.append({"id": call_id, "type": "function",
                                   "function": {"name": name, "arguments": arguments}})
            if wire_calls:
                entry["tool_calls"] = wire_calls
            wire.append(entry)
        elif role == "tool":
            _closed(message, {"role", "toolCallId", "name", "content"})
            call_id = _string(message["toolCallId"], 512, printable=True)
            name = _string(message["name"], 128)
            _check(call_id in outstanding and outstanding[call_id] == name)
            del outstanding[call_id]
            content = _string(message["content"], empty=True)
            slots[pointer + "/content"] = content
            wire.append({"role": role, "tool_call_id": call_id, "content": content})
        else:
            _reject()
    _check(not outstanding)
    definitions = data["tools"]
    _check(type(definitions) is list and len(definitions) <= 3)
    names: set[str] = set()
    for definition in definitions:
        _closed(definition, {"name", "description", "parameters"})
        name = _string(definition["name"], 128)
        _check(name in allowed_names and name not in names)
        names.add(name)
        _string(definition["description"], 2000, empty=True)
        parameters = definition["parameters"]
        _check(type(parameters) is dict and parameters.get("type") == "object")
        _local_schema(parameters)
    mode = data["mode"]
    _check(bool(definitions) if mode == "tools" else not definitions)
    expected_schema = connection_schema() if connection == "final" else _schema()
    _check(_equal(data["answerSchema"], expected_schema) if mode == "final" else data["answerSchema"] is None)
    tags = data["sourceTags"]
    metadata_tags = data.get("metadataTags", [])
    _check(type(tags) is list and type(metadata_tags) is list
           and len(tags) + len(metadata_tags) <= 1000)
    files: set[str] = set()
    for group in (tags, metadata_tags):
        previous: tuple[str, int, int, str] | None = None
        for tag in group:
            _closed(tag, {"pointer", "start", "end", "file"})
            pointer = _string(tag["pointer"])
            _check(pointer in slots)
            start, end = tag["start"], tag["end"]
            _check(type(start) is int and type(end) is int
                   and 0 <= start < end <= len(slots[pointer]))
            file = _string(tag["file"], 2048, printable=True)
            _check(not file.startswith("/") and "\\" not in file and ":" not in file
                   and all(part not in ("", ".", "..") for part in file.split("/")))
            order = (pointer, start, end, file)
            _check(previous is None or order > previous)
            _check(previous is None or pointer != previous[0] or start >= previous[2])
            previous = order
            files.add(file)
    _check(len(files) <= 8)
    try:
        occupied = {(tag["pointer"], tag["start"], tag["end"]) for group in (tags, metadata_tags) for tag in group}
        interpretation_footprint(interpretation_tags(data), strings_in(data["messages"], "/messages"),
                                 None, occupied)
    except (ValueError, TypeError, AttributeError):
        _check(False)
    return data, wire


def _resource_limits(
    wire_profile: _SDKWireProfile | None,
    data: dict[str, Any],
    draft: ProviderDraft,
) -> tuple[int, int]:
    """Freeze response bounds selected only by trusted profile and logical mode."""
    if wire_profile is None:
        return (_CAP, 0)
    if (connection_stage(data) is None and data["mode"] == "final"
            and draft.max_output_tokens == 4_096):
        return (_DEEPSEEK_FINAL_SSE_RAW_CAP, _DEEPSEEK_FINAL_SSE_FRAME_COUNT)
    return (_DEEPSEEK_SSE_RAW_CAP, _DEEPSEEK_SSE_FRAME_COUNT)


def _error(exc: Exception, boundary: ProtocolBoundary,
           check_site: ProtocolCheckSite | None = None) -> SDKAdapterError:
    diagnostic = ProtocolDiagnostic(boundary, check_site=check_site if boundary == "dispatch" else None)
    if type(exc) is SDKAdapterError:
        if exc.code == "AI_PROTOCOL_INCOMPATIBLE" and exc.diagnostic is None:
            return SDKAdapterError(exc.code, diagnostic, rejection=_private_rejection(exc))
        return exc
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return SDKAdapterError("AI_NETWORK_TIMEOUT")
    return SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", diagnostic)


def _factory_validate(config: ProviderConfig, key: MemoryKey | None) -> None:
    code: str | None = None
    try:
        if key is None:
            code = "AI_NOT_CONFIGURED"
        else:
            _validate_config(config)
            _check(type(key) is MemoryKey)
            key.__post_init__()
    except (ValueError, TypeError, AttributeError):
        code = "AI_INVALID_INPUT"
    if code:
        raise ProviderError(code)


@dataclass(frozen=True, slots=True)
class _SDKWireProfile:
    """Closed application selection; never a provider or draft claim."""

    profile_id: str
    endpoint: str
    model: str
    method: str

    def __post_init__(self) -> None:
        from ._deepseek_admission import _ENDPOINT, _METHOD, _MODEL, _PROFILE_ID

        _check(all(type(value) is str for value in
                   (self.profile_id, self.endpoint, self.model, self.method)))
        _check((self.profile_id, self.endpoint, self.model, self.method)
               == (_PROFILE_ID, _ENDPOINT, _MODEL, _METHOD))


def _create_deepseek_provider(config: ProviderConfig, key: MemoryKey | None, *,
                              sdk_loader: Callable[[], object],
                              transport_factory: Callable[[], object],
                              clock: Callable[[], float] = time.monotonic) -> SDKProviderAdapter:
    from ._deepseek_admission import _ENDPOINT, _METHOD, _MODEL, _PROFILE_ID

    _factory_validate(config, key)
    if config.endpoint != _ENDPOINT or config.model != _MODEL:
        raise ProviderError("AI_INVALID_INPUT")
    profile = _SDKWireProfile(_PROFILE_ID, _ENDPOINT, _MODEL, _METHOD)
    modules = sdk_loader()
    _check(type(modules) is dict and set(modules) == set(_MODULES))
    assert isinstance(modules, dict) and key is not None
    return SDKProviderAdapter(config, key, modules, transport_factory, clock, _wire_profile=profile)


def _json_template(data: dict[str, Any]) -> str:
    from ._deepseek_admission import _JSON_TEMPLATE, _PROBE_JSON_TEMPLATE

    return _PROBE_JSON_TEMPLATE if connection_stage(data) == "final" else _JSON_TEMPLATE


def _json_instructions(data: dict[str, Any]) -> str:
    # This is the SDK's documented prompted-output substitution. Its insertion
    # is checked against final bytes, never recovered by substring matching.
    if connection_stage(data) == "final":
        return _probe_json_instructions()
    return _json_template(data).format(schema=json.dumps(data["answerSchema"]))


def _load_sdk_modules() -> dict[str, Any]:
    """Load the pinned optional SDK without retaining dependency details."""
    missing = False
    try:
        modules = {key: importlib.import_module(name) for key, name in _MODULES.items()}
    except ImportError:
        missing = True
    if missing:
        raise ProviderError("AI_DEPENDENCY_MISSING") from None
    return modules


def _new_secure_transport(modules: object) -> Any:
    """Construct the dispatch-only transport with the existing closed policy."""
    _check(type(modules) is dict and set(modules) == set(_MODULES))
    assert isinstance(modules, dict)
    context = ssl.create_default_context()
    _check(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname,
           "AI_PROTOCOL_INCOMPATIBLE")
    return modules["httpx2"].AsyncHTTPTransport(
        verify=context, trust_env=False, proxy=None, retries=0)


def create_provider(config: ProviderConfig, key: MemoryKey | None) -> SDKProviderAdapter:
    _factory_validate(config, key)
    modules = _load_sdk_modules()
    return _create_provider(config, key, sdk_loader=lambda: modules,
                            transport_factory=lambda: _new_secure_transport(modules))


def _create_provider(config: ProviderConfig, key: MemoryKey | None, *,
                     sdk_loader: Callable[[], object], transport_factory: Callable[[], object],
                     clock: Callable[[], float] = time.monotonic) -> SDKProviderAdapter:
    _factory_validate(config, key)
    modules = sdk_loader()
    _check(type(modules) is dict and set(modules) == set(_MODULES))
    assert isinstance(modules, dict) and key is not None
    return SDKProviderAdapter(config, key, modules, transport_factory, clock)


class _Send:
    """Loop-owned bounded state, deliberately no value repr."""

    def __init__(self, draft: ProviderDraft, data: dict[str, Any], expected: list[dict[str, Any]],
                 resource_limits: tuple[int, int]) -> None:
        self.draft, self.data, self.expected = draft, data, expected
        self.resource_limits = resource_limits
        self.ready = asyncio.Event()
        self.response_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.queue: asyncio.Queue[ProviderEvent] = asyncio.Queue(maxsize=1)
        self.sdk_task: asyncio.Task[None] | None = None
        self.consumer: asyncio.Task[Any] | None = None
        self.prepared: PreparedRequest | None = None
        self.fields: tuple[Any, ...] | None = None
        self.binding: tuple[Any, ...] | None = None
        self.request: Any = None
        self.fingerprint: tuple[Any, ...] | None = None
        self.attribution: list[tuple[str, int, int, str]] = []
        self.metadata_attribution: list[tuple[str, int, int, str]] = []
        self.interpretation_attribution: list[tuple[str, int, int, str]] = []
        self.http: Any = None
        self.api: Any = None
        self.transport: Any = None
        self.response: Any = None
        self.stream: Any = None
        self.entries = 0
        self.transport_entered = False
        self.used = self.closed = False
        self.failure: SDKAdapterError | None = None
        self.delta_rejection: DeltaRejection | None = None


def _fingerprint(request: Any) -> tuple[Any, ...]:
    # The SDK still owns this live request while its capture coroutine waits.
    # Freeze identity as well as values so an equivalent replacement is refused.
    return id(request), request.method, str(request.url), request.content, tuple(request.headers.raw)


def _occurrence_fields(occurrences: tuple[WireOccurrence, ...]) -> tuple[tuple[Any, ...], ...]:
    _check(type(occurrences) is tuple)
    result = []
    for item in occurrences:
        _check(type(item) is WireOccurrence and type(item.pointer) is str
               and type(item.start) is int and type(item.end) is int and type(item.file) is str)
        result.append((item.pointer, item.start, item.end, item.file))
    return tuple(result)


def _fields(prepared: PreparedRequest) -> tuple[Any, ...]:
    _check(type(prepared.interpretation_occurrences) is tuple and all(
        type(item) is InterpretationOccurrence for item in prepared.interpretation_occurrences))
    accounting = prepared.estimated_input_accounting
    estimate = None
    if accounting is not None:
        _check(type(accounting) is EstimatedInputAccounting and type(accounting.profile_id) is str
               and type(accounting.method) is str and type(accounting.estimated_input_tokens) is int
               and type(accounting.body_sha256) is str)
        estimate = (accounting.profile_id, accounting.method,
                    accounting.estimated_input_tokens, accounting.body_sha256)
    return (prepared.preparation_id, prepared.method, prepared.endpoint, prepared.body,
            prepared.max_output_tokens, prepared.deadline, prepared.input_token_upper_bound,
            _occurrence_fields(prepared.source_occurrences),
            _occurrence_fields(prepared.metadata_occurrences),
            prepared.attribution_complete, estimate,
            tuple((item.pointer, item.start, item.end, item.origin_id) for item in prepared.interpretation_occurrences))


def _attribution(send: _Send, wire: dict[str, Any], *,
                 tag_key: str = "sourceTags") -> list[tuple[str, int, int, str]]:
    """Map each service tag to its exact decoded wire slot, without deduplication."""
    slots: dict[str, tuple[str, str]] = {}
    for index, message in enumerate(send.data["messages"]):
        prefix = f"/messages/{index}"
        if message.get("content") is not None:
            slots[prefix + "/content"] = (prefix + "/content", message["content"])
        for number, call in enumerate(message.get("toolCalls", [])):
            pointer = prefix + f"/toolCalls/{number}/arguments"
            slots[pointer] = (prefix + f"/tool_calls/{number}/function/arguments", call["arguments"])
    allowed = {slot[0] for slot in slots.values()}
    # A tagged slice may not also ride in model names, tool descriptions/schema,
    # call identifiers or any other string field lacking a message attribution.
    all_strings: dict[str, str] = {}

    def strings(value: Any, pointer: str) -> None:
        if type(value) is dict:
            for key, child in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                all_strings[pointer + "/" + escaped + "#key"] = key
                strings(child, pointer + "/" + escaped)
        elif type(value) is list:
            for number, child in enumerate(value):
                strings(child, pointer + f"/{number}")
        elif type(value) is str:
            all_strings[pointer] = value

    strings(wire, "")
    mapped: list[tuple[str, int, int, str]] = []
    tagged_texts: set[str] = set()
    for tag in send.data.get(tag_key, []):
        wire_pointer, content = slots[tag["pointer"]]
        slice_text = content[tag["start"]:tag["end"]]
        tagged_texts.add(slice_text)
        mapped.append((wire_pointer, tag["start"], tag["end"],
                       tag["originId"] if tag_key == "interpretationTags" else tag["file"]))
    if tag_key == "sourceTags":
        approved = {(pointer, start, end) for pointer, start, end, _ in mapped}
        for source_text in tagged_texts:
            for pointer, content in all_strings.items():
                offset = 0
                while (start := content.find(source_text, offset)) >= 0:
                    _check(pointer in allowed and
                           (pointer, start, start + len(source_text)) in approved,
                           "AI_PROTOCOL_INCOMPATIBLE")
                    offset = start + 1
    return mapped


def _deepseek_attribution(send: _Send, wire: dict[str, Any]) -> tuple[
    list[tuple[str, int, int, str]], list[tuple[str, int, int, str]],
    list[tuple[str, int, int, str]],
]:
    """Derive identity mappings from logical slots and the verified SDK insertion.

    Exact message validation precedes this mapping. No search result can grant
    origin; the shared source inspector only detects undeclared copies later.
    """
    insertion = None
    if send.data["mode"] == "final":
        insertion = 1 if send.data["messages"][0]["role"] == "system" else 0
    slots: dict[str, tuple[str, str]] = {}
    for index, message in enumerate(send.data["messages"]):
        wire_index = index + int(insertion is not None and index >= insertion)
        logical_prefix, wire_prefix = f"/messages/{index}", f"/messages/{wire_index}"
        actual = wire["messages"][wire_index]
        if message.get("content") is not None:
            content = message["content"]
            _check(_equal(actual.get("content"), content), "AI_PROTOCOL_INCOMPATIBLE")
            slots[logical_prefix + "/content"] = (wire_prefix + "/content", content)
        for number, call in enumerate(message.get("toolCalls", [])):
            pointer = logical_prefix + f"/toolCalls/{number}/arguments"
            arguments = call["arguments"]
            _check(_equal(actual["tool_calls"][number]["function"]["arguments"], arguments),
                   "AI_PROTOCOL_INCOMPATIBLE")
            slots[pointer] = (wire_prefix + f"/tool_calls/{number}/function/arguments", arguments)
    groups: list[list[tuple[str, int, int, str]]] = []
    for tag_key in ("sourceTags", "metadataTags", "interpretationTags"):
        mapped = []
        for tag in send.data.get(tag_key, []):
            wire_pointer, content = slots[tag["pointer"]]
            _check(0 <= tag["start"] < tag["end"] <= len(content), "AI_PROTOCOL_INCOMPATIBLE")
            mapped.append((wire_pointer, tag["start"], tag["end"],
                           tag["originId"] if tag_key == "interpretationTags" else tag["file"]))
        groups.append(mapped)
    # The logical parser checks each group independently. Cross-group overlap
    # cannot provide two origins for the same characters, even under two files.
    previous: tuple[str, int, int, str] | None = None
    for item in sorted(groups[0] + groups[1] + groups[2]):
        _check(previous is None or item[0] != previous[0] or item[1] >= previous[2],
               "AI_PROTOCOL_INCOMPATIBLE")
        previous = item
    return groups[0], groups[1], groups[2]


class SDKProviderAdapter:
    """One preparation at a time; service owns the final consent/budget guard."""

    def __init__(self, config: ProviderConfig, key: MemoryKey, modules: dict[str, Any],
                 transport_factory: Callable[[], object], clock: Callable[[], float], *,
                 _wire_profile: _SDKWireProfile | None = None) -> None:
        _factory_validate(config, key)
        if _wire_profile is not None:
            _check(type(_wire_profile) is _SDKWireProfile)
            _wire_profile.__post_init__()
            _check(config.endpoint == _wire_profile.endpoint and config.model == _wire_profile.model)
        self._wire_profile = _wire_profile
        self._config = config
        self._key: MemoryKey | None = key
        self._modules, self._transport_factory, self._clock = modules, transport_factory, clock
        self._send: _Send | None = None
        self._closed = False
        self._cleanup_failed = False
        self._cleanup_lock = asyncio.Lock()

    def _binding(self, resource_limits: tuple[int, int]) -> tuple[Any, ...]:
        """Snapshot only exact primitives, anchored by capture, plus owner identities."""
        config, profile = self._config, self._wire_profile
        _validate_config(config)
        _check(all(type(value) is str for value in
                   (config.base_url, config.origin, config.endpoint, config.model))
               and type(config.revision) is int, "AI_PROTOCOL_INCOMPATIBLE")
        profile_fields = None
        if profile is not None:
            _check(type(profile) is _SDKWireProfile, "AI_PROTOCOL_INCOMPATIBLE")
            profile.__post_init__()
            _check(config.endpoint == profile.endpoint and config.model == profile.model,
                   "AI_PROTOCOL_INCOMPATIBLE")
            profile_fields = (id(profile), profile.profile_id, profile.endpoint,
                              profile.model, profile.method)
        _check(type(resource_limits) is tuple and len(resource_limits) == 2
               and all(type(value) is int and value >= 0 for value in resource_limits),
               "AI_PROTOCOL_INCOMPATIBLE")
        return ((id(config), config.base_url, config.origin, config.endpoint,
                 config.model, config.revision), profile_fields, resource_limits)

    def _remaining(self, send: _Send) -> float:
        remaining = send.draft.deadline - self._clock()
        _check(remaining > 0, "AI_NETWORK_TIMEOUT")
        _check(not self._closed and not send.closed, "AI_INVALID_INPUT")
        return remaining

    def _capture(self, send: _Send, request: Any) -> None:
        self._remaining(send)
        send.entries += 1
        _check(send.entries == 1, "AI_PROTOCOL_INCOMPATIBLE")
        body = request.content  # Never read an opaque stream.
        _check(type(body) is bytes and 1 <= len(body) <= _CAP, "AI_PROTOCOL_INCOMPATIBLE")
        validate_transport_target(self._config, method=request.method, endpoint=str(request.url))
        headers = request.headers
        key = self._key
        _check(key is not None, "AI_INVALID_INPUT")
        assert key is not None
        _check(headers.get("content-type") == "application/json"
               and headers.get("content-length") == str(len(body))
               and headers.get("authorization") == "Bearer " + key.value
               and headers.get("accept-encoding") == "identity", "AI_PROTOCOL_INCOMPATIBLE")
        allowed = {"content-type", "content-length", "authorization", "accept-encoding", "accept",
                   "connection", "host", "user-agent", "openai-organization", "openai-project",
                   "x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
                   "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
                   "x-stainless-async", "x-stainless-retry-count", "x-stainless-read-timeout"}
        counts: dict[str, int] = {}
        for name, value in headers.raw:
            lower = name.decode("ascii").lower()
            _check(lower in allowed, "AI_PROTOCOL_INCOMPATIBLE")
            counts[lower] = counts.get(lower, 0) + 1
            _check(counts[lower] == 1, "AI_PROTOCOL_INCOMPATIBLE")
            if lower in ("openai-organization", "openai-project"):
                _check(value == b"", "AI_PROTOCOL_INCOMPATIBLE")
            if lower == "x-stainless-retry-count":
                _check(value == b"0", "AI_PROTOCOL_INCOMPATIBLE")
        wire = _parse(body)
        if self._wire_profile is not None:
            self._wire_profile.__post_init__()
            self._deepseek_wire(send, wire)
            send.attribution, send.metadata_attribution, send.interpretation_attribution = _deepseek_attribution(send, wire)
        else:
            self._generic_wire(send, wire)
            send.attribution = _attribution(send, wire)
            send.metadata_attribution = _attribution(send, wire, tag_key="metadataTags")
            send.interpretation_attribution = _attribution(send, wire, tag_key="interpretationTags")
        send.request, send.fingerprint = request, _fingerprint(request)
        # The captured JSON does not prove the provider's hidden chat framing.
        # The service rejects this explicit absence of a verified input bound.
        input_bound = None
        occurrences = tuple(WireOccurrence(*item) for item in send.attribution)
        metadata = tuple(WireOccurrence(*item) for item in send.metadata_attribution)
        interpretations = tuple(InterpretationOccurrence(*item) for item in send.interpretation_attribution)
        prepared = PreparedRequest(uuid.uuid4().hex, "POST", self._config.endpoint, body,
                                   send.draft.max_output_tokens, send.draft.deadline,
                                   input_bound, occurrences, metadata, self._wire_profile is not None,
                                   interpretation_occurrences=interpretations)
        if self._wire_profile is not None:
            # Reuse only pure attribution inspection: no trusted-profile registry,
            # hidden-framing claim, service, ledger or job reservation in prepare.
            _inspect_wire_sources(prepared, wire, _structural_only=True)
            estimate = EstimatedInputAccounting(self._wire_profile.profile_id, "wire-bytes-v1",
                                                 len(body), hashlib.sha256(body).hexdigest())
            prepared = PreparedRequest(prepared.preparation_id, prepared.method, prepared.endpoint,
                                       body, prepared.max_output_tokens, prepared.deadline, None,
                                       occurrences, metadata, True, estimate, interpretations)
        send.prepared = prepared
        send.fields = _fields(send.prepared)
        send.binding = self._binding(send.resource_limits)
        send.ready.set()

    def _deepseek_wire(self, send: _Send, wire: Any) -> None:
        required = {"model", "messages", "stream", "max_tokens", "thinking", "stream_options"}
        optional = {"tool_choice", "tools", "response_format"}
        _check(type(wire) is dict and required <= set(wire) <= required | optional,
               "AI_PROTOCOL_INCOMPATIBLE")
        profile = self._wire_profile
        assert profile is not None
        _check(self._config.endpoint == profile.endpoint and self._config.model == profile.model
               and 1 <= len(send.expected) <= 40
               and wire["model"] == profile.model and _equal(wire["messages"], send.expected)
               and wire["stream"] is True and type(wire["max_tokens"]) is int
               and wire["max_tokens"] == send.draft.max_output_tokens
               and _equal(wire["thinking"], {"type": "disabled"})
               and _equal(wire["stream_options"], {"include_usage": True}),
               "AI_PROTOCOL_INCOMPATIBLE")
        if send.data["mode"] == "tools":
            expected_tools = [{"type": "function", "function": {"name": item["name"],
                "description": item["description"], "parameters": item["parameters"]}}
                for item in send.data["tools"]]
            _check(_equal(wire.get("tools"), expected_tools) and wire.get("tool_choice") == "auto",
                   "AI_PROTOCOL_INCOMPATIBLE")
        else:
            _check("tools" not in wire and "tool_choice" not in wire, "AI_PROTOCOL_INCOMPATIBLE")
        if send.data["mode"] == "final":
            _check(_equal(wire.get("response_format"), {"type": "json_object"}),
                   "AI_PROTOCOL_INCOMPATIBLE")
        else:
            _check("response_format" not in wire, "AI_PROTOCOL_INCOMPATIBLE")

    def _generic_wire(self, send: _Send, wire: Any) -> None:
        required = {"model", "messages", "stream", "max_completion_tokens"}
        optional = {"parallel_tool_calls", "tool_choice", "stream_options", "tools", "response_format"}
        _check(type(wire) is dict and required <= set(wire) <= required | optional,
               "AI_PROTOCOL_INCOMPATIBLE")
        _check(wire["model"] == self._config.model and wire["messages"] == send.expected
               and wire["stream"] is True and type(wire["max_completion_tokens"]) is int
               and wire["max_completion_tokens"] == send.draft.max_output_tokens,
               "AI_PROTOCOL_INCOMPATIBLE")
        _check("parallel_tool_calls" not in wire or wire["parallel_tool_calls"] is False,
               "AI_PROTOCOL_INCOMPATIBLE")
        _check("stream_options" not in wire or _equal(wire["stream_options"], {"include_usage": True}),
               "AI_PROTOCOL_INCOMPATIBLE")
        if send.data["mode"] == "tools":
            expected_tools = [{"type": "function", "function": {"name": item["name"],
                "description": item["description"], "parameters": item["parameters"], "strict": True}}
                for item in send.data["tools"]]
            _check(_equal(wire.get("tools"), expected_tools) and wire.get("tool_choice", "auto") == "auto"
                   and wire.get("parallel_tool_calls") is False, "AI_PROTOCOL_INCOMPATIBLE")
        else:
            _check("tools" not in wire and wire.get("tool_choice", "none") == "none",
                   "AI_PROTOCOL_INCOMPATIBLE")
        if send.data["mode"] == "final":
            connection = connection_stage(send.data) == "final"
            _check(_equal(wire.get("response_format"), {"type": "json_schema", "json_schema": {
                "name": "p" if connection else "CodeAgentAnswer", "strict": True,
                "schema": connection_schema() if connection else _schema()}}),
                "AI_PROTOCOL_INCOMPATIBLE")
        else:
            _check("response_format" not in wire, "AI_PROTOCOL_INCOMPATIBLE")

    async def prepare(self, draft: ProviderDraft) -> PreparedRequest:
        _check(not self._closed and self._send is None)
        failure: SDKAdapterError | None = None
        send: _Send | None = None
        try:
            data, expected = _draft(draft, self._clock(), _wire_profile=self._wire_profile)
            if self._wire_profile is not None and data["mode"] == "final":
                insertion = 1 if expected[0]["role"] == "system" else 0
                expected.insert(insertion, {"role": "system", "content": _json_instructions(data)})
            resource_limits = _resource_limits(self._wire_profile, data, draft)
            send = _Send(draft, data, expected, resource_limits)
            self._send = send
            owner = self
            httpx = self._modules["httpx2"]

            class CaptureGate(httpx.AsyncBaseTransport):  # type: ignore[name-defined, misc]
                async def handle_async_request(self, request: Any) -> Any:
                    owner._capture(send_state, request)
                    return await send_state.response_future

                async def aclose(self) -> None:
                    send_state.closed = True
                    if not send_state.response_future.done():
                        send_state.response_future.cancel()

            send_state = send
            send.http = httpx.AsyncClient(transport=CaptureGate(), follow_redirects=False,
                                         trust_env=False, timeout=self._remaining(send),
                                         headers={"Accept-Encoding": "identity"})
            key = self._key
            _check(key is not None)
            assert key is not None
            send.api = self._modules["openai"].AsyncOpenAI(
                api_key=key.value, base_url=self._config.base_url, http_client=send.http,
                max_retries=0, organization="", project="", webhook_secret="")
            send.sdk_task = asyncio.create_task(self._run(send))
            await asyncio.wait_for(send.ready.wait(), self._remaining(send))
            if send.failure:
                failure = send.failure
            elif send.prepared is None or send.sdk_task.done():
                failure = SDKAdapterError(
                    "AI_PROTOCOL_INCOMPATIBLE",
                    ProtocolDiagnostic("pre_send"),
                )
            else:
                return send.prepared
        except asyncio.CancelledError:
            if send is not None:
                await self._cleanup(send)
            raise
        except Exception as exc:
            failure = (_error(exc, "pre_send") if type(exc) is SDKAdapterError
                       else SDKAdapterError("AI_INVALID_INPUT") if send is None
                       else _error(exc, "pre_send"))
        if send is not None:
            if not await self._cleanup(send):
                failure = SDKAdapterError(
                    "AI_PROTOCOL_INCOMPATIBLE",
                    ProtocolDiagnostic("pre_send"),
                )
        failure = failure or SDKAdapterError(
            "AI_PROTOCOL_INCOMPATIBLE",
            ProtocolDiagnostic("pre_send"),
        )
        _reject(failure.code, failure.diagnostic, rejection=_private_rejection(failure))

    def _messages(self, send: _Send) -> list[Any]:
        module = self._modules["messages"]
        result = []
        for item in send.data["messages"]:
            role = item["role"]
            if role == "assistant":
                parts = []
                if item["content"] is not None:
                    parts.append(module.TextPart(content=item["content"]))
                parts.extend(module.ToolCallPart(tool_name=call["name"], args=call["arguments"],
                                                tool_call_id=call["id"]) for call in item["toolCalls"])
                result.append(module.ModelResponse(parts=parts))
            elif role == "tool":
                result.append(module.ModelRequest(parts=[module.ToolReturnPart(
                    tool_name=item["name"], content=item["content"], tool_call_id=item["toolCallId"])]))
            else:
                part = module.SystemPromptPart if role == "system" else module.UserPromptPart
                result.append(module.ModelRequest(parts=[part(content=item["content"])]))
        return result

    async def _put(self, send: _Send, event: ProviderEvent) -> None:
        _check(type(event.payload) is bytes and len(event.payload) <= 8192, "AI_PROTOCOL_INCOMPATIBLE")
        await asyncio.wait_for(send.queue.put(event), self._remaining(send))

    async def _run(self, send: _Send) -> None:
        boundary: ProtocolBoundary = "pre_send"
        check_site: ProtocolCheckSite = "sdk_decode"
        try:
            modules = self._modules
            deepseek = self._wire_profile is not None
            profile_options = (dict(supports_json_schema_output=False, supports_json_object_output=True,
                                   openai_chat_supports_max_completion_tokens=False,
                                   openai_supports_strict_tool_definition=False,
                                   openai_chat_send_back_thinking_parts=False)
                               if deepseek else dict(supports_json_schema_output=True,
                                                   openai_chat_supports_max_completion_tokens=True))
            model = modules["sdk"].OpenAIChatModel(self._config.model,
                provider=modules["providers"].OpenAIProvider(openai_client=send.api),
                profile=modules["profiles"].OpenAIModelProfile(**profile_options))
            settings: dict[str, Any] = {"max_tokens": send.draft.max_output_tokens}
            if deepseek:
                settings["extra_body"] = {"thinking": {"type": "disabled"}}
            else:
                settings["parallel_tool_calls"] = False
            options: dict[str, Any] = {}
            mode = send.data["mode"]
            if mode == "tools":
                options["function_tools"] = [modules["tools"].ToolDefinition(name=item["name"],
                    description=item["description"],
                    parameters_json_schema=deepcopy(item["parameters"]) if deepseek else item["parameters"],
                    strict=False if deepseek else True)
                    for item in send.data["tools"]]
            else:
                settings["tool_choice"] = "none"
            if mode == "final":
                connection = connection_stage(send.data) == "final"
                options.update(output_mode="prompted" if deepseek else "native",
                    output_object=modules["output"].OutputObjectDefinition(
                    json_schema=connection_schema() if connection else _schema(),
                    name=None if deepseek else "p" if connection else "CodeAgentAnswer",
                    strict=False if deepseek else True))
                if deepseek:
                    options["prompted_output_template"] = _json_template(send.data)
            parameters = modules["models"].ModelRequestParameters(**options)
            completion: list[ProviderEvent] = []
            async with model.request_stream(self._messages(send), settings, parameters) as response:
                module = modules["messages"]
                boundary = "dispatch"
                async for event in response:
                    self._remaining(send)
                    text = None
                    if isinstance(event, module.PartStartEvent) and isinstance(event.part, module.TextPart):
                        text = event.part.content
                    elif isinstance(event, module.PartDeltaEvent) and isinstance(event.delta, module.TextPartDelta):
                        text = event.delta.content_delta
                    if text and not (deepseek and connection_stage(send.data) == "final"):
                        for chunk in _text_chunks(text):
                            await self._put(send, ProviderEvent("text", chunk))
                boundary = "result_validation"
                stream = send.stream
                _check(stream is not None and stream.done and stream.finish in ("stop", "tool_calls"),
                       "AI_PROTOCOL_INCOMPATIBLE")
                parts = response.get().parts
                texts, tools = [], []
                for part in parts:
                    if isinstance(part, module.TextPart):
                        texts.append(part.content)
                    elif isinstance(part, module.ToolCallPart):
                        tools.append(part)
                    else:
                        _reject("AI_PROTOCOL_INCOMPATIBLE")
                _check(mode == "tools" or not tools, "AI_PROTOCOL_INCOMPATIBLE")
                tool_events = []
                ids: set[str] = set()
                declared = {item["name"] for item in send.data["tools"]}
                for tool in tools:
                    call_id = _string(tool.tool_call_id, 512, printable=True,
                                      code="AI_PROTOCOL_INCOMPATIBLE")
                    _check(call_id not in ids and len(ids) < 8 and tool.tool_name in declared,
                           "AI_PROTOCOL_INCOMPATIBLE")
                    ids.add(call_id)
                    _check(type(tool.args) is str, "AI_PROTOCOL_INCOMPATIBLE")
                    args = _remote_json(tool.args.encode(), 8192)
                    _check(type(args) is dict, "AI_PROTOCOL_INCOMPATIBLE")
                    payload = _encode({"id": call_id, "name": tool.tool_name, "arguments": args})
                    _check(len(payload) <= 8192, "AI_PROTOCOL_INCOMPATIBLE")
                    tool_events.append(ProviderEvent("tool", payload))
                _check(bool(tools) == (stream.finish == "tool_calls"), "AI_PROTOCOL_INCOMPATIBLE")
                # Access the actual SDK usage property; raw presence remains authoritative.
                usage = response.usage
                if deepseek:
                    _check(stream.usage is not None, "AI_PROTOCOL_INCOMPATIBLE")
                if stream.usage is not None:
                    _check(usage.input_tokens == stream.usage[0] and usage.output_tokens == stream.usage[1],
                           "AI_PROTOCOL_INCOMPATIBLE")
                for tool_event in tool_events:
                    completion.append(tool_event)
                if mode == "final":
                    answer = "".join(texts)
                    _check(bool(answer) and len(answer.encode()) <= 65536, "AI_PROTOCOL_INCOMPATIBLE")
                    fragments = list(_answer_chunks(answer))
                    for index, fragment in enumerate(fragments):
                        completion.append(ProviderEvent("answer", _encode({"index": index,
                            "last": index == len(fragments) - 1, "content": fragment})))
                reported = stream.usage
                completion.append(ProviderEvent("usage", _encode({
                    "inputTokens": reported[0] if reported else None,
                    "outputTokens": reported[1] if reported else None,
                    "basis": "actual" if reported else "unknown"})))
                # Any failure while the entered SDK context releases transport
                # state is a dispatch failure, not model-result validation.
                boundary = "dispatch"
                check_site = "context_exit"
            # Completion is actionable only after normal SDK context termination.
            for completed_event in completion:
                await self._put(send, completed_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if boundary != "result_validation" and send.transport_entered:
                boundary = "dispatch"
            send.failure = _carry_rejection(_error(exc, boundary, check_site), send.delta_rejection)
        finally:
            send.ready.set()

    def _handoff_check(self, send: _Send, prepared: PreparedRequest) -> None:
        self._remaining(send)
        expected_resources = _resource_limits(self._wire_profile, send.data, send.draft)
        _check(send.resource_limits == expected_resources
               and _equal(self._binding(expected_resources), send.binding),
               "AI_PROTOCOL_INCOMPATIBLE")
        _check(self._send is send and type(prepared) is PreparedRequest and prepared is send.prepared
               and _equal(_fields(prepared), send.fields) and not send.used and send.sdk_task is not None
               and not send.sdk_task.done() and not send.response_future.done())
        output_limit = (4_096 if self._wire_profile is not None
                        and connection_stage(send.data) is None
                        and send.data["mode"] == "final" else 2_000)
        _check(type(prepared.body) is bytes and 1 <= len(prepared.body) <= _CAP
               and type(prepared.max_output_tokens) is int
               and 1 <= prepared.max_output_tokens <= output_limit
               and type(prepared.deadline) in (int, float) and math.isfinite(prepared.deadline))
        validate_transport_target(self._config, method=prepared.method, endpoint=prepared.endpoint)
        _check(_fingerprint(send.request) == send.fingerprint and send.request.content is prepared.body,
               "AI_PROTOCOL_INCOMPATIBLE")

    async def dispatch(self, prepared: PreparedRequest) -> AsyncIterator[ProviderEvent]:
        send = self._send
        _check(send is not None)
        assert send is not None
        failure: SDKAdapterError | None = None
        active = False
        boundary: ProtocolBoundary = "pre_send"
        check_site: ProtocolCheckSite = "response_headers"
        try:
            self._handoff_check(send, prepared)
            send.used = active = True
            send.consumer = asyncio.current_task()
            send.transport = self._transport_factory()
            self._remaining(send)
            # Compare to the capture-time binding again; factory entry is not
            # authority to replace or mutate config/profile, even at equal URLs.
            _check(_equal(self._binding(send.resource_limits), send.binding)
                   and prepared is send.prepared and _equal(_fields(prepared), send.fields)
                   and _fingerprint(send.request) == send.fingerprint
                   and send.request.content is prepared.body, "AI_PROTOCOL_INCOMPATIBLE")
            # No create_task/wait_for/Event: nested coroutine enters before suspension.
            boundary = "dispatch"
            async with asyncio.timeout(self._remaining(send)):
                send.transport_entered = True
                send.response = await send.transport.handle_async_request(send.request)
            # Response.aclose can mark itself closed before its stream closes.
            # Own the actual stream independently, including rejected responses.
            send.stream = send.response.stream
            self._remaining(send)
            status = send.response.status_code
            if status in (401, 403):
                _reject("AI_AUTH_FAILED")
            if status == 429:
                _reject("AI_RATE_LIMITED")
            if type(status) is int and 100 <= status <= 599 and not 200 <= status <= 299:
                _reject(
                    "AI_PROTOCOL_INCOMPATIBLE",
                    ProtocolDiagnostic("dispatch", status),
                )
            _check(type(status) is int and 200 <= status <= 299, "AI_PROTOCOL_INCOMPATIBLE")
            _check(send.response.headers.get("content-type", "").split(";", 1)[0].strip()
                   == "text/event-stream" and send.response.headers.get("content-encoding", "identity")
                   == "identity", "AI_PROTOCOL_INCOMPATIBLE")
            send.stream = _bounded_stream(self, send, send.stream)
            send.response.stream = send.stream
            sdk_task = send.sdk_task
            _check(sdk_task is not None, "AI_PROTOCOL_INCOMPATIBLE")
            assert sdk_task is not None
            _check(not send.response_future.done() and not sdk_task.done(), "AI_PROTOCOL_INCOMPATIBLE")
            send.response_future.set_result(send.response)
            check_site = "sdk_decode"
            while True:
                send.consumer = asyncio.current_task()
                if send.queue.empty() and sdk_task.done():
                    break
                waiter = asyncio.create_task(send.queue.get())
                try:
                    done, _ = await asyncio.wait((waiter, sdk_task), timeout=self._remaining(send),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    _check(bool(done), "AI_NETWORK_TIMEOUT")
                    if waiter in done:
                        self._remaining(send)
                        send.consumer = None
                        yield waiter.result()
                    elif send.queue.empty():
                        break
                finally:
                    if not waiter.done():
                        waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
            failure = send.failure
        except GeneratorExit:
            # Explicit iterator close must reach the fixed-error exit after
            # cleanup. Raise there, outside this handler, without close context.
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _carry_rejection(_error(exc, boundary, check_site), send.delta_rejection)
        finally:
            if active:
                if not await self._cleanup(send):
                    failure = SDKAdapterError(
                        "AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic("dispatch", check_site="cleanup"),
                        rejection=send.delta_rejection or _private_rejection(failure))
        if failure:
            _reject(failure.code, failure.diagnostic, rejection=_private_rejection(failure))

    async def _cleanup(self, send: _Send) -> bool:
        send.closed = True
        if not send.response_future.done():
            send.response_future.cancel()
        task = send.sdk_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        failed = False
        async with self._cleanup_lock:
            for name, method in (("stream", "aclose"), ("response", "aclose"), ("api", "close"),
                                 ("http", "aclose"), ("transport", "aclose")):
                resource = getattr(send, name)
                if resource is not None:
                    try:
                        await getattr(resource, method)()
                    except Exception:
                        failed = True
                    else:
                        setattr(send, name, None)
            if not failed:
                self._cleanup_failed = False
                send.request = send.fingerprint = send.prepared = send.fields = send.stream = None
                send.binding = None
                # A completed future retains its Response, even after client close.
                # Break that retention rather than relying on collection of cycles.
                send.response_future = asyncio.get_running_loop().create_future()
                send.response_future.cancel()
                send.data.clear()
                send.expected.clear()
                send.attribution.clear()
                send.metadata_attribution.clear()
                send.draft = ProviderDraft(b"", 0, 0)
                send.sdk_task = send.consumer = None
                while not send.queue.empty():
                    send.queue.get_nowait()
                if self._send is send:
                    self._send = None
        if failed:
            self._closed = True
            self._cleanup_failed = True
        return not failed

    async def cancel(self) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        self._key = None
        send = self._send
        if send is not None:
            send.closed = True
            consumer = send.consumer
            if consumer is not None and consumer is not asyncio.current_task() and not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await self._cleanup(send)
        if self._cleanup_failed:
            _reject("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic("dispatch", check_site="cleanup"))


def _text_chunks(text: str) -> Iterator[bytes]:
    # Synchronous generator; splitting decoded code points avoids invalid UTF-8.
    chunk = bytearray()
    for char in text:
        raw = char.encode("utf-8")
        if len(chunk) + len(raw) > 8192:
            yield bytes(chunk)
            chunk.clear()
        chunk.extend(raw)
    if chunk:
        yield bytes(chunk)


def _answer_chunks(text: str) -> Iterator[str]:
    # 1000 code points fits worst-case JSON escaping plus the bounded index framing.
    for start in range(0, len(text), 1000):
        yield text[start:start + 1000]


def _bounded_stream(owner: SDKProviderAdapter, send: _Send, underlying: Any) -> Any:
    httpx = owner._modules["httpx2"]
    # Capture already validated this closed, application-selected profile.
    # Response fields or later owner mutations cannot select framing.
    deepseek = send.binding is not None and send.binding[1] is not None
    count_limit = 2**53 - 1 if deepseek else 2**63 - 1
    raw_limit = send.binding[2][0] if send.binding is not None else _CAP
    frame_limit = send.binding[2][1] if send.binding is not None else 0

    class BoundedStream(httpx.AsyncByteStream):  # type: ignore[name-defined, misc]
        def __init__(self) -> None:
            self.done = False
            self.finish: str | None = None
            self.usage: tuple[int, int] | None = None
            self.raw_count = self.output_count = self.text_count = 0
            self.frame_count = 0
            self.calls: dict[int, dict[str, str]] = {}
            self.closed = False
            self.check_site: ProtocolCheckSite = "sse_syntax"

        def charge(self, count: int) -> None:
            self.output_count += count
            if not deepseek:
                _check(self.output_count <= send.draft.max_output_tokens, "AI_BUDGET_EXCEEDED")

        def guard(self, condition: bool, reason: str) -> None:
            if not condition:
                if send.delta_rejection is None:
                    send.delta_rejection = DeltaRejection(reason)
                _reject("AI_PROTOCOL_INCOMPATIBLE", rejection=send.delta_rejection)

        def checked_string(self, value: Any, prefix: str) -> str:
            self.guard(type(value) is str, prefix + "_type")
            self.guard(len(value) <= _CAP, prefix + "_string_size")
            try:
                value.encode("utf-8", "strict")
            except UnicodeError:
                self.guard(False, prefix + "_encoding")
            return str(value)

        def frame(self, raw: bytes) -> None:
            owner._remaining(send)
            self.check_site = "sse_syntax"
            value = _remote_json(raw)
            self.check_site = "chunk_shape"
            _check(type(value) is dict and set(value) <= {"id", "object", "created", "model", "choices",
                "usage", "system_fingerprint", "service_tier"}, "AI_PROTOCOL_INCOMPATIBLE")
            usage = value.get("usage")
            if deepseek:
                choices = value.get("choices")
                _check(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict
                       and self.finish is None, "AI_PROTOCOL_INCOMPATIBLE")
                finish = choices[0].get("finish_reason")
                if finish is not None and finish not in ("stop", "tool_calls"):
                    _reject_finish(finish)
                self.check_site = "usage_shape"
                _check(usage is None or choices[0].get("finish_reason") in ("stop", "tool_calls"),
                       "AI_PROTOCOL_INCOMPATIBLE")
            if usage is not None:
                self.check_site = "usage_shape"
                _check(self.usage is None and type(usage) is dict, "AI_PROTOCOL_INCOMPATIBLE")
                if deepseek:
                    # Cache counts describe the same prompt, never additional input.
                    for key in ("total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                        if key in usage:
                            _check(type(usage[key]) is int and 0 <= usage[key] <= count_limit,
                                   "AI_PROTOCOL_INCOMPATIBLE")
                    for key in ("prompt_tokens_details", "completion_tokens_details"):
                        details = usage.get(key)
                        if details is not None:
                            _check(type(details) is dict and all(type(n) is int and 0 <= n <= count_limit
                                   for n in details.values()), "AI_PROTOCOL_INCOMPATIBLE")
                counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
                _check(all(type(n) is int and 0 <= n <= count_limit for n in counts),
                       "AI_PROTOCOL_INCOMPATIBLE")
                _check(counts[1] <= send.draft.max_output_tokens, "AI_PROTOCOL_INCOMPATIBLE")
                for key in ("prompt_tokens_details", "completion_tokens_details"):
                    details = usage.get(key)
                    if details:
                        _check(type(details) is dict and all(type(n) is int and n >= 0 for n in details.values()),
                               "AI_PROTOCOL_INCOMPATIBLE")
                        _check(not details.get("reasoning_tokens") and not details.get("audio_tokens"),
                               "AI_PROTOCOL_INCOMPATIBLE")
                self.usage = counts
            choices = value.get("choices")
            self.check_site = "chunk_shape"
            _check(type(choices) is list and len(choices) <= 1, "AI_PROTOCOL_INCOMPATIBLE")
            if not choices:
                _check(self.finish is not None and usage is not None, "AI_PROTOCOL_INCOMPATIBLE")
                return
            choice = choices[0]
            _check(type(choice) is dict and set(choice) <= {"index", "delta", "finish_reason", "logprobs"}
                   and type(choice.get("index")) is int and choice["index"] == 0
                   and not choice.get("logprobs") and self.finish is None, "AI_PROTOCOL_INCOMPATIBLE")
            delta = choice.get("delta")
            self.check_site = "delta_shape"
            delta_keys = {"role", "content", "tool_calls", "refusal"}
            if deepseek:
                delta_keys.add("reasoning_content")
            self.guard(type(delta) is dict, "delta_object")
            self.guard(set(delta) <= delta_keys, "delta_fields")
            self.guard(not delta.get("refusal"), "delta_refusal")
            self.guard(delta.get("role", "assistant") == "assistant", "delta_role")
            if deepseek:
                reasoning = delta.get("reasoning_content")
                self.guard(reasoning is None or type(reasoning) is str and reasoning == "",
                           "delta_reasoning")
            text = delta.get("content")
            if text is not None:
                size = len(self.checked_string(text, "content").encode())
                self.charge(size)
                self.text_count += size
                self.guard(self.text_count <= 65536, "content_total_size")
            calls = delta.get("tool_calls")
            if calls is not None:
                self.guard(send.data["mode"] == "tools", "tool_mode")
                self.guard(type(calls) is list, "tool_list")
                self.guard(len(calls) <= 8, "tool_count")
                for call in calls:
                    self.guard(type(call) is dict, "tool_item_object")
                    self.guard(set(call) <= {"index", "id", "type", "function"}, "tool_item_fields")
                    index = call.get("index")
                    self.guard(type(index) is int and 0 <= index < 8, "tool_index")
                    self.guard(call.get("type", "function") == "function", "tool_type")
                    saved = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    function = call.get("function", {})
                    self.guard(type(function) is dict, "tool_function_object")
                    self.guard(set(function) <= {"name", "arguments"}, "tool_function_fields")
                    for key, fragment in (("id", call.get("id")), ("name", function.get("name")),
                                          ("arguments", function.get("arguments"))):
                        if fragment is not None:
                            fragment = self.checked_string(fragment, "fragment")
                            self.charge(len(fragment.encode()))
                            saved[key] += fragment
                            self.guard(len(saved[key].encode()) <= (8192 if key == "arguments" else 512),
                                       "fragment_joined_size")
            finish = choice.get("finish_reason")
            if finish is not None:
                self.check_site = "terminal_finish"
                if finish not in ("stop", "tool_calls"):
                    _reject_finish(finish)
                self.finish = finish
                self.check_site = "delta_shape"
                ids: set[str] = set()
                for call in self.calls.values():
                    self.guard(call["id"] not in ids, "terminal_duplicate_id")
                    self.guard(bool(call["id"]), "terminal_missing_id")
                    self.guard(call["name"] in {t["name"] for t in send.data["tools"]},
                               "terminal_unknown_name")
                    ids.add(call["id"])
                    argument_bytes = call["arguments"].encode()
                    try:
                        arguments = _remote_json(argument_bytes, 8192)
                    except SDKAdapterError as error:
                        if type(error) is SDKAdapterError and error.code == "AI_PROTOCOL_INCOMPATIBLE":
                            self.guard(False, "arguments_json")
                        raise
                    self.guard(type(arguments) is dict, "arguments_object")
                    payload = _encode({"id": call["id"], "name": call["name"], "arguments": arguments})
                    self.guard(len(payload) <= 8192, "tool_payload_size")
                    # Framing counts once, in addition to verbatim generated strings.
                    self.charge(max(0, len(payload) - sum(len(v.encode()) for v in call.values())))

        async def __aiter__(self) -> AsyncIterator[bytes]:
            buffer = bytearray()
            done_frame: bytes | None = None
            try:
                iterator = underlying.__aiter__()
                while True:
                    self.check_site = "sse_syntax"
                    try:
                        chunk = await asyncio.wait_for(anext(iterator), owner._remaining(send))
                    except StopAsyncIteration:
                        break
                    owner._remaining(send)
                    self.check_site = "chunk_shape"
                    _check(type(chunk) is bytes, "AI_PROTOCOL_INCOMPATIBLE")
                    self.raw_count += len(chunk)
                    _check(self.raw_count <= raw_limit, "AI_BUDGET_EXCEEDED")
                    buffer.extend(chunk)
                    while True:
                        # Both conventional SSE newline encodings, including chunk splits.
                        candidates = [(buffer.find(mark), mark) for mark in (b"\n\n", b"\r\n\r\n")]
                        candidates = [(pos, mark) for pos, mark in candidates if pos >= 0]
                        if not candidates:
                            break
                        position, marker = min(candidates)
                        self.check_site = "sse_syntax"
                        if deepseek:
                            owner._remaining(send)
                            self.frame_count += 1
                            _check(position <= _CAP and self.frame_count <= frame_limit,
                                   "AI_BUDGET_EXCEEDED")
                        frame = bytes(buffer[:position])
                        frame.decode("utf-8", "strict")
                        del buffer[:position + len(marker)]
                        lines = frame.splitlines()
                        if deepseek:
                            _check(bool(lines), "AI_PROTOCOL_INCOMPATIBLE")
                        if deepseek and all(line.startswith(b":") for line in lines):
                            # Comments (including after DONE) are still bounded by
                            # bytes, original deadline and complete framing/EOF.
                            continue
                        _check(done_frame is None, "AI_PROTOCOL_INCOMPATIBLE")
                        _check(all(line.startswith((b"data:", b":")) for line in lines),
                               "AI_PROTOCOL_INCOMPATIBLE")
                        data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
                        if not data:
                            _check(not deepseek or self.finish is None, "AI_PROTOCOL_INCOMPATIBLE")
                            continue
                        if data == b"[DONE]":
                            _check(self.finish is not None, "AI_PROTOCOL_INCOMPATIBLE")
                            done_frame = b"data: [DONE]\n\n"
                        else:
                            self.frame(data)
                            yield b"data: " + data + b"\n\n"
                    if deepseek:
                        # A transport chunk may contain many complete frames;
                        # only its unfinished logical tail uses the frame cap.
                        # Up to three bytes may instead be the prefix of a
                        # delimiter split across chunks; charge all bytes to
                        # raw_count, but do not charge framing as frame content.
                        delimiter_prefix = max((len(prefix) for prefix in
                            (b"\n", b"\r", b"\r\n", b"\r\n\r")
                            if buffer.endswith(prefix)), default=0)
                        _check(len(buffer) - delimiter_prefix <= _CAP, "AI_BUDGET_EXCEEDED")
                owner._remaining(send)
                self.check_site = "stream_eof"
                _check(done_frame is not None and not buffer, "AI_PROTOCOL_INCOMPATIBLE")
                assert done_frame is not None
                self.done = True
                yield done_frame
            except Exception as exc:
                failure = _error(exc, "dispatch", self.check_site)
                _reject(failure.code, failure.diagnostic, rejection=send.delta_rejection)
            finally:
                await self.aclose()

        async def aclose(self) -> None:
            if not self.closed:
                failure = None
                try:
                    await underlying.aclose()
                except Exception as exc:
                    failure = _error(exc, "dispatch", "cleanup")
                if failure is not None:
                    _reject(failure.code, failure.diagnostic, rejection=send.delta_rejection)
                self.closed = True

    return BoundedStream()
