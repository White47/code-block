"""Bounded local source identity observations, never model tool payloads.

Two scans and native byte reads describe an observation interval, not a disk
transaction or continuity guarantee. No observed hash repairs analysis facts.
"""

import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from time import monotonic_ns

from codeviz.analysis.discovery import DiscoveryScanError, discover_python_files_strict
from codeviz.source_handles import SourceRead, SourceReadError, read_source_bytes

from .contracts import AgentError, RequestScope
from .snapshot import AnalysisSnapshot, SnapshotBinding
from .source_policy import (
    SourceExcerptError,
    _binding,
    _fail,
    _text,
    is_protected_source_path,
    validate_source_path,
)

_HASH = re.compile(r"[0-9a-f]{64}")


def _scope(scope: RequestScope) -> None:
    if type(scope) is not RequestScope:
        _fail()
    try:
        _text(scope.project_epoch)
        _text(scope.request_id)
        RequestScope(scope.project_epoch, scope.analysis_generation, scope.request_id)
    except (AgentError, AttributeError):
        _fail()


def _hash(value: str) -> None:
    _text(value)
    if _HASH.fullmatch(value) is None:
        _fail()


def _root_observation(binding: SnapshotBinding) -> tuple[int, int, int, int]:
    """Use pathname metadata only; never mix Windows fstat ctime with lstat."""
    try:
        root = Path(binding.project_root)
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or not info.st_dev or not info.st_ino
                or str(root.resolve(strict=True)) != binding.project_root):
            _fail("verification_failed")
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns
    except (OSError, RuntimeError, ValueError):
        _fail("verification_failed")


def _native_digest(read: SourceRead, cap: int) -> str:
    if type(read) is not SourceRead or type(read.raw_bytes) is not bytes:
        _fail("verification_failed")
    if len(read.raw_bytes) > cap:
        _fail("verification_failed")
    try:
        _hash(read.content_hash)
    except SourceExcerptError:
        _fail("verification_failed")
    return sha256(read.raw_bytes).hexdigest()


class FreshnessStatus(StrEnum):
    FRESH = "Fresh"
    NOT_FRESH = "notFresh"


class FreshnessReason(StrEnum):
    SCOPE_MISMATCH = "scope_mismatch"
    ROOT_UNVERIFIED = "root_unverified"
    SCAN_INCOMPLETE = "scan_incomplete"
    INVENTORY_CHANGED = "inventory_changed"
    CONTENT_CHANGED = "content_changed"
    IDENTITY_MISSING = "identity_missing"
    FILE_UNVERIFIED = "file_unverified"
    PROTECTED_FILE = "protected_file"
    LIMIT_EXCEEDED = "limit_exceeded"


@dataclass(frozen=True, slots=True)
class FreshnessLimits:
    max_entries: int = 200_000
    max_directories: int = 20_000
    max_files: int = 200_000
    max_file_bytes: int = 5_000_000
    max_total_bytes: int = 50_000_000

    def __post_init__(self) -> None:
        for value, cap in ((self.max_entries, 200_000), (self.max_directories, 20_000),
                           (self.max_files, 200_000), (self.max_file_bytes, 5_000_000),
                           (self.max_total_bytes, 50_000_000)):
            if type(value) is not int or not 1 <= value <= cap:
                _fail()


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    """Private local metadata. Never serialize roots or observed hashes to tools."""

    binding: SnapshotBinding
    scope: RequestScope
    status: FreshnessStatus
    reasons: tuple[FreshnessReason, ...]
    observed_hashes: tuple[tuple[str, str], ...]
    scan_complete: bool
    started_ns: int
    checked_ns: int

    def __post_init__(self) -> None:
        _binding(self.binding)
        _scope(self.scope)
        if (type(self.status) is not FreshnessStatus or type(self.reasons) is not tuple
                or type(self.observed_hashes) is not tuple or type(self.scan_complete) is not bool):
            _fail()
        if any(type(r) is not FreshnessReason for r in self.reasons):
            _fail()
        if self.reasons != tuple(r for r in FreshnessReason if r in self.reasons):
            _fail()
        for pair in self.observed_hashes:
            if type(pair) is not tuple or len(pair) != 2:
                _fail()
            validate_source_path(pair[0])
            _hash(pair[1])
        if (self.observed_hashes != tuple(sorted(self.observed_hashes))
                or len({p for p, _ in self.observed_hashes}) != len(self.observed_hashes)):
            _fail()
        for value in (self.started_ns, self.checked_ns):
            if type(value) is not int or value < 0:
                _fail()
        if self.checked_ns < self.started_ns:
            _fail()
        if self.status is FreshnessStatus.FRESH:
            if self.reasons or not self.scan_complete:
                _fail()
        elif not self.reasons:
            _fail()


_DEFAULT_LIMITS = FreshnessLimits()


