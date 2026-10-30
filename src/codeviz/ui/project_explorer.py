"""Bounded project file operations, serialized with saves by DesktopBridge.

Checks narrow external races; they are not an OS-wide filesystem transaction.
"""

import ctypes
import json
import os
import stat
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable
from uuid import uuid4

from codeviz.source_handles import opened_file_path

from . import recycle_bin
from .project_file_policy import (
    is_text_file,
    protected_path_reason,
    relative_path,
    validate_name,
)
from .source_editing import (
    MAX_SOURCE_BYTES,
    SourceEditingError,
    SourceEditingService,
    _identity,
    _linked,
    _writable,
)
from .web_contract import JsonObject, JsonValue

PAGE_SIZE = 200
MAX_DIRECTORY_ENTRIES = 10_000
MAX_SUBTREE_ENTRIES = 5_000
MAX_SUBTREE_DEPTH = 64
MAX_SUBTREE_BYTES = 64 * 1024 * 1024
TOKEN_TTL_SECONDS = 60
_MAX_REVISIONS = 20_000
_MAX_TOKENS = 128
_MAX_CURSORS = 128


class ProjectFileError(SourceEditingError):
    def __init__(self, code: str, message: str, affected_paths: tuple[str, ...] = ()) -> None:
        super().__init__(code, message)
        self.affected_paths = affected_paths


def _conflict() -> ProjectFileError:
    return ProjectFileError("FILE_CONFLICT", "Project files changed. Refresh before trying again.")


def _readonly() -> ProjectFileError:
    return ProjectFileError("FILE_READ_ONLY", "This project entry cannot be changed safely.")


def _limit() -> ProjectFileError:
    return ProjectFileError("FILE_LIMIT", "The directory or subtree exceeds the safe operation limit.")


def _metadata(info: os.stat_result) -> tuple[int, ...]:
    # Windows path stat and CRT fstat expose different ctime semantics.
    # Use identity and actual content hashes rather than that unstable field.
    change_time = info.st_ctime_ns if sys.platform != "win32" else 0
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, change_time, getattr(info, "st_file_attributes", 0))


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class _Stamp:
    digest: str
    parents: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Revision:
    path: str
    stamp: _Stamp
    expires: float


@dataclass(frozen=True)
class _Cursor:
    directory: str
    stamp: _Stamp
    offset: int
    expires: float


@dataclass(frozen=True)
class _Operation:
    path: str
    operation: str
    new_path: str | None
    entry: _Stamp
    subtree: _Stamp
    affected: tuple[str, ...]
    expires: float
    source_directory: _Stamp | None = None
    destination_directory: str | None = None
    destination_stamp: _Stamp | None = None


def _rename_no_replace(source: Path, destination: Path) -> None:
    if sys.platform == "win32":
        # Windows rename fails when the destination already exists.
        os.rename(source, destination)
    elif sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise NotImplementedError("exclusive rename unavailable")
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                           ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), "exclusive rename failed")
    else:
        raise NotImplementedError("exclusive rename unavailable")


