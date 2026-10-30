"""Private conservative disclosure origins; never evidence or exact AST text."""

import hashlib
from dataclasses import dataclass, field
from typing import Literal, cast

from .bridge_requests import _id, _natural
from .provider import InterpretationOccurrence
from .snapshot import SnapshotBinding
from .source_policy import validate_source_path
from .source_text import contains_suspected_secret


@dataclass(frozen=True, slots=True, repr=False)
class AnswerOriginManifest:
    binding: SnapshotBinding
    owner: tuple[str, str, str]
    conversation_revision: int
    config_revision: int
    consent_revision: int
    origin: str
    normalized_scope: str
    answer_digest: str
    dependency_files: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.binding) is not SnapshotBinding or type(self.owner) is not tuple or len(self.owner) != 3:
            raise ValueError()
        self.binding.__post_init__()
        for value in self.owner[1:]:
            _id(value)
        for revision in (self.conversation_revision, self.config_revision, self.consent_revision):
            _natural(revision)
        if (type(self.dependency_files) is not tuple or len(self.dependency_files) > 8
                or type(self.origin) is not str or not self.origin
                or type(self.normalized_scope) is not str or not self.normalized_scope):
            raise ValueError()
        paths = []
        for path, digest in self.dependency_files:
            if validate_source_path(path) != path:
                raise ValueError()
            self._digest(digest)
            paths.append(path)
        self._digest(self.answer_digest)
        if paths != sorted(set(paths)) or len({path.casefold() for path in paths}) != len(paths):
            raise ValueError()

    @staticmethod
    def _digest(value: str) -> None:
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError()

    def logical_payload(self) -> dict[str, object]:
        return {"kind": "answer_origin", "version": 1,
            "binding": [self.binding.project_root, list(self.binding.ignore_paths),
                        self.binding.project_epoch, self.binding.analysis_generation],
            "owner": list(self.owner), "conversationRevision": self.conversation_revision,
            "configRevision": self.config_revision, "consentRevision": self.consent_revision,
            "origin": self.origin, "normalizedScope": self.normalized_scope,
            "answerDigest": self.answer_digest, "dependencyFiles": [list(item) for item in self.dependency_files]}

    @classmethod
    def from_payload(cls, value: object) -> "AnswerOriginManifest":
        if type(value) is not dict or set(value) != {"kind", "version", "binding", "owner",
                "conversationRevision", "configRevision", "consentRevision", "origin", "normalizedScope",
                "answerDigest", "dependencyFiles"} or value["kind"] != "answer_origin" or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError()
        binding = value["binding"]
        if type(binding) is not list or len(binding) != 4 or type(binding[1]) is not list:
            raise ValueError()
        if type(value["owner"]) is not list or type(value["dependencyFiles"]) is not list:
            raise ValueError()
        return cls(SnapshotBinding(binding[0], tuple(binding[1]), binding[2], binding[3]),
            cast(tuple[str, str, str], tuple(value["owner"])), value["conversationRevision"],
            value["configRevision"], value["consentRevision"], value["origin"], value["normalizedScope"],
            value["answerDigest"], tuple(tuple(item) for item in value["dependencyFiles"]))

    def matches_answer(self, answer_json: str) -> bool:
        return hashlib.sha256(answer_json.encode("utf-8", "strict")).hexdigest() == self.answer_digest


@dataclass(frozen=True, slots=True, repr=False)
class PriorInterpretationOrigin:
    origin_id: str
    manifest: AnswerOriginManifest
    artifact_id: str
    artifact_revision: int
    item_id: str
    text: str = field(repr=False)
    kind: Literal["prior_interpretation"] = field(default="prior_interpretation", init=False)
    version: Literal[1] = field(default=1, init=False)

    def __post_init__(self) -> None:
        _id(self.origin_id)
        _id(self.artifact_id)
        _natural(self.artifact_revision, 1)
        if (type(self.manifest) is not AnswerOriginManifest or type(self.text) is not str
                or not 1 <= len(self.text) <= 1000 or type(self.item_id) is not str):
            raise ValueError()
        self.manifest.__post_init__()
        self.text.encode("utf-8", "strict")
        if contains_suspected_secret(self.text):
            raise ValueError()


def strings_in(value: object, pointer: str = "") -> dict[str, str]:
    strings: dict[str, str] = {}
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError()
            escaped = key.replace("~", "~0").replace("/", "~1")
            strings[pointer + "/" + escaped + "#key"] = key
            strings.update(strings_in(child, pointer + "/" + escaped))
    elif type(value) is list:
        for index, child in enumerate(value):
            strings.update(strings_in(child, pointer + f"/{index}"))
    elif type(value) is str:
        strings[pointer] = value
    return strings


def interpretation_footprint(occurrences: tuple[InterpretationOccurrence, ...], strings: dict[str, str],
        trusted: tuple[PriorInterpretationOrigin, ...] | None,
        occupied: set[tuple[str, int, int]]) -> tuple[int, tuple[str, ...], set[tuple[str, int, int]]]:
    """None is structural SDK inspection only; service admission always supplies a tuple."""
    if type(occurrences) is not tuple or len(occurrences) > 1:
        raise ValueError()
    origins = {} if trusted is None else {origin.origin_id: origin for origin in trusted}
    if trusted is not None:
        if type(trusted) is not tuple or len(origins) != len(trusted) or len(trusted) > 1:
            raise ValueError()
        for origin in trusted:
            if type(origin) is not PriorInterpretationOrigin:
                raise ValueError()
            origin.__post_init__()
        if {item.origin_id for item in occurrences} != set(origins):
            raise ValueError()
    count = 0
    files: set[str] = set()
    approved: set[tuple[str, int, int]] = set()
    for item in occurrences:
        if (type(item) is not InterpretationOccurrence or type(item.pointer) is not str
                or not item.pointer.startswith("/messages/") or type(item.start) is not int or type(item.end) is not int):
            raise ValueError()
        _id(item.origin_id)
        text = strings.get(item.pointer)
        if text is None or not 0 <= item.start < item.end <= len(text):
            raise ValueError()
        location = (item.pointer, item.start, item.end)
        if any(pointer == item.pointer and start < item.end and item.start < end
               for pointer, start, end in occupied | approved):
            raise ValueError()
        fragment = text[item.start:item.end]
        if not 1 <= len(fragment) <= 1000 or contains_suspected_secret(fragment):
            raise ValueError()
        fragment.encode("utf-8", "strict")
        if trusted is not None:
            matched_origin = origins.get(item.origin_id)
            if matched_origin is None or fragment != matched_origin.text:
                raise ValueError()
            files.update(path for path, _hash in matched_origin.manifest.dependency_files)
        for pointer, content in strings.items():
            offset = 0
            while (start := content.find(fragment, offset)) >= 0:
                if (pointer, start, start + len(fragment)) != location:
                    raise ValueError()
                offset = start + 1
        approved.add(location)
        count += len(fragment)
    return count, tuple(sorted(files)), approved


def interpretation_tags(value: dict[str, object]) -> tuple[InterpretationOccurrence, ...]:
    tags = value.get("interpretationTags", [])
    if type(tags) is not list or len(tags) > 1:
        raise ValueError()
    result = []
    for item in tags:
        if type(item) is not dict or set(item) != {"pointer", "start", "end", "originId"}:
            raise ValueError()
        result.append(InterpretationOccurrence(item["pointer"], item["start"], item["end"], item["originId"]))
    return tuple(result)
