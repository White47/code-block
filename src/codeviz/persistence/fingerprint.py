"""Content fingerprints for explicit, one-shot project analysis."""

import os
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG

from codeviz.analysis.discovery import _normalize_ignore, discover_python_files
from codeviz.source_handles import opened_file_path

ANALYSIS_VERSION = "1"
PARSER_VERSION = f"python-{sys.version_info.major}.{sys.version_info.minor}"
SEMANTIC_VERSION = "1"
_MAX_SOURCE_BYTES = 50_000_000


@dataclass(frozen=True, slots=True)
class ProjectFingerprint:
    files: tuple[tuple[str, str], ...]
    ignore_paths: tuple[str, ...]
    parser_version: str
    semantic_version: str
    analysis_version: str

    def to_payload(self) -> dict[str, object]:
        return {
            "files": [[path, digest] for path, digest in self.files],
            "ignore_paths": list(self.ignore_paths),
            "parser_version": self.parser_version,
            "semantic_version": self.semantic_version,
            "analysis_version": self.analysis_version,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "ProjectFingerprint":
        if not isinstance(payload, dict) or set(payload) != {
            "files", "ignore_paths", "parser_version", "semantic_version", "analysis_version"
        }:
            raise ValueError("invalid fingerprint record")
        raw_files, raw_ignored = payload["files"], payload["ignore_paths"]
        if not isinstance(raw_files, list) or not isinstance(raw_ignored, list):
            raise ValueError("invalid fingerprint paths")
        files: list[tuple[str, str]] = []
        for pair in raw_files:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    type(pair[0]) is not str or type(pair[1]) is not str or
                    len(pair[1]) != 64 or any(char not in "0123456789abcdef" for char in pair[1])):
                raise ValueError("invalid fingerprint file hash")
            files.append((pair[0], pair[1]))
        if (files != sorted(files) or len({path for path, _ in files}) != len(files) or
                any(type(path) is not str for path in raw_ignored) or
                any(type(payload[key]) is not str for key in (
                    "parser_version", "semantic_version", "analysis_version"))):
            raise ValueError("invalid fingerprint metadata")
        ignored = tuple(raw_ignored)
        if tuple(sorted(set(ignored))) != ignored:
            raise ValueError("invalid fingerprint ignore paths")
        try:
            if any(_normalize_ignore(path) != path for path in ignored):
                raise ValueError("invalid fingerprint ignore path")
        except ValueError as error:
            raise ValueError("invalid fingerprint ignore path") from error
        return cls(tuple(files), ignored, payload["parser_version"],
                   payload["semantic_version"], payload["analysis_version"])


def _file_digest(root: Path, relative_path: str) -> str:
    path = root.joinpath(*relative_path.split("/"))
    if path.is_symlink():
        raise ValueError("linked source files cannot be fingerprinted")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("source file resolves outside the project")
    before = resolved.stat()
    if not S_ISREG(before.st_mode) or before.st_size > _MAX_SOURCE_BYTES:
        raise ValueError("source file cannot be safely fingerprinted")
    digest = sha256()
    with resolved.open("rb") as source:
        opened = os.fstat(source.fileno())
        if not opened_file_path(source).is_relative_to(root):
            raise ValueError("opened source file is outside the project")
        if (not before.st_ino or not opened.st_ino or
                (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or
                not S_ISREG(opened.st_mode) or opened.st_size > _MAX_SOURCE_BYTES):
            raise ValueError("source file changed during fingerprinting")
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_SOURCE_BYTES:
                raise ValueError("source file exceeds fingerprint size limit")
            digest.update(chunk)
        after = os.fstat(source.fileno())
        if (after.st_size != opened.st_size or after.st_mtime_ns != opened.st_mtime_ns or
                path.resolve(strict=True) != resolved):
            raise ValueError("source file changed during fingerprinting")
    return digest.hexdigest()


def fingerprint_project(
    project_root: Path,
    ignore_paths: tuple[str, ...] = (),
    *,
    parser_version: str = PARSER_VERSION,
    semantic_version: str = SEMANTIC_VERSION,
    analysis_version: str = ANALYSIS_VERSION,
) -> ProjectFingerprint:
    """Hash discovered source bytes and all analysis-affecting configuration."""
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    ignored = tuple(sorted({_normalize_ignore(path) for path in ignore_paths}))
    files = tuple((path, _file_digest(root, path))
                  for path in discover_python_files(root, ignored))
    return ProjectFingerprint(files, ignored, parser_version,
                              semantic_version, analysis_version)


def outdated_reasons(saved: ProjectFingerprint,
                     current: ProjectFingerprint) -> tuple[str, ...]:
    reasons: list[str] = []
    if saved.files != current.files:
        reasons.append("source changed")
    if saved.ignore_paths != current.ignore_paths:
        reasons.append("analysis config changed")
    if saved.parser_version != current.parser_version:
        reasons.append("parser version changed")
    if saved.semantic_version != current.semantic_version:
        reasons.append("semantic rules version changed")
    if saved.analysis_version != current.analysis_version:
        reasons.append("analysis version changed")
    return tuple(reasons)