def verify_snapshot_freshness(
    snapshot: AnalysisSnapshot, *, scope: RequestScope,
    limits: FreshnessLimits = _DEFAULT_LIMITS,
    checkpoint: Callable[[], None] | None = None,
    clock_ns: Callable[[], int] = monotonic_ns,
) -> FreshnessReport:
    """Verify complete inventory against actual parsed bytes within fixed caps.

    The byte cap bounds successfully accepted raw bytes. B1 may read one extra
    byte to detect overflow; this is not an exact OS I/O or heap bound.
    """
    if type(snapshot) is not AnalysisSnapshot or type(limits) is not FreshnessLimits:
        _fail()
    _binding(snapshot.binding)
    _scope(scope)
    limits.__post_init__()
    if not callable(clock_ns) or (checkpoint is not None and not callable(checkpoint)):
        _fail()
    if not snapshot.matches_scope(scope):
        # Scope rejection calls neither a clock nor a callback nor the filesystem.
        return FreshnessReport(snapshot.binding, scope, FreshnessStatus.NOT_FRESH,
                               (FreshnessReason.SCOPE_MISMATCH,), (), False, 0, 0)

    def check() -> None:
        if checkpoint is not None:
            try:
                checkpoint()
            except AgentError:
                raise
            except Exception:
                _fail("verification_failed")

    def now() -> int:
        try:
            value = clock_ns()
        except Exception:
            _fail("verification_failed")
        if type(value) is not int or value < 0:
            _fail("verification_failed")
        return value

    started = now()
    reasons: set[FreshnessReason] = set()
    observed: list[tuple[str, str]] = []
    check()
    initial: tuple[int, int, int, int] | None = None
    try:
        initial = _root_observation(snapshot.binding)
    except SourceExcerptError:
        reasons.add(FreshnessReason.ROOT_UNVERIFIED)

    def scan() -> tuple[str, ...] | None:
        try:
            return discover_python_files_strict(
                Path(snapshot.binding.project_root), snapshot.binding.ignore_paths,
                max_entries=limits.max_entries, max_directories=limits.max_directories,
                max_files=limits.max_files, checkpoint=check,
            )
        except DiscoveryScanError as error:
            reasons.add({"ROOT_UNVERIFIED": FreshnessReason.ROOT_UNVERIFIED,
                         "SCAN_INCOMPLETE": FreshnessReason.SCAN_INCOMPLETE,
                         "LIMIT_EXCEEDED": FreshnessReason.LIMIT_EXCEEDED}[error.code])
            return None

    complete = False
    if initial is not None:
        inventory = snapshot.file_inventory
        paths = tuple(f.relative_path for f in inventory)
        first = scan()
        if first is not None and first != paths:
            reasons.add(FreshnessReason.INVENTORY_CHANGED)
        accepted_bytes = 0
        if first is not None:
            for file in inventory:
                check()
                try:
                    validate_source_path(file.relative_path)
                except SourceExcerptError:
                    reasons.add(FreshnessReason.FILE_UNVERIFIED)
                    continue
                missing = file.content_hash is None
                if missing:
                    reasons.add(FreshnessReason.IDENTITY_MISSING)
                if is_protected_source_path(file.relative_path):
                    reasons.add(FreshnessReason.PROTECTED_FILE)
                    continue
                if missing:
                    continue
                remaining = limits.max_total_bytes - accepted_bytes
                if remaining <= 0:
                    reasons.add(FreshnessReason.LIMIT_EXCEEDED)
                    break
                cap = min(limits.max_file_bytes, remaining)
                stop = False
                try:
                    read = read_source_bytes(Path(snapshot.binding.project_root),
                                             file.relative_path, max_bytes=cap)
                    digest = _native_digest(read, cap)
                    accepted_bytes += len(read.raw_bytes)
                    if digest != read.content_hash:
                        reasons.add(FreshnessReason.FILE_UNVERIFIED)
                    else:
                        observed.append((file.relative_path, digest))
                        if digest != file.content_hash:
                            reasons.add(FreshnessReason.CONTENT_CHANGED)
                except SourceReadError as error:
                    if error.code == "SOURCE_CHANGED":
                        reasons.add(FreshnessReason.CONTENT_CHANGED)
                    elif error.code == "FILE_TOO_LARGE" and remaining < limits.max_file_bytes:
                        reasons.add(FreshnessReason.LIMIT_EXCEEDED)
                        stop = True
                    else:
                        reasons.add(FreshnessReason.FILE_UNVERIFIED)
                except (SourceExcerptError, OSError):
                    reasons.add(FreshnessReason.FILE_UNVERIFIED)
                check()
                if stop:
                    break
        second = scan()
        if second is not None and (second != paths or (first is not None and second != first)):
            reasons.add(FreshnessReason.INVENTORY_CHANGED)
        try:
            final = _root_observation(snapshot.binding)
            root_ok = final == initial
            if not root_ok:
                reasons.add(FreshnessReason.ROOT_UNVERIFIED)
                if final[:2] == initial[:2]:
                    reasons.add(FreshnessReason.SCAN_INCOMPLETE)
            complete = first is not None and second is not None and root_ok
        except SourceExcerptError:
            reasons.add(FreshnessReason.ROOT_UNVERIFIED)
    check()
    checked = now()
    if checked < started:
        _fail("verification_failed")
    return FreshnessReport(
        snapshot.binding, scope,
        FreshnessStatus.NOT_FRESH if reasons else FreshnessStatus.FRESH,
        tuple(r for r in FreshnessReason if r in reasons), tuple(sorted(observed)),
        complete, started, checked,
    )
