"""Closed JSON projection for locally constructed navigation targets.

This module validates and serializes an existing target only. Receipt ownership,
current disk/editor authorization, and outer poll identity remain caller duties.
"""

import json
import re
from datetime import datetime
from typing import NoReturn

from codeviz.core import SourceAnchor, ViewKind
from codeviz.navigation import RevealTarget

from .contracts import RequestScope
from .navigation import NavigationError, NavigationTarget
from .snapshot import SnapshotBinding
from .source_text import MonacoRange

_MAX_SAFE_INTEGER = 2**53 - 1
_SCOPE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UTC_TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(?:Z|\+00:00)"
)


def _unavailable() -> NoReturn:
    raise NavigationError() from None


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_SAFE_INTEGER:
        _unavailable()
    return value


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.isprintable():
        _unavailable()
    value.encode("utf-8", errors="strict")
    return value


def _scope_identifier(value: object) -> str:
    if type(value) is not str or _SCOPE_ID.fullmatch(value) is None:
        _unavailable()
    return value


def _timestamp(value: object) -> str:
    if type(value) is not str or len(value) > 64 or _UTC_TIMESTAMP.fullmatch(value) is None:
        _unavailable()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _unavailable()
    offset = parsed.utcoffset()
    if parsed.year == 0 or offset is None or offset.total_seconds() != 0:
        _unavailable()
    return value


def _path(value: object) -> str:
    path = _text(value, 512)
    parts = path.split("/")
    if "\\" in path or ":" in path or any(part in ("", ".", "..") for part in parts):
        _unavailable()
    return path


def _anchor(value: object) -> tuple[dict[str, object], bool, int, int]:
    if type(value) is not SourceAnchor:
        _unavailable()
    path = _path(value.file_path)
    start_line = _integer(value.start_line, minimum=1)
    end_line = _integer(value.end_line, minimum=1)
    if end_line < start_line:
        _unavailable()

    start_column = value.start_column
    end_column = value.end_column
    if start_column is not None:
        _integer(start_column)
    if end_column is not None:
        _integer(end_column)
    if start_column is not None and end_column is not None:
        complete_columns = True
        if start_line == end_line and end_column < start_column:
            _unavailable()
    else:
        complete_columns = False
    return ({
        "filePath": path,
        "startLine": start_line,
        "startColumn": start_column if complete_columns else None,
        "endLine": end_line,
        "endColumn": end_column if complete_columns else None,
    }, complete_columns, start_line, end_line)


def _monaco_range(value: object, *, ast_start: int, ast_end: int) -> dict[str, object]:
    if type(value) is not MonacoRange:
        _unavailable()
    start_line = _integer(value.start_line, minimum=1)
    start_column = _integer(value.start_column, minimum=1)
    end_line = _integer(value.end_line, minimum=1)
    end_column = _integer(value.end_column, minimum=1)
    if (start_line, start_column) > (end_line, end_column):
        _unavailable()
    if start_line < ast_start or end_line > ast_end:
        _unavailable()
    return {
        "startLineNumber": start_line,
        "startColumn": start_column,
        "endLineNumber": end_line,
        "endColumn": end_column,
    }


def _reveal(value: object) -> dict[str, object]:
    if type(value) is not RevealTarget or type(value.view_kind) is not ViewKind:
        _unavailable()
    if value.reason is not None:
        _unavailable()
    selected_id = _text(value.selected_id, 512)
    root_id = None if value.root_id is None else _text(value.root_id, 512)
    return {
        "viewKind": value.view_kind.value,
        "rootId": root_id,
        "selectedId": selected_id,
        "reason": None,
    }


def _project(target: NavigationTarget) -> dict[str, object]:
    if type(target) is not NavigationTarget:
        _unavailable()
    scope = target.scope
    binding = target.binding
    if type(scope) is not RequestScope or type(binding) is not SnapshotBinding:
        _unavailable()
    # Re-run validators because frozen typed objects can still be forged with
    # object.__new__/object.__setattr__.
    RequestScope.__post_init__(scope)
    SnapshotBinding.__post_init__(binding)
    project_epoch = _scope_identifier(scope.project_epoch)
    request_id = _scope_identifier(scope.request_id)
    generation = _integer(scope.analysis_generation)
    binding_epoch = _scope_identifier(binding.project_epoch)
    binding_generation = _integer(binding.analysis_generation)
    if (project_epoch, generation) != (binding_epoch, binding_generation):
        _unavailable()

    evidence_id = _text(target.evidence_id, 512)
    conversation_id = _text(target.conversation_id, 128)
    conversation_revision = _integer(target.conversation_revision)
    config_revision = _integer(target.config_revision)
    consent_revision = _integer(target.consent_revision)
    checked_at = _timestamp(target.checked_at)
    if type(target.action) is not str or target.action not in ("source", "graph"):
        _unavailable()

    ast_anchor, complete_columns, ast_start, ast_end = _anchor(target.ast_anchor)
    monaco_range: dict[str, object] | None = None
    reveal: dict[str, object] | None = None
    if target.action == "source":
        if target.reveal is not None:
            _unavailable()
        if target.monaco_range is not None:
            checked_range = _monaco_range(
                target.monaco_range, ast_start=ast_start, ast_end=ast_end)
            if complete_columns:
                monaco_range = checked_range
    else:
        if target.monaco_range is not None:
            _unavailable()
        reveal = _reveal(target.reveal)

    public: dict[str, object] = {
        "evidenceId": evidence_id,
        "scope": {
            "projectEpoch": project_epoch,
            "analysisGeneration": generation,
            "requestId": request_id,
        },
        "binding": {
            "projectEpoch": binding_epoch,
            "analysisGeneration": binding_generation,
        },
        "action": target.action,
        "astAnchor": ast_anchor,
        "monacoRange": monaco_range,
        "reveal": reveal,
        "checkedAt": checked_at,
        "conversationId": conversation_id,
        "conversationRevision": conversation_revision,
        "configRevision": config_revision,
        "consentRevision": consent_revision,
    }
    encoded = json.dumps(
        public, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 8192:
        _unavailable()
    return public


def navigation_target_to_public(target: NavigationTarget) -> dict[str, object]:
    """Return the exact bounded C7 DTO or one fixed navigation failure."""
    try:
        return _project(target)
    except Exception:
        pass
    _unavailable()
