"""Private projection of an actually retrieved excerpt into the frozen wire."""

import json
import math
import re
from dataclasses import dataclass, replace

from .provider import PreparedRequest, ProviderDraft, WireOccurrence
from .response import ResponseError
from .snapshot import AnchorRecord
from .source_policy import validate_source_path
from .tool_contracts import ExcerptToolResult

_BODY_CAP = 256 * 1024
_MAX_LOGICAL_MESSAGES = 40
_MAX_WIRE_MESSAGES = 41
_MAX_OCCURRENCES = 1000
_MESSAGE_CONTENT_POINTER = re.compile(r"/messages/(0|[1-9][0-9]*)/content\Z")


@dataclass(frozen=True, slots=True)
class RootSourceFragment:
    evidence_id: str
    relative_path: str
    text: str
    returned_anchor: AnchorRecord
    truncated: bool

    @classmethod
    def from_result(cls, result: ExcerptToolResult) -> "RootSourceFragment":
        if type(result) is not ExcerptToolResult:
            raise ResponseError()
        result.__post_init__()
        return cls(result.evidence_id, result.relative_path, result.text,
                   result.returned_anchor, result.truncated)


def _canonical_root(fragment: RootSourceFragment) -> tuple[str, str, int]:
    """Revalidate the frozen fragment and reproduce its exact inert wire value."""
    if type(fragment) is not RootSourceFragment:
        raise ValueError()
    validated = ExcerptToolResult(
        fragment.evidence_id,
        fragment.relative_path,
        fragment.text,
        fragment.returned_anchor,
        fragment.truncated,
    )
    anchor = validated.returned_anchor
    header = json.dumps({"evidenceId": validated.evidence_id,
                         "relativePath": validated.relative_path,
                         "returnedAnchor": {"startLine": anchor.start_line,
                                            "startColumn": anchor.start_column,
                                            "endLine": anchor.end_line,
                                            "endColumn": anchor.end_column},
                         "truncated": validated.truncated},
                        ensure_ascii=False, separators=(",", ":")) + "\n"
    return header, header + validated.text, len(header)


def inject_root(draft: ProviderDraft, fragment: RootSourceFragment) -> tuple[ProviderDraft, str, str, int]:
    """Append inert retrieval text, preserving existing attribution coordinates."""
    failed = False
    try:
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        logical = json.loads(draft.body, object_pairs_hook=unique)
        required = {"version", "mode", "messages", "tools", "answerSchema", "sourceTags"}
        if (type(logical) is not dict or not required <= logical.keys()
                or not logical.keys() <= required | {"metadataTags"}
                or logical["version"] != 1 or logical["mode"] not in ("text", "tools", "final")
                or type(logical["messages"]) is not list
                or not 1 <= len(logical["messages"]) < 40
                or type(logical["sourceTags"]) is not list
                or type(logical.get("metadataTags", [])) is not list):
            raise ValueError()
        header, content, source_start = _canonical_root(fragment)
        pointer = f'/messages/{len(logical["messages"])}/content'
        logical["messages"].append({"role": "user", "content": content})
        logical["sourceTags"].append({"pointer": pointer, "start": source_start,
                                     "end": len(content), "file": fragment.relative_path})
        logical.setdefault("metadataTags", []).append({
            "pointer": pointer, "start": 0, "end": source_start, "file": fragment.relative_path})
        for group in (logical["sourceTags"], logical["metadataTags"]):
            group.sort(key=lambda tag: (tag["pointer"], tag["start"], tag["end"], tag["file"]))
        body = json.dumps(logical, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        if len(body) > _BODY_CAP:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise ResponseError()
    return replace(draft, body=body), pointer, content, source_start


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError()


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError()
    return number


def _wire_messages(prepared: PreparedRequest) -> list[object]:
    if (type(prepared) is not PreparedRequest or type(prepared.body) is not bytes
            or not 1 <= len(prepared.body) <= _BODY_CAP
            or prepared.body.startswith(b"\xef\xbb\xbf")
            or type(prepared.source_occurrences) is not tuple
            or type(prepared.metadata_occurrences) is not tuple
            or prepared.attribution_complete is not True
            or len(prepared.source_occurrences) + len(prepared.metadata_occurrences)
            > _MAX_OCCURRENCES):
        raise ValueError()
    text = prepared.body.decode("utf-8", "strict")
    wire = json.loads(text, object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant, parse_float=_finite_float)
    if type(wire) is not dict:
        raise ValueError()
    messages = wire.get("messages")
    if type(messages) is not list or not 1 <= len(messages) <= _MAX_WIRE_MESSAGES:
        raise ValueError()
    return messages


def _message_index(pointer: str, *, maximum: int) -> int:
    if type(pointer) is not str or len(pointer) > 64:
        raise ValueError()
    match = _MESSAGE_CONTENT_POINTER.fullmatch(pointer)
    if match is None:
        raise ValueError()
    index = int(match.group(1))
    if index >= maximum:
        raise ValueError()
    return index


def _validate_occurrence(item: object) -> WireOccurrence:
    if (type(item) is not WireOccurrence or type(item.pointer) is not str
            or not 1 <= len(item.pointer) <= 2048 or not item.pointer.startswith("/")
            or type(item.start) is not int or type(item.end) is not int
            or not 0 <= item.start < item.end <= _BODY_CAP
            or type(item.file) is not str or not 1 <= len(item.file) <= 2048):
        raise ValueError()
    item.pointer.encode("utf-8", "strict")
    validate_source_path(item.file)
    return item


def _root_candidates(group: tuple[WireOccurrence, ...], messages: list[object], *,
                     start: int, end: int, path: str, content: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for value in group:
        item = _validate_occurrence(value)
        if (item.start, item.end, item.file) != (start, end, path):
            continue
        index = _message_index(item.pointer, maximum=len(messages))
        message = messages[index]
        if (type(message) is not dict or set(message) != {"role", "content"}
                or type(message.get("role")) is not str or message["role"] != "user"
                or type(message.get("content")) is not str or message["content"] != content):
            continue
        candidates.append(item.pointer)
    return tuple(candidates)


def verify_root_wire(prepared: PreparedRequest, fragment: RootSourceFragment,
                     pointer: str, content: str, source_start: int) -> None:
    """Do not authorize an ID unless this exact captured send contains its source."""
    try:
        _header, expected_content, expected_start = _canonical_root(fragment)
        if (type(content) is not str or content != expected_content
                or type(source_start) is not int or source_start != expected_start):
            raise ValueError()
        messages = _wire_messages(prepared)
        _message_index(pointer, maximum=min(len(messages), _MAX_LOGICAL_MESSAGES))
        source = _root_candidates(
            prepared.source_occurrences, messages, start=expected_start,
            end=len(expected_content), path=fragment.relative_path,
            content=expected_content)
        metadata = _root_candidates(
            prepared.metadata_occurrences, messages, start=0,
            end=expected_start, path=fragment.relative_path,
            content=expected_content)
        if len(source) != 1 or len(metadata) != 1 or source[0] != metadata[0]:
            raise ValueError()
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
        raise ResponseError() from None
