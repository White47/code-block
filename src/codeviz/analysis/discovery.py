"""Discover project Python files without traversing dependencies or symlinks."""

import os
import stat
import unicodedata
from collections.abc import Callable, Generator
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath

_IGNORED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages", "weights",
    "checkpoints", "runs", "wandb", "work",
})


def _normalize_ignore(path: str) -> str:
    if not path or "\x00" in path or PureWindowsPath(path).drive:
        raise ValueError("ignore path must be project-relative")
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or not parsed.parts or ".." in parsed.parts:
        raise ValueError("ignore path must stay inside the project")
    return parsed.as_posix()


def _matches_ignore(relative_path: str, ignored: tuple[str, ...]) -> bool:
    return any(relative_path == path or relative_path.startswith(f"{path}/") for path in ignored)


def discover_python_files(project_root: Path, ignore_paths: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return sorted project-relative `.py` paths; ignore paths are literal prefixes."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    ignored = tuple(_normalize_ignore(path) for path in ignore_paths)
    found: list[str] = []

    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        names[:] = [
            name for name in names
            if name.casefold() not in _IGNORED_DIRECTORIES
            and not (parent / name).is_symlink()
            and not _matches_ignore((parent / name).relative_to(root).as_posix(), ignored)
        ]
        for name in filenames:
            path = parent / name
            if path.suffix.casefold() != ".py" or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if not _matches_ignore(relative, ignored):
                found.append(relative)

    return tuple(sorted(found))


_SCAN_MESSAGES = {
    "ROOT_UNVERIFIED": "Project root could not be verified.",
    "SCAN_INCOMPLETE": "Project source scan is incomplete.",
    "LIMIT_EXCEEDED": "Project source scan limit exceeded.",
}
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"}
)


class DiscoveryScanError(ValueError):
    """Fixed public scan failure without native error or path details."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _SCAN_MESSAGES else "SCAN_INCOMPLETE"
        self.message = _SCAN_MESSAGES[self.code]
        super().__init__(self.message)


def _linked(info: os.stat_result) -> bool:
    return bool(stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400)


def _scan_state(info: os.stat_result, *, directory: bool,
                code: str = "SCAN_INCOMPLETE") -> tuple[int, int, int, int]:
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if _linked(info) or not valid_type or not info.st_dev or not info.st_ino:
        raise DiscoveryScanError(code) from None
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def _scan_observe(path: Path, *, directory: bool,
                  code: str = "SCAN_INCOMPLETE") -> tuple[int, int, int, int]:
    try:
        info = path.lstat()
    except OSError:
        raise DiscoveryScanError(code) from None
    return _scan_state(info, directory=directory, code=code)


def _strict_candidate_path(relative: str) -> None:
    # Apply Windows alias restrictions even on case-sensitive host filesystems.
    if not relative or PureWindowsPath(relative).drive or relative.startswith("/"):
        raise DiscoveryScanError("SCAN_INCOMPLETE") from None
    for part in relative.split("/"):
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or any(c in '<>:"\\|?*' or unicodedata.category(c) == "Cc"
                       or 0xD800 <= ord(c) <= 0xDFFF for c in part)
                or part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_DEVICES):
            raise DiscoveryScanError("SCAN_INCOMPLETE") from None


def _strict_entries(directory: Path) -> Generator[os.DirEntry[str], None, None]:
    try:
        with os.scandir(directory) as entries:
            yield from entries
    except OSError:
        raise DiscoveryScanError("SCAN_INCOMPLETE") from None


def discover_python_files_strict(
    project_root: Path, ignore_paths: tuple[str, ...] = (), *,
    max_entries: int = 200_000, max_directories: int = 20_000,
    max_files: int = 200_000, checkpoint: Callable[[], None] | None = None,
) -> tuple[str, ...]:
    """Return a complete bounded inventory, or fail without a partial result."""
    for limit in (max_entries, max_directories, max_files):
        if type(limit) is not int or limit < 0:
            raise DiscoveryScanError("LIMIT_EXCEEDED") from None
    ignored = tuple(_normalize_ignore(path) for path in ignore_paths)
    # Do not resolve away a root symlink or junction before inspecting it.
    root = Path(project_root).absolute()
    if checkpoint is not None:
        checkpoint()
    initial = _scan_observe(root, directory=True, code="ROOT_UNVERIFIED")
    pending = [(root, "", initial)]
    directories = 1
    entries_count = 0
    found: list[str] = []
    seen: set[str] = set()
    observations: list[tuple[Path, bool, tuple[int, int, int, int]]] = []
    if directories > max_directories:
        raise DiscoveryScanError("LIMIT_EXCEEDED") from None

    while pending:
        directory, prefix, expected = pending.pop()
        if checkpoint is not None:
            checkpoint()
        before = _scan_observe(directory, directory=True)
        if before != expected:
            raise DiscoveryScanError("SCAN_INCOMPLETE") from None
        with closing(_strict_entries(directory)) as entries:
            for entry in entries:
                entries_count += 1
                if entries_count > max_entries:
                    raise DiscoveryScanError("LIMIT_EXCEEDED") from None
                if checkpoint is not None:
                    checkpoint()
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    raise DiscoveryScanError("SCAN_INCOMPLETE") from None
                if _linked(info):
                    continue
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                is_directory = stat.S_ISDIR(info.st_mode)
                if _matches_ignore(relative, ignored):
                    continue
                if is_directory and entry.name.casefold() in _IGNORED_DIRECTORIES:
                    continue
                if not is_directory and Path(entry.name).suffix.casefold() != ".py":
                    continue
                _strict_candidate_path(relative)
                # Windows uses case-insensitive canonical names; elsewhere exact duplicates.
                key = relative.casefold() if os.name == "nt" else relative
                if key in seen:
                    raise DiscoveryScanError("SCAN_INCOMPLETE") from None
                seen.add(key)
                path = directory / entry.name
                if os.name == "nt":
                    # Windows DirEntry.stat omits dev/ino. A no-follow pathname
                    # observation must supply the identity, never invent one.
                    state = _scan_observe(path, directory=is_directory)
                    if ((info.st_dev and info.st_dev != state[0])
                            or (info.st_ino and info.st_ino != state[1])):
                        raise DiscoveryScanError("SCAN_INCOMPLETE") from None
                else:
                    state = _scan_state(info, directory=is_directory)
                if is_directory:
                    directories += 1
                    if directories > max_directories:
                        raise DiscoveryScanError("LIMIT_EXCEEDED") from None
                    pending.append((path, relative, state))
                else:
                    if len(found) >= max_files:
                        raise DiscoveryScanError("LIMIT_EXCEEDED") from None
                    found.append(relative)
                    observations.append((path, False, state))
        if checkpoint is not None:
            checkpoint()
        if _scan_observe(directory, directory=True) != before:
            raise DiscoveryScanError("SCAN_INCOMPLETE") from None
        observations.append((directory, True, before))

    # Recheck earlier directories too, including the root after all descendants.
    for path, is_directory, expected in observations:
        if checkpoint is not None:
            checkpoint()
        if _scan_observe(path, directory=is_directory) != expected:
            raise DiscoveryScanError("SCAN_INCOMPLETE") from None
    return tuple(sorted(found))