class ProjectExplorerService:
    def __init__(self) -> None:
        self._root: Path | None = None
        self._root_identity: tuple[int, int] | None = None
        self._epoch = ""
        self._revisions: OrderedDict[str, _Revision] = OrderedDict()
        self._revision_keys: dict[tuple[str, _Stamp], str] = {}
        self._cursors: OrderedDict[str, _Cursor] = OrderedDict()
        self._tokens: OrderedDict[str, _Operation] = OrderedDict()

    def bind_project(self, root: Path, epoch: str | None = None) -> None:
        identity = _identity(root.lstat())
        self._root = root
        self._root_identity = identity
        self._epoch = epoch if epoch is not None else uuid4().hex
        self._revisions.clear()
        self._revision_keys.clear()
        self._cursors.clear()
        self._tokens.clear()

    def unbind_project(self) -> None:
        self._root = None
        self._root_identity = None
        self._epoch = ""
        self._revisions.clear()
        self._revision_keys.clear()
        self._cursors.clear()
        self._tokens.clear()

    def _project(self, project_path: str, epoch: str | None, *, bootstrap: bool = False) -> Path:
        root = self._root
        try:
            if (root is None or not project_path or len(project_path) > 32768
                    or "\x00" in project_path or project_path.replace("/", "\\").startswith("\\\\")
                    or (epoch is None and not bootstrap) or (epoch is not None and epoch != self._epoch)
                    or Path(project_path).resolve(strict=True) != root
                    or _identity(root.lstat()) != self._root_identity or _linked(root.lstat())):
                raise ValueError("project changed")
            SourceEditingService._directories(root / "unused")
        except (OSError, ValueError, SourceEditingError):
            raise ProjectFileError("FILE_PROJECT_CHANGED", "The open project has changed.") from None
        return root

    @staticmethod
    def _items(path: Path, limit: int = MAX_DIRECTORY_ENTRIES) -> list[tuple[str, os.stat_result]]:
        items: list[tuple[str, os.stat_result]] = []
        with os.scandir(path) as entries:
            for entry in entries:
                if len(items) >= limit:
                    raise _limit()
                # Windows DirEntry.stat can report zero identities and stale
                # directory timestamps. Use a fresh no-follow path stat.
                items.append((entry.name, (path / entry.name).lstat()))
        items.sort(key=lambda item: (not stat.S_ISDIR(item[1].st_mode) or _linked(item[1]),
                                     item[0].casefold(), item[0]))
        return items

    @staticmethod
    def _directory(path: Path) -> _Stamp:
        parents = SourceEditingService._directories(path / "unused")
        before = path.lstat()
        if _linked(before) or not stat.S_ISDIR(before.st_mode):
            raise _readonly()
        items = ProjectExplorerService._items(path)
        after = path.lstat()
        if (_metadata(before) != _metadata(after)
                or parents != SourceEditingService._directories(path / "unused")):
            raise _conflict()
        return _Stamp(_digest((_metadata(after), [(name, _metadata(info)) for name, info in items])),
                      parents)

    @staticmethod
    def _file_digest(path: Path, info: os.stat_result, budget: list[int],
                     captured: bytearray | None = None) -> str:
        if info.st_size > MAX_SUBTREE_BYTES - budget[0]:
            raise _limit()
        parents = SourceEditingService._directories(path)
        fingerprint = sha256()
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if (_metadata(before) != _metadata(info) or opened_file_path(source) != path
                    or not stat.S_ISREG(before.st_mode) or _linked(before)):
                raise _conflict()
            while True:
                content = source.read(min(1024 * 1024, MAX_SUBTREE_BYTES - budget[0] + 1))
                if not content:
                    break
                budget[0] += len(content)
                if budget[0] > MAX_SUBTREE_BYTES:
                    raise _limit()
                if captured is not None:
                    if len(captured) + len(content) > MAX_SOURCE_BYTES:
                        raise _limit()
                    captured.extend(content)
                fingerprint.update(content)
            after = os.fstat(source.fileno())
        if (_metadata(before) != _metadata(after) or _metadata(path.lstat()) != _metadata(info)
                or parents != SourceEditingService._directories(path)):
            raise _conflict()
        return fingerprint.hexdigest()

    def _entry_stamp(self, path: Path, budget: list[int],
                     captured: bytearray | None = None) -> _Stamp:
        parents = SourceEditingService._directories(path)
        info = path.lstat()
        fingerprint = None
        if stat.S_ISREG(info.st_mode) and not _linked(info):
            fingerprint = self._file_digest(path, info, budget, captured)
        return _Stamp(_digest((_metadata(info), fingerprint)), parents)

    def _tree(self, root: Path, relative: str, *, contents: bool,
              inspection_budget: list[int] | None = None) -> tuple[_Stamp, tuple[str, ...]]:
        parents = SourceEditingService._directories(root / relative)
        pending = [(relative, 0)]
        records: list[tuple[str, tuple[int, ...], str | None]] = []
        budget = [0]
        while pending:
            current, depth = pending.pop()
            if len(records) >= MAX_SUBTREE_ENTRIES or depth > MAX_SUBTREE_DEPTH:
                raise _limit()
            if inspection_budget is not None:
                inspection_budget[0] += 1
                if inspection_budget[0] > MAX_DIRECTORY_ENTRIES:
                    raise _limit()
            relative_path(current)
            path = root / current
            if protected_path_reason(current):
                raise _readonly()
            SourceEditingService._directories(path)
            info = path.lstat()
            if _linked(info) or not _writable(info):
                raise _readonly()
            fingerprint = None
            if stat.S_ISDIR(info.st_mode):
                remaining = MAX_SUBTREE_ENTRIES - len(records) - len(pending) - 1
                if inspection_budget is not None:
                    remaining = min(remaining, MAX_DIRECTORY_ENTRIES - inspection_budget[0])
                items = self._items(path, min(MAX_DIRECTORY_ENTRIES, remaining))
                if len(records) + len(pending) + len(items) + 1 > MAX_SUBTREE_ENTRIES:
                    raise _limit()
                pending.extend((f"{current}/{name}", depth + 1) for name, _ in reversed(items))
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise _readonly()
                if contents:
                    fingerprint = self._file_digest(path, info, budget)
                else:
                    budget[0] += info.st_size
                    if budget[0] > MAX_SUBTREE_BYTES:
                        raise _limit()
            else:
                raise _readonly()
            # Content hashes and identity matter; mtimes alone never authorize a mutation.
            records.append((current, _metadata(info), fingerprint))
        if parents != SourceEditingService._directories(root / relative):
            raise _conflict()
        return _Stamp(_digest(records), parents), tuple(record[0] for record in records)

    def _prune(self) -> None:
        now = time.monotonic()
        for key, record in tuple(self._revisions.items()):
            if record.expires <= now:
                del self._revisions[key]
                self._revision_keys.pop((record.path, record.stamp), None)
        for cache in (self._cursors, self._tokens):
            for key in tuple(cache):
                if cache[key].expires <= now:
                    del cache[key]

    def _issue(self, relative: str, stamp: _Stamp) -> str:
        token = self._revision_keys.get((relative, stamp))
        if token is not None:
            self._revisions.move_to_end(token)
            return token
        token = self._epoch + "." + uuid4().hex
        self._revisions[token] = _Revision(relative, stamp, time.monotonic() + TOKEN_TTL_SECONDS)
        self._revision_keys[relative, stamp] = token
        while len(self._revisions) > _MAX_REVISIONS:
            _, record = self._revisions.popitem(last=False)
            self._revision_keys.pop((record.path, record.stamp), None)
        return token

    def _check_revision(self, relative: str, token: str, stamp: _Stamp) -> None:
        self._prune()
        record = self._revisions.get(token)
        if record is None or record.path != relative or record.stamp != stamp:
            raise _conflict()
        self._revisions.move_to_end(token)

    def _describe(self, root: Path, relative: str, budget: list[int],
                  inspection_budget: list[int] | None = None) -> JsonObject:
        path = root / relative
        info = path.lstat()
        kind = ("link" if _linked(info) else "directory" if stat.S_ISDIR(info.st_mode)
                else "file" if stat.S_ISREG(info.st_mode) else "other")
        reason = protected_path_reason(relative)
        can_open = False
        captured = (bytearray() if kind == "file" and info.st_nlink == 1
                    and is_text_file(relative) and info.st_size <= MAX_SOURCE_BYTES else None)
        try:
            relative_path(relative)
            stamp = (_Stamp(_digest(_metadata(info)), SourceEditingService._directories(path))
                     if reason is not None and captured is None
                     else self._entry_stamp(path, budget, captured))
            if captured is not None:
                # Reuse the bytes already read for the revision, under the same
                # page budget. Read permission is independent of write policy.
                try:
                    SourceEditingService._decode(bytes(captured), relative)
                except SourceEditingError:
                    pass
                else:
                    can_open = True
        except (ValueError, SourceEditingError, OSError):
            stamp = _Stamp(_digest(_metadata(info)), SourceEditingService._directories(path))
            reason = "This entry exceeds the safe file policy or operation limit."
        if kind in {"link", "other"}:
            reason = "Links and special files cannot be opened or changed."
        elif kind == "file" and info.st_nlink != 1:
            reason = "Files with multiple hard links cannot be changed."
        elif not _writable(info):
            reason = "The project entry is read-only."
        mutable = reason is None
        can_create = kind == "directory" and mutable
        if kind == "directory" and mutable:
            try:
                self._tree(root, relative, contents=False, inspection_budget=inspection_budget)
            except (SourceEditingError, ValueError, OSError):
                reason = "This directory contains protected entries or exceeds the safe operation limit."
                mutable = False
        return {"path": relative, "name": path.name, "kind": kind,
                "revision": self._issue(relative, stamp), "readOnlyReason": reason,
                "canOpen": can_open,
                "canCreate": can_create, "canRename": mutable,
                "canDelete": mutable and sys.platform == "win32"}

    def list_directory(self, project_path: str, directory: str, epoch: str | None = None,
                       cursor: str | None = None) -> JsonObject:
        relative = relative_path(directory, allow_root=True)
        root = self._project(project_path, epoch, bootstrap=relative == "" and cursor is None)
        self._prune()
        path = root / relative
        try:
            stamp = self._directory(path)
            items = self._items(path)
            offset = 0
            if cursor is not None:
                page = self._cursors.get(cursor)
                if page is None or page.directory != relative or page.stamp != stamp:
                    raise _conflict()
                offset = page.offset
                self._cursors.move_to_end(cursor)
            budget = [0]
            inspection_budget = [0]
            entries: list[JsonValue] = [self._describe(root, f"{relative}/{name}" if relative else name,
                                                       budget, inspection_budget)
                                      for name, _ in items[offset:offset + PAGE_SIZE]]
            if self._directory(path) != stamp:
                raise _conflict()
            self._project(project_path, epoch, bootstrap=relative == "" and cursor is None)
            next_cursor = None
            if offset + PAGE_SIZE < len(items):
                next_cursor = self._epoch + "." + uuid4().hex
                self._cursors[next_cursor] = _Cursor(relative, stamp, offset + PAGE_SIZE,
                                                    time.monotonic() + TOKEN_TTL_SECONDS)
                while len(self._cursors) > _MAX_CURSORS:
                    self._cursors.popitem(last=False)
            return {"projectPath": str(root), "projectEpoch": self._epoch, "directory": relative,
                    "directoryRevision": self._issue(relative, stamp), "entries": entries,
                    "nextCursor": next_cursor}
        except OSError:
            raise ProjectFileError("FILE_READ_FAILED", "The directory could not be read safely. Refresh the tree.") from None

    def create_entry(self, project_path: str, epoch: str, parent: str, parent_revision: str,
                     name: str, kind: str, *,
                     before_mutation: Callable[[], None] | None = None) -> JsonObject:
        root = self._project(project_path, epoch)
        parent = relative_path(parent, allow_root=True)
        validate_name(name)
        relative = f"{parent}/{name}" if parent else name
        if kind not in {"file", "directory"}:
            raise ValueError("invalid project entry kind")
        if protected_path_reason(relative):
            raise _readonly()
        attempted = False
        created = False
        try:
            directory = root / parent
            stamp = self._directory(directory)
            self._check_revision(parent, parent_revision, stamp)
            if not _writable(directory.lstat()):
                raise _readonly()
            if any(existing.casefold() == name.casefold() for existing, _ in self._items(directory)):
                raise _conflict()
            self._project(project_path, epoch)
            if self._directory(directory) != stamp:
                raise _conflict()
            target = root / relative
            if before_mutation is not None:
                before_mutation()
            attempted = True
            if kind == "file":
                handle = target.open("xb")
                created = True
                with handle:
                    pass
            else:
                target.mkdir()
                created = True
            return {"operation": "create", "oldPath": None, "newPath": relative,
                    "affectedPaths": [relative]}
        except Exception as error:
            # Once a native create is entered, an unknown failure may follow a
            # disk mutation. A returned handle proves creation before enter/close.
            if attempted and (created or not isinstance(error, (
                    FileExistsError, PermissionError, FileNotFoundError, NotADirectoryError))):
                raise ProjectFileError(
                    "FILE_OPERATION_FAILED",
                    "The operation did not complete safely. Refresh the tree before proceeding.",
                    (relative,)) from None
            if not isinstance(error, OSError):
                raise
            raise ProjectFileError("FILE_CREATE_FAILED", "The entry could not be created. Refresh the tree.") from None

    def _destination(self, root: Path, relative: str, new_name: str) -> str:
        validate_name(new_name)
        parent, _, old_name = relative.rpartition("/")
        if old_name.casefold() == new_name.casefold():
            raise ValueError("case-only or identical rename is unsupported")
        destination = f"{parent}/{new_name}" if parent else new_name
        if protected_path_reason(destination):
            raise _readonly()
        if any(name.casefold() == new_name.casefold() for name, _ in self._items(root / parent)):
            raise _conflict()
        return destination

    def _move_destination(self, root: Path, relative: str, directory: str) -> str:
        if (directory.casefold() == relative.casefold()
                or directory.casefold().startswith(relative.casefold() + "/")):
            raise ValueError("cannot move into the source or its descendant")
        parent, _, basename = relative.rpartition("/")
        source = root / relative
        target = root / directory
        SourceEditingService._directories(source)
        SourceEditingService._directories(target / "unused")
        source_info = source.lstat()
        target_info = target.lstat()
        if (protected_path_reason(directory) or _linked(target_info)
                or not stat.S_ISDIR(target_info.st_mode) or not _writable(target_info)
                or not _writable((root / parent).lstat())
                or source_info.st_dev != target_info.st_dev):
            raise _readonly()
        # Preserve the disk spelling, including when Windows resolved a case alias.
        actual = next((name for name, info in self._items(root / parent)
                       if name.casefold() == basename.casefold()
                       and _identity(info) == _identity(source_info)), None)
        if actual is None:
            raise _conflict()
        destination = relative_path(f"{directory}/{actual}" if directory else actual)
        if destination.casefold() == relative.casefold():
            raise ValueError("identical move is unsupported")
        if protected_path_reason(destination):
            raise _readonly()
        if any(name.casefold() == actual.casefold() for name, _ in self._items(target)):
            raise _conflict()
        return destination

    def prepare_operation(self, project_path: str, epoch: str, relative: str, revision: str,
                          operation: str, new_name: str | None = None,
                          destination_directory: str | None = None,
                          destination_revision: str | None = None) -> JsonObject:
        root = self._project(project_path, epoch)
        relative = relative_path(relative)
        if operation not in {"rename", "move", "delete"}:
            raise ValueError("unsupported file operation")
        if operation == "move":
            if (new_name is not None or destination_directory is None
                    or destination_revision is None):
                raise ValueError("move requires only a destination directory and revision")
            destination_directory = relative_path(destination_directory, allow_root=True)
        elif destination_directory is not None or destination_revision is not None:
            raise ValueError("unexpected move target")
        elif (operation == "rename") != (new_name is not None):
            raise ValueError("unexpected or missing rename target")
        try:
            entry = self._entry_stamp(root / relative, [0])
            self._check_revision(relative, revision, entry)
            subtree, affected = self._tree(root, relative, contents=True)
            destination = (self._destination(root, relative, new_name) if new_name is not None
                           and operation == "rename" else None)
            if operation == "rename" and destination is None:
                raise ValueError("rename requires a new name")
            source_directory = None
            destination_stamp = None
            if operation == "move" and destination_directory is not None:
                source_directory = self._directory(root / relative.rpartition("/")[0])
                destination_stamp = self._directory(root / destination_directory)
                assert destination_revision is not None
                self._check_revision(destination_directory, destination_revision, destination_stamp)
                destination = self._move_destination(root, relative, destination_directory)
            self._project(project_path, epoch)
            if (self._entry_stamp(root / relative, [0]) != entry
                    or self._tree(root, relative, contents=True)[0] != subtree):
                raise _conflict()
            if operation == "move" and destination_directory is not None:
                if (self._directory(root / relative.rpartition("/")[0]) != source_directory
                        or self._directory(root / destination_directory) != destination_stamp
                        or self._move_destination(root, relative, destination_directory) != destination):
                    raise _conflict()
            token = self._epoch + "." + uuid4().hex
            self._tokens[token] = _Operation(relative, operation, destination, entry, subtree,
                                              affected, time.monotonic() + TOKEN_TTL_SECONDS,
                                              source_directory, destination_directory, destination_stamp)
            while len(self._tokens) > _MAX_TOKENS:
                self._tokens.popitem(last=False)
            return {"operationToken": token, "operation": operation, "path": relative,
                    "newPath": destination, "affectedPaths": list(affected)}
        except OSError:
            raise ProjectFileError("FILE_READ_FAILED", "The operation could not be prepared safely. Refresh the tree.") from None

    def apply_operation(self, project_path: str, epoch: str, token: str, *,
                        before_mutation: Callable[[], None] | None = None) -> JsonObject:
        root = self._project(project_path, epoch)
        self._prune()
        prepared = self._tokens.pop(token, None)
        if prepared is None:
            raise _conflict()
        try:
            for _ in range(2):
                self._project(project_path, epoch)
                if (self._entry_stamp(root / prepared.path, [0]) != prepared.entry
                        or self._tree(root, prepared.path, contents=True)[0] != prepared.subtree):
                    raise _conflict()
                if prepared.operation == "rename" and prepared.new_path is not None:
                    self._destination(root, prepared.path, Path(prepared.new_path).name)
                elif prepared.operation == "move":
                    if (prepared.destination_directory is None
                            or self._directory(root / prepared.path.rpartition("/")[0])
                            != prepared.source_directory
                            or self._directory(root / prepared.destination_directory)
                            != prepared.destination_stamp
                            or self._move_destination(root, prepared.path,
                                                      prepared.destination_directory)
                            != prepared.new_path):
                        raise _conflict()
        except OSError:
            raise _conflict() from None
        if (prepared.operation not in {"rename", "move", "delete"}
                or (prepared.operation in {"rename", "move"} and prepared.new_path is None)):
            raise ValueError("unsupported prepared operation")
        if before_mutation is not None:
            before_mutation()
        try:
            if prepared.operation == "rename" and prepared.new_path is not None:
                _rename_no_replace(root / prepared.path, root / prepared.new_path)
            elif prepared.operation == "move" and prepared.new_path is not None:
                _rename_no_replace(root / prepared.path, root / prepared.new_path)
            elif prepared.operation == "delete":
                recycle_bin.recycle_path(root / prepared.path)
            else:
                raise ValueError("unsupported prepared operation")
        except Exception:
            # Native calls can partly mutate before failing. Consume the token,
            # invalidate affected documents, and tell the caller to refresh.
            uncertain_paths = prepared.affected
            if prepared.operation == "move" and prepared.new_path is not None:
                uncertain_paths = tuple(dict.fromkeys((*prepared.affected, *(
                    prepared.new_path + path[len(prepared.path):]
                    for path in prepared.affected
                ))))
            raise ProjectFileError("FILE_OPERATION_FAILED",
                                   "The operation did not complete safely. Refresh the tree before proceeding.",
                                   uncertain_paths) from None
        return {"operation": prepared.operation, "oldPath": prepared.path,
                "newPath": prepared.new_path, "affectedPaths": list(prepared.affected)}
