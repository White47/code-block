"""Discover project Python files without traversing dependencies or symlinks."""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

_IGNORED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages", "data", "datasets", "weights",
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
