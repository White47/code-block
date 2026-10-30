"""Private, versioned JSON analysis cache; never writes to analyzed projects."""

import json
import os
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from codeviz.analysis.project import ProjectAnalysis
from codeviz.core import SourceAnchor
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.persistence.fingerprint import (
    ANALYSIS_VERSION,
    PARSER_VERSION,
    SEMANTIC_VERSION,
    ProjectFingerprint,
)
from codeviz.views import ViewState

_SCHEMA_VERSION = 1
_MAX_CACHE_BYTES = 64_000_000
_MAX_STATE_BYTES = 1_000_000


class CacheError(ValueError):
    """A cache cannot be safely read or written; analysis can still proceed."""


class CacheVersionMismatch(CacheError):
    """Old analysis requires reanalysis, not corruption recovery."""


def _validate_source_path(path: str) -> None:
    try:
        if (type(path) is not str or SourceAnchor(path, 1).file_path != path
                or not path.casefold().endswith(".py")):
            raise ValueError("invalid source path")
    except (TypeError, ValueError):
        raise CacheError("analysis cache source inventory mismatch") from None


def _validate_analysis_sources(analysis: ProjectAnalysis,
                               fingerprint: ProjectFingerprint) -> None:
    """Verify persisted provenance without reading current project sources."""
    expected: dict[str, str] = {}
    for path, digest in fingerprint.files:
        _validate_source_path(path)
        if path in expected:
            raise CacheError("analysis cache source inventory mismatch")
        expected[path] = digest
    seen: set[str] = set()
    for parsed in analysis.files:
        path = parsed.relative_path
        _validate_source_path(path)
        if path in seen:
            raise CacheError("analysis cache source inventory mismatch")
        seen.add(path)
        if path not in expected:
            raise CacheError("analysis cache source inventory mismatch")
        if parsed.content_hash is None:
            if parsed.was_parsed:
                raise CacheError("analysis cache source hash mismatch")
        elif parsed.content_hash != expected[path]:
            raise CacheError("analysis cache source hash mismatch")
    if seen != expected.keys():
        raise CacheError("analysis cache source inventory mismatch")


@dataclass(frozen=True, slots=True)
class CachedAnalysis:
    analysis: ProjectAnalysis
    fingerprint: ProjectFingerprint


def default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "codeviz"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "codeviz"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "codeviz"


class CacheStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    def _project_dir(self, project_root: Path) -> tuple[Path, Path]:
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise CacheError("project root is not a directory")
        data_root = self.data_dir.resolve()
        if data_root.is_relative_to(root):
            raise CacheError("cache directory must be outside the analyzed project")
        key = sha256(str(root).encode("utf-8")).hexdigest()
        directory = data_root / "projects" / key
        if directory.exists() and not directory.resolve().is_relative_to(data_root):
            raise CacheError("cache directory resolves outside application data")
        return root, directory

    def analysis_path(self, project_root: Path) -> Path:
        return self._project_dir(project_root)[1] / "analysis.json"

    def _write_json(self, target: Path, document: object, max_bytes: int) -> None:
        try:
            content = json.dumps(document, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
            if len(content) > max_bytes:
                raise CacheError("cache record exceeds size limit")
            directory = target.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not directory.resolve().is_relative_to(self.data_dir.resolve()):
                raise CacheError("cache directory resolves outside application data")
            temporary: Path | None = None
            try:
                with NamedTemporaryFile(mode="wb", dir=directory, prefix=f".{target.stem}-",
                                        suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError(f"cannot save cache record: {error}") from error

    @staticmethod
    def _read_json(target: Path, max_bytes: int) -> object | None:
        if not target.exists():
            return None
        if target.is_symlink():
            raise CacheError("cache record is a symbolic link")
        try:
            if target.stat().st_size > max_bytes:
                raise CacheError("cache record exceeds size limit")
            with target.open("rb") as source:
                content = source.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise CacheError("cache record exceeds size limit")
            decoded = cast(object, json.loads(content))
            if decoded is None:
                raise CacheError("cache record cannot be null")
            return decoded
        except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError(f"cannot load cache record: {error}") from error

    @staticmethod
    def _project_document(root: Path, data: object) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "project_root": str(root), "data": data}

    @staticmethod
    def _project_data(payload: object, root: Path) -> object:
        if (not isinstance(payload, dict) or set(payload) != {
            "schema_version", "project_root", "data"
        } or type(payload["schema_version"]) is not int or
                payload["schema_version"] != _SCHEMA_VERSION or
                payload["project_root"] != str(root)):
            raise CacheError("cache record has wrong schema or project")
        return payload["data"]

    def save_analysis(self, project_root: Path, analysis: ProjectAnalysis,
                      fingerprint: ProjectFingerprint) -> None:
        root, directory = self._project_dir(project_root)
        try:
            fingerprint_payload = fingerprint.to_payload()
            validated = ProjectFingerprint.from_payload(fingerprint_payload)
            if (validated.analysis_version != ANALYSIS_VERSION or
                    validated.semantic_version != SEMANTIC_VERSION or
                    validated.parser_version != PARSER_VERSION):
                raise CacheVersionMismatch("analysis versions changed; reanalysis required")
            _validate_analysis_sources(analysis, validated)
            document = {
                "schema_version": _SCHEMA_VERSION,
                "project_root": str(root),
                "fingerprint": fingerprint_payload,
                "analysis": encode_analysis(analysis),
            }
        except (TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError("cannot save analysis cache") from None
        self._write_json(directory / "analysis.json", document, _MAX_CACHE_BYTES)

    def load_analysis(self, project_root: Path) -> CachedAnalysis | None:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "analysis.json", _MAX_CACHE_BYTES)
        if payload is None:
            return None
        try:
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version", "project_root", "fingerprint", "analysis"
            } or type(payload["schema_version"]) is not int:
                raise CacheError("analysis cache schema is invalid")
            if payload["schema_version"] != _SCHEMA_VERSION:
                raise CacheError("analysis cache schema version is unsupported")
            if payload["project_root"] != str(root):
                raise CacheError("analysis cache belongs to another project")
            fingerprint = ProjectFingerprint.from_payload(payload["fingerprint"])
            if (fingerprint.analysis_version != ANALYSIS_VERSION or
                    fingerprint.semantic_version != SEMANTIC_VERSION or
                    fingerprint.parser_version != PARSER_VERSION):
                raise CacheVersionMismatch("analysis versions changed; reanalysis required")
            analysis = decode_analysis(payload["analysis"])
            _validate_analysis_sources(analysis, fingerprint)
            return CachedAnalysis(analysis, fingerprint)
        except (OSError, UnicodeError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, CacheError):
                raise
            raise CacheError("cannot load analysis cache") from None

    def save_view_state(self, project_root: Path, state: ViewState) -> None:
        root, directory = self._project_dir(project_root)
        self._write_json(directory / "view-state.json",
                         self._project_document(root, state.to_dict()), _MAX_STATE_BYTES)

    def load_view_state(self, project_root: Path) -> ViewState:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "view-state.json", _MAX_STATE_BYTES)
        if payload is None:
            return ViewState()
        data = self._project_data(payload, root)
        if not isinstance(data, dict):
            raise CacheError("view state has invalid fields")
        try:
            return ViewState.from_dict(data)
        except (TypeError, ValueError) as error:
            raise CacheError(f"invalid view state: {error}") from error

    def save_annotations(self, project_root: Path, notes: dict[str, str]) -> None:
        if (len(notes) > 1000 or any(not key or type(key) is not str or
                                     type(value) is not str or len(value) > 4000
                                     for key, value in notes.items())):
            raise CacheError("annotations exceed their limits")
        root, directory = self._project_dir(project_root)
        self._write_json(directory / "annotations.json",
                         self._project_document(root, notes), _MAX_STATE_BYTES)

    def load_annotations(self, project_root: Path) -> dict[str, str]:
        root, directory = self._project_dir(project_root)
        payload = self._read_json(directory / "annotations.json", _MAX_STATE_BYTES)
        if payload is None:
            return {}
        data = self._project_data(payload, root)
        if (not isinstance(data, dict) or len(data) > 1000 or
                any(type(key) is not str or not key or type(value) is not str or
                    len(value) > 4000 for key, value in data.items())):
            raise CacheError("invalid annotations record")
        return data

    def recent_projects(self) -> tuple[Path, ...]:
        payload = self._read_json(self.data_dir.resolve() / "recent-projects.json",
                                  _MAX_STATE_BYTES)
        if payload is None:
            return ()
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "paths"}
                or payload["schema_version"] != _SCHEMA_VERSION or
                not isinstance(payload["paths"], list) or len(payload["paths"]) > 10 or
                any(type(path) is not str for path in payload["paths"])):
            raise CacheError("invalid recent projects record")
        return tuple(path for raw in payload["paths"]
                     if (path := Path(raw)).is_dir())

    def record_recent_project(self, project_root: Path) -> None:
        root, _ = self._project_dir(project_root)
        previous = self.recent_projects()
        paths = [str(root), *(str(path) for path in previous if path != root)][:10]
        self._write_json(self.data_dir.resolve() / "recent-projects.json",
                         {"schema_version": _SCHEMA_VERSION, "paths": paths},
                         _MAX_STATE_BYTES)
