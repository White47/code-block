"""Stable identities and project-relative source locations."""

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath


class EntityRole(StrEnum):
    DEFINITION = "Definition"
    INSTANCE = "Instance"
    INVOCATION = "Invocation"


class NodeKind(StrEnum):
    PROJECT = "Project"
    MODULE = "Module"
    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    LEXICAL_SCOPE = "LexicalScope"
    VARIABLE = "Variable"
    PARAMETER = "Parameter"
    ATTRIBUTE = "Attribute"
    EXTERNAL_SYMBOL = "ExternalSymbol"
    UNRESOLVED_SYMBOL = "UnresolvedSymbol"
    CALL_SITE = "CallSite"
    FLOW_STEP = "FlowStep"
    CONTROL_BLOCK = "ControlBlock"
    DATA = "Data"


def _relative_path(value: str, *, allow_root: bool = False) -> str:
    if not value or "\x00" in value or PureWindowsPath(value).drive:
        raise ValueError("path must be project-relative")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or (not allow_root and not path.parts):
        raise ValueError("path must stay inside the project")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    file_path: str
    start_line: int
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", _relative_path(self.file_path))
        if self.start_line < 1:
            raise ValueError("start_line must be at least 1")
        end_line = self.end_line if self.end_line is not None else self.start_line
        object.__setattr__(self, "end_line", end_line)
        if end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        if self.start_column is not None and self.start_column < 0:
            raise ValueError("start_column must be nonnegative")
        if self.end_column is not None and self.end_column < 0:
            raise ValueError("end_column must be nonnegative")
        if (self.end_line == self.start_line and self.start_column is not None
                and self.end_column is not None and self.end_column < self.start_column):
            raise ValueError("end_column precedes start_column")


def stable_node_id(
    relative_path: str,
    qualified_name: str,
    kind: NodeKind,
    scope: str,
    role: EntityRole = EntityRole.DEFINITION,
    occurrence_key: str | None = None,
) -> str:
    """Create an ID without line-number dependence.

    A parser supplies a structural occurrence key for each instance or call site.
    """
    path = _relative_path(relative_path, allow_root=True)
    kind = NodeKind(kind)
    role = EntityRole(role)
    if not qualified_name:
        raise ValueError("qualified_name is required")
    if role != EntityRole.DEFINITION and not occurrence_key:
        raise ValueError("instances and invocations require occurrence_key")
    payload = ["v1", path, qualified_name, kind.value, scope, role.value, occurrence_key]
    digest = sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"node_{digest[:24]}"
