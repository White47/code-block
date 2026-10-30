"""Explicit memory-only grants for exact backend project and destination identities."""

import os
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from threading import RLock

from .config import (
    ProviderConfig,
    _fail,
    _parse_url,
    _printable,
    _revision,
    _validate_config,
)
from .snapshot import MAX_SEQUENCE_ENTRIES
from .source_policy import (
    SourceExcerptError,
    is_protected_source_path,
    validate_source_path,
)


def _project(project_key: str) -> None:
    _printable(project_key, 4096)
    path = PureWindowsPath(project_key) if os.name == "nt" else PurePosixPath(project_key)
    if (not path.is_absolute()
            or os.path.normcase(os.path.normpath(project_key)) != project_key
            or os.name == "nt" and ("/" in project_key
                                   or project_key.startswith(("\\\\?\\", "\\\\.\\")))
            or os.name != "nt" and "\\" in project_key):
        _fail()
    invalid = False
    try:
        for component in path.parts[1:]:
            validate_source_path(component)
    except SourceExcerptError:
        invalid = True
    if invalid:
        _fail()


def _origin(origin: str) -> None:
    normalized, path = _parse_url(origin)
    if origin != normalized or path:
        _fail()


def _files(files: tuple[str, ...]) -> None:
    if type(files) is not tuple or len(files) > MAX_SEQUENCE_ENTRIES:
        _fail()
    invalid = False
    try:
        for path in files:
            _printable(path, 4096)
            validate_source_path(path)
            if not path.endswith(".py") or is_protected_source_path(path):
                _fail()
    except SourceExcerptError:
        invalid = True
    if invalid:
        _fail()
    if (files != tuple(sorted(set(files)))
            or len({path.casefold() for path in files}) != len(files)):
        _fail()


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    project_key: str
    origin: str
    config_revision: int
    allowed_files: tuple[str, ...]
    revision: int
    enabled: bool

    def __post_init__(self) -> None:
        _project(self.project_key)
        _origin(self.origin)
        _revision(self.config_revision)
        _files(self.allowed_files)
        _revision(self.revision)
        if type(self.enabled) is not bool:
            _fail()


class ConsentStore:
    """Own replacement revisions; never discover files or authorize implicitly."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._grants: dict[tuple[str, str], ConsentGrant] = {}
        self._revisions: dict[tuple[str, str], int] = {}
        self._configs: dict[tuple[str, str], ProviderConfig] = {}

    def grant(self, *, project_key: str, config: ProviderConfig,
              allowed_files: tuple[str, ...], confirmed: bool) -> ConsentGrant:
        if confirmed is not True:
            _fail()
        _validate_config(config)
        # Validate before touching existing state, including before revision replacement.
        candidate = ConsentGrant(project_key, config.origin, config.revision,
                                 allowed_files, 0, True)
        partition = (project_key, config.origin)
        with self._lock:
            revision = self._revisions.get(partition, 0) + 1
            _revision(revision)
            candidate = ConsentGrant(candidate.project_key, candidate.origin,
                                     candidate.config_revision, candidate.allowed_files,
                                     revision, True)
            self._grants[partition] = candidate
            self._revisions[partition] = revision
            self._configs[partition] = config
            return candidate

    def get(self, *, project_key: str, origin: str) -> ConsentGrant | None:
        _project(project_key)
        _origin(origin)
        with self._lock:
            return self._grants.get((project_key, origin))

    def revoke(self, *, project_key: str, origin: str) -> int:
        _project(project_key)
        _origin(origin)
        partition = (project_key, origin)
        with self._lock:
            revision = self._revisions.get(partition, 0)
            if partition in self._grants:
                revision += 1
                _revision(revision)
                del self._grants[partition]
                self._configs.pop(partition, None)
                self._revisions[partition] = revision
            return revision

    def is_current(self, grant: ConsentGrant, *, config: ProviderConfig) -> bool:
        if type(grant) is not ConsentGrant or type(config) is not ProviderConfig:
            return False
        partition = (grant.project_key, grant.origin)
        with self._lock:
            return (grant.enabled is True and self._grants.get(partition) is grant
                    and self._configs.get(partition) == config
                    and grant.config_revision == config.revision)
