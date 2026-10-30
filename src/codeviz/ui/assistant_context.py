"""Detached local publication values; capture is neither admission nor consent."""

from __future__ import annotations

import os
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PosixPath, WindowsPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from codeviz.assistant.snapshot import AnalysisSnapshot, AnchorRecord
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import ViewKind

if TYPE_CHECKING:
    from .controller import AnalysisStatus


class PublicationError(ValueError):
    """Fixed local errors, independent of provider and agent error taxonomies."""

    def __init__(self, code: str = "AI_ANALYSIS_REQUIRED") -> None:
        if type(code) is not str or code not in ("AI_ANALYSIS_BUSY", "AI_ANALYSIS_REQUIRED"):
            code = "AI_ANALYSIS_REQUIRED"
        self.code = code
        super().__init__("Analysis publication is busy." if code == "AI_ANALYSIS_BUSY"
                         else "Accepted analysis publication is required.")


def _text(value: object, *, maximum: int = 512, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not str or not value or len(value) > maximum:
        raise PublicationError()
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise PublicationError()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PublicationError() from None


def _items(values: object, cls: type[EntityRef] | type[AnchorRecord] | type[ViewNodeKey],
           maximum: int) -> None:
    if type(values) is not tuple or len(values) > maximum:
        raise PublicationError()
    for value in values:
        if type(value) is not cls:
            raise PublicationError()
        try:
            value.__post_init__()
        except ValueError:
            raise PublicationError() from None


@dataclass(frozen=True, slots=True)
class ViewNodeKey:
    view_kind: ViewKind
    root_id: str | None
    detail_source_id: str | None
    visible_node_id: str

    def __post_init__(self) -> None:
        if type(self.view_kind) is not ViewKind:
            raise PublicationError()
        _text(self.root_id, optional=True)
        _text(self.detail_source_id, optional=True)
        _text(self.visible_node_id)


@dataclass(frozen=True, slots=True)
class NodeContext:
    targets: tuple[EntityRef, ...]
    source_anchors: tuple[AnchorRecord, ...]
    source_only: bool
    truncated: bool
    reason: str | None

    def __post_init__(self) -> None:
        _items(self.targets, EntityRef, 20)
        _items(self.source_anchors, AnchorRecord, 20)
        if type(self.source_only) is not bool or type(self.truncated) is not bool:
            raise PublicationError()
        if self.reason is not None and (type(self.reason) is not str
                                       or self.reason != "unmapped_visible_identity"):
            raise PublicationError()


@dataclass(frozen=True, slots=True)
class NavigationFact:
    source_anchors: tuple[AnchorRecord, ...]
    visible_locations: tuple[ViewNodeKey, ...]

    def __post_init__(self) -> None:
        _items(self.source_anchors, AnchorRecord, 200_000)
        _items(self.visible_locations, ViewNodeKey, 100)


def _identity(value: AssistantContext | PublicationIdentity) -> None:
    if (type(value.publication_revision) is not int
            or not 0 <= value.publication_revision <= 2**63 - 1):
        raise PublicationError()
    if value.analysis_generation is not None and (
            type(value.analysis_generation) is not int
            or not 1 <= value.analysis_generation <= 2**63 - 1):
        raise PublicationError()
    _text(value.project_epoch, optional=True)
    _text(value.project_key, maximum=32_768, optional=True)
    if value.root is not None:
        if type(value.root) not in (PosixPath, WindowsPath) or not value.root.is_absolute():
            raise PublicationError()
    if value.project_key is not None:
        if (value.root is None or value.project_key !=
                os.path.normcase(os.path.normpath(str(value.root)))):
            raise PublicationError()
    if type(value.ignore_paths) is not tuple or len(value.ignore_paths) > 200_000:
        raise PublicationError()
    for path in value.ignore_paths:
        _text(path, maximum=32_768)
    # The enum stays in controller. Looking up its already-defined class avoids
    # a runtime controller import (including during controller construction).
    controller = sys.modules.get("codeviz.ui.controller")
    status_type = getattr(controller, "AnalysisStatus", None)
    if type(value.status) is not status_type or type(value.eligible) is not bool:
        raise PublicationError()


@dataclass(frozen=True, slots=True)
class AssistantContext:
    publication_revision: int
    project_epoch: str | None
    analysis_generation: int | None
    project_key: str | None
    root: Path | None
    ignore_paths: tuple[str, ...]
    status: AnalysisStatus
    eligible: bool
    snapshot: AnalysisSnapshot | None
    navigation_facts: Mapping[EntityRef, NavigationFact]
    context_map: Mapping[ViewNodeKey, NodeContext]

    def __post_init__(self) -> None:
        _identity(self)
        if self.snapshot is not None and type(self.snapshot) is not AnalysisSnapshot:
            raise PublicationError()
        # Only concrete dictionaries/proxies: no user mapping implementation or
        # retained caller-owned backing storage. All work precedes publication.
        if (type(self.navigation_facts) not in (dict, MappingProxyType)
                or type(self.context_map) not in (dict, MappingProxyType)
                or len(self.navigation_facts) > 200_000 or len(self.context_map) > 100):
            raise PublicationError()
        facts = dict(self.navigation_facts)
        nodes = dict(self.context_map)
        for target, fact in facts.items():
            _items((target,), EntityRef, 1)
            if type(fact) is not NavigationFact:
                raise PublicationError()
            fact.__post_init__()
        for key, node in nodes.items():
            _items((key,), ViewNodeKey, 1)
            if type(node) is not NodeContext:
                raise PublicationError()
            node.__post_init__()
        object.__setattr__(self, "navigation_facts", MappingProxyType(facts))
        object.__setattr__(self, "context_map", MappingProxyType(nodes))


@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    publication_revision: int
    project_epoch: str | None
    analysis_generation: int | None
    project_key: str | None
    root: Path | None
    ignore_paths: tuple[str, ...]
    status: AnalysisStatus
    eligible: bool

    def __post_init__(self) -> None:
        _identity(self)


class PublicationMirror(Protocol):
    """Trusted future bounded identity slot; not an event or callback API.

    Replacement may take only the service state lock, revoke bounded permits,
    mark cancellation, and coalesce a pending closure revision. It must not
    raise, do I/O, call clocks/callbacks, schedule work, or wait for teardown.
    Composition drains the pending slot outside publication/service locks.
    """

    def replace_current(self, identity: PublicationIdentity) -> None: ...


# Keep the approved plan's port name and the explicit Protocol spelling.
PublicationMirrorProtocol = PublicationMirror
