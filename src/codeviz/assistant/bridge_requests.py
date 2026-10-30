"""Closed renderer inputs. These immutable selections grant no source authority."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar, cast

from codeviz.core import ViewKind

from .config import ProviderConfig
from .contracts import RequestScope
from .provider import MemoryKey
from .snapshot import MAX_SEQUENCE_ENTRIES, AnchorRecord
from .source_policy import is_protected_source_path, validate_source_path

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_T = TypeVar("_T")


class BridgeRequestError(ValueError):
    code = "AI_INVALID_INPUT"

    def __init__(self) -> None:
        super().__init__("Invalid assistant request.")


@dataclass(frozen=True, slots=True)
class QuestionContext:
    kind: Literal["question"] = field(default="question", init=False)


@dataclass(frozen=True, slots=True)
class ProjectContext:
    kind: Literal["project"] = field(default="project", init=False)


@dataclass(frozen=True, slots=True)
class NodeContext:
    view_kind: ViewKind
    visible_node_id: str
    root_id: str | None
    detail_source_id: str | None
    kind: Literal["node"] = field(default="node", init=False)


@dataclass(frozen=True, slots=True)
class SourceContext:
    file_path: str
    anchor: AnchorRecord | None
    mode: Literal["function", "file"]
    kind: Literal["source"] = field(default="source", init=False)


@dataclass(frozen=True, slots=True)
class UnderstandingContext:
    artifact_id: str
    revision: int
    item_id: str
    kind: Literal["understanding"] = field(default="understanding", init=False)


@dataclass(frozen=True, slots=True)
class StartRequest:
    scope: RequestScope
    conversation_id: str
    config_revision: int
    consent_revision: int
    question: str
    context: QuestionContext | ProjectContext | NodeContext | SourceContext | UnderstandingContext


@dataclass(frozen=True, slots=True)
class ConfigureRequest:
    base_url: str
    model: str
    enabled: bool
    key_action: Literal["keep", "clear", "set"]
    key: MemoryKey | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class CancelRequest:
    job_id: str
    project_epoch: str | None


@dataclass(frozen=True, slots=True)
class PollRequest:
    job_id: str
    project_epoch: str | None
    after_seq: int


@dataclass(frozen=True, slots=True)
class ProjectRequest:
    project_epoch: str


@dataclass(frozen=True, slots=True)
class ConsentCandidatesRequest:
    project_epoch: str
    analysis_generation: int


@dataclass(frozen=True, slots=True)
class ClearRequest:
    project_epoch: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class ConnectionRequest:
    config_revision: int
    request_id: str


@dataclass(frozen=True, slots=True)
class ConsentRequest:
    project_epoch: str
    analysis_generation: int
    config_revision: int
    allowed_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnderstandingRef:
    artifact_id: str
    revision: int
    item_id: str

    def to_public(self) -> dict[str, object]:
        return {"artifactId": self.artifact_id, "revision": self.revision, "itemId": self.item_id}


@dataclass(frozen=True, slots=True)
class ResolveRequest:
    project_epoch: str
    analysis_generation: int
    conversation_id: str
    evidence_id: str
    action: Literal["source", "graph"]
    understanding: UnderstandingRef | None = None


def _closed(value: object, keys: set[str]) -> dict[str, object]:
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or value.keys() != keys):
        raise ValueError()
    return cast(dict[str, object], value)


def _text(value: object, maximum: int, *, opaque: bool = False, prose: bool = False) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.strip():
        raise ValueError()
    value.encode("utf-8")
    if prose:
        invalid = any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value)
    else:
        invalid = not value.isprintable()
    if invalid:
        raise ValueError()
    if opaque and _ID.fullmatch(value) is None:
        raise ValueError()
    return value


def _id(value: object) -> str:
    return _text(value, 128, opaque=True)


def _optional_id(value: object) -> str | None:
    return None if value is None else _id(value)


def _natural(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise ValueError()
    return value


def _path(value: object) -> str:
    return validate_source_path(_text(value, 512))


def _understanding_ref(value: object) -> UnderstandingRef:
    binding = _closed(value, {"artifactId", "revision", "itemId"})
    item = _text(binding["itemId"], 128)
    if item != "project" and re.fullmatch(
            r"(?:summary|entry|steps|branches|relations|evidence|unknowns):(?:0|[1-9][0-9]*)", item) is None:
        raise ValueError()
    return UnderstandingRef(_id(binding["artifactId"]), _natural(binding["revision"], 1), item)


def _context(value: object) -> QuestionContext | ProjectContext | NodeContext | SourceContext | UnderstandingContext:
    if type(value) is not dict:
        raise ValueError()
    if value.get("kind") == "question":
        _closed(value, {"kind"})
        return QuestionContext()
    if value.get("kind") == "project":
        _closed(value, {"kind"})
        return ProjectContext()
    if value.get("kind") == "understanding":
        data = _closed(value, {"kind", "artifactId", "revision", "itemId"})
        binding = _understanding_ref({key: data[key] for key in ("artifactId", "revision", "itemId")})
        return UnderstandingContext(binding.artifact_id, binding.revision, binding.item_id)
    if value.get("kind") == "node":
        node = _closed(value, {"kind", "viewKind", "visibleNodeId", "rootId", "detailSourceId"})
        return NodeContext(ViewKind(_text(node["viewKind"], 64)),
                           _text(node["visibleNodeId"], 512), _optional_id(node["rootId"]),
                           _optional_id(node["detailSourceId"]))
    source = _closed(value, {"kind", "filePath", "anchor", "mode"})
    if source["kind"] != "source" or source["mode"] not in ("function", "file"):
        raise ValueError()
    path = _path(source["filePath"])
    anchor = None
    if source["anchor"] is not None:
        a = _closed(source["anchor"], {"filePath", "startLine", "startColumn", "endLine", "endColumn"})
        if _path(a["filePath"]) != path:
            raise ValueError()
        anchor = AnchorRecord(path, _natural(a["startLine"], 1),
                              None if a["startColumn"] is None else _natural(a["startColumn"]),
                              _natural(a["endLine"], 1),
                              None if a["endColumn"] is None else _natural(a["endColumn"]))
    return SourceContext(path, anchor, source["mode"])


def _start(value: object) -> StartRequest:
    data = _closed(value, {"projectEpoch", "analysisGeneration", "conversationId", "requestId",
                           "configRevision", "consentRevision", "question", "context"})
    return StartRequest(
        RequestScope(_id(data["projectEpoch"]), _natural(data["analysisGeneration"]),
                     _id(data["requestId"])),
        _id(data["conversationId"]), _natural(data["configRevision"]),
        _natural(data["consentRevision"]), _text(data["question"], 2000, prose=True),
        _context(data["context"]))


def _guard(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except Exception:
        pass
    # Do not retain a Unicode/path/type error containing renderer input.
    raise BridgeRequestError() from None


def parse_start(value: object) -> StartRequest:
    return _guard(lambda: _start(value))


def parse_configure(value: object) -> ConfigureRequest:
    def parse() -> ConfigureRequest:
        data = _closed(value, {"baseUrl", "model", "key", "enabled"})
        if type(data["enabled"]) is not bool:
            raise ValueError()
        config = ProviderConfig.from_base_url(_text(data["baseUrl"], 2048),
                                              _text(data["model"], 128), 0)
        key = data["key"]
        if key is None:
            action: Literal["keep", "clear", "set"] = "keep"
            memory_key = None
        elif type(key) is str and not key:
            action, memory_key = "clear", None
        else:
            action, memory_key = "set", MemoryKey(_text(key, 8192))
        return ConfigureRequest(config.base_url, config.model, data["enabled"], action, memory_key)
    return _guard(parse)


def parse_cancel(value: object) -> CancelRequest:
    def parse() -> CancelRequest:
        data = _closed(value, {"jobId", "projectEpoch"})
        return CancelRequest(_id(data["jobId"]), _optional_id(data["projectEpoch"]))
    return _guard(parse)


def parse_poll(value: object) -> PollRequest:
    def parse() -> PollRequest:
        data = _closed(value, {"jobId", "projectEpoch", "afterSeq"})
        return PollRequest(_id(data["jobId"]), _optional_id(data["projectEpoch"]),
                           _natural(data["afterSeq"]))
    return _guard(parse)


def parse_project(value: object) -> ProjectRequest:
    return _guard(lambda: ProjectRequest(_id(_closed(value, {"projectEpoch"})["projectEpoch"])))


def parse_consent_candidates(value: object) -> ConsentCandidatesRequest:
    def parse() -> ConsentCandidatesRequest:
        data = _closed(value, {"projectEpoch", "analysisGeneration"})
        return ConsentCandidatesRequest(_id(data["projectEpoch"]), _natural(data["analysisGeneration"], 1))
    return _guard(parse)


def parse_clear(value: object) -> ClearRequest:
    def parse() -> ClearRequest:
        data = _closed(value, {"projectEpoch", "conversationId"})
        return ClearRequest(_id(data["projectEpoch"]), _id(data["conversationId"]))
    return _guard(parse)


def parse_connection(value: object) -> ConnectionRequest:
    def parse() -> ConnectionRequest:
        data = _closed(value, {"configRevision", "requestId"})
        return ConnectionRequest(_natural(data["configRevision"]), _id(data["requestId"]))
    return _guard(parse)


def parse_consent(value: object) -> ConsentRequest:
    def parse() -> ConsentRequest:
        data = _closed(value, {"projectEpoch", "analysisGeneration", "configRevision", "allowedFiles", "confirmed"})
        files = data["allowedFiles"]
        if data["confirmed"] is not True or type(files) is not list or len(files) > MAX_SEQUENCE_ENTRIES:
            raise ValueError()
        paths = tuple(_path(item) for item in files)
        if (len({path.casefold() for path in paths}) != len(paths)
                or any(not path.endswith(".py") or is_protected_source_path(path) for path in paths)):
            raise ValueError()
        return ConsentRequest(_id(data["projectEpoch"]), _natural(data["analysisGeneration"]),
                              _natural(data["configRevision"]), tuple(sorted(paths)))
    return _guard(parse)


def parse_resolve(value: object) -> ResolveRequest:
    def parse() -> ResolveRequest:
        has_understanding = type(value) is dict and "understanding" in value
        data = _closed(value, {"projectEpoch", "analysisGeneration", "conversationId", "evidenceId", "action"}
                       | ({"understanding"} if has_understanding else set()))
        if data["action"] not in ("source", "graph"):
            raise ValueError()
        understanding = None
        if has_understanding:
            understanding = _understanding_ref(data["understanding"])
        return ResolveRequest(_id(data["projectEpoch"]), _natural(data["analysisGeneration"]),
                              _id(data["conversationId"]), _text(data["evidenceId"], 512), data["action"],
                              understanding)
    return _guard(parse)
