"""Bounded, explicit source saves; never execute or cache document contents.

Checks narrow the replacement race, but are not an OS-wide compare-and-swap
against an uncooperative process replacing directories concurrently.
"""

import codecs
import json
import os
import stat
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from codeviz.source_handles import opened_file_path

from .project_file_policy import (
    decode_text,
    encode_text,
    is_text_file,
    protected_path_reason,
    relative_path,
)

MAX_SOURCE_BYTES = 5_000_000
_MAX_DOCUMENTS = 128


class SourceEditingError(Exception):
    """Only fixed, safe messages cross the desktop boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SourceConflictError(SourceEditingError):
    def __init__(self) -> None:
        super().__init__("SOURCE_CONFLICT", "Source changed externally. Reopen before saving.")


def _project_changed() -> SourceEditingError:
    return SourceEditingError("SOURCE_PROJECT_CHANGED", "The open project has changed.")


def _read_only() -> SourceEditingError:
    return SourceEditingError("SOURCE_READ_ONLY", "This source cannot be edited safely.")


@dataclass(frozen=True, slots=True)
class EditableSource:
    project_path: str
    file_path: str
    text: str
    revision: str
    encoding: str
    newline: str
    read_only_reason: str | None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _writable(info: os.stat_result) -> bool:
    return bool(info.st_mode & 0o222) and not bool(getattr(info, "st_file_attributes", 0) & 1)


def _security(path: Path) -> bytes:
    """Snapshot Windows owner, group and DACL with inheritance protection."""
    if sys.platform != "win32":
        if sys.platform != "linux":
            raise _read_only()
        info = path.stat()
        return json.dumps({
            "uid": info.st_uid, "gid": info.st_gid,
            "attributes": {name: os.getxattr(path, name).hex()
                           for name in sorted(os.listxattr(path))},
        }, sort_keys=True).encode("ascii")
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    get.restype = wintypes.BOOL
    length = wintypes.DWORD()
    get(str(path), 7, None, 0, ctypes.byref(length))
    if not length.value:
        raise OSError("cannot verify source permissions")
    buffer = ctypes.create_string_buffer(length.value)
    if not get(str(path), 7, buffer, length.value, ctypes.byref(length)):
        raise OSError("cannot verify source permissions")
    return buffer.raw


def _copy_security(path: Path, descriptor: bytes) -> None:
    if sys.platform != "win32":
        if sys.platform != "linux":
            raise _read_only()
        payload = json.loads(descriptor)
        os.chown(path, payload["uid"], payload["gid"])
        for name in os.listxattr(path):
            if name not in payload["attributes"]:
                os.removexattr(path, name)
        for name, value in payload["attributes"].items():
            os.setxattr(path, name, bytes.fromhex(value))
        if _security(path) != descriptor:
            raise OSError("source permissions were not preserved")
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    buffer = ctypes.create_string_buffer(descriptor)
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    get_control = api.GetSecurityDescriptorControl
    get_control.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.WORD),
                           ctypes.POINTER(wintypes.DWORD))
    get_control.restype = wintypes.BOOL
    if not get_control(buffer, ctypes.byref(control), ctypes.byref(revision)):
        raise OSError("cannot preserve source permissions")
    set_security = api.SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID)
    set_security.restype = wintypes.BOOL
    flags = 7 | (0x80000000 if control.value & 0x1000 else 0x20000000)
    if not set_security(str(path), flags, buffer):
        raise OSError("cannot preserve source permissions")
    if _security(path) != descriptor:
        raise OSError("source permissions were not preserved")


@dataclass(frozen=True, slots=True)
class _Snapshot:
    digest: str
    identity: tuple[int, int]
    directories: tuple[tuple[int, int], ...]
    mode: int
    security: bytes


@dataclass(frozen=True, slots=True)
class _Baseline:
    file_path: str
    snapshot: _Snapshot
    encoding: str
    newline: str
    read_only_reason: str | None


class SourceEditingService:
    def __init__(self) -> None:
        self._lock = RLock()
        self._root: Path | None = None
        self._root_identity: tuple[int, int] | None = None
        self._epoch = uuid4().hex
        self._documents: OrderedDict[str, _Baseline] = OrderedDict()

    def bind_project(self, root: Path, epoch: str | None = None) -> None:
        with self._lock:
            self._root = root
            self._root_identity = _identity(root.stat())
            self._epoch = epoch if epoch is not None else uuid4().hex
            self._documents.clear()

    def invalidate_paths(self, affected_paths: tuple[str, ...]) -> None:
        with self._lock:
            paths = (tuple(os.path.normcase(path).replace("\\", "/") for path in affected_paths)
                     if sys.platform == "win32" else affected_paths)
            for key, baseline in tuple(self._documents.items()):
                file_path = (os.path.normcase(baseline.file_path).replace("\\", "/")
                             if sys.platform == "win32" else baseline.file_path)
                if any(file_path == path or file_path.startswith(path + "/") for path in paths):
                    del self._documents[key]

    def _project(self, project_path: str) -> Path:
        root = self._root
        if (root is None or not project_path or len(project_path) > 32_768
                or "\x00" in project_path or project_path.replace("/", "\\").startswith("\\\\")):
            raise _project_changed()
        try:
            requested = Path(project_path).resolve(strict=True)
            if requested != root or _identity(root.stat()) != self._root_identity:
                raise _project_changed()
        except (OSError, ValueError):
            raise _project_changed() from None
        return root

    @staticmethod
    def _relative(file_path: str) -> str:
        relative = relative_path(file_path)
        if not is_text_file(relative):
            raise ValueError("invalid source path")
        return relative

    @staticmethod
    def _directories(path: Path) -> tuple[tuple[int, int], ...]:
        chain = (*reversed(path.parent.parents), path.parent)
        identities = []
        for parent in chain:
            info = parent.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                raise _read_only()
            identities.append(_identity(info))
        return tuple(identities)

    def _read(self, root: Path, relative: str) -> tuple[bytes, _Snapshot, str | None]:
        path = root / relative
        directories = self._directories(path)
        info = path.lstat()
        if _linked(info) or not stat.S_ISREG(info.st_mode):
            raise _read_only()
        if info.st_size > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds size limit")
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if (_identity(opened) != _identity(info) or opened_file_path(source) != path
                    or not stat.S_ISREG(opened.st_mode)):
                raise SourceConflictError()
            content = source.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds size limit")
        if (_identity(path.lstat()) != _identity(info)
                or self._directories(path) != directories
                or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise SourceConflictError()
        reason = protected_path_reason(relative)
        if reason is None and after.st_nlink != 1:
            reason = "Files with multiple hard links cannot be edited."
        elif reason is None and not _writable(after):
            reason = "The source file is read-only."
        snapshot = _Snapshot(sha256(content).hexdigest(), _identity(after), directories,
                             after.st_mode, _security(path))
        return content, snapshot, reason

    @staticmethod
    def _decode(content: bytes, relative: str = "source.py") -> tuple[str, str, str, str | None]:
        try:
            return decode_text(relative, content)
        except (SyntaxError, UnicodeError, LookupError, ValueError):
            raise _read_only() from None

    def _document(self, relative: str, content: bytes, snapshot: _Snapshot,
                  reason: str | None, *, fresh: bool = False) -> EditableSource:
        text, encoding, newline, encoding_reason = self._decode(content, relative)
        baseline = _Baseline(relative, snapshot, encoding, newline, reason or encoding_reason)
        revision = next((key for key, value in self._documents.items() if value == baseline), None)
        if fresh or revision is None:
            revision = self._epoch + "." + uuid4().hex
        self._documents[revision] = baseline
        self._documents.move_to_end(revision)
        while len(self._documents) > _MAX_DOCUMENTS:
            self._documents.popitem(last=False)
        return EditableSource(str(self._root), relative, text, revision, encoding,
                              newline, baseline.read_only_reason)

    def open(self, project_path: str, file_path: str) -> EditableSource:
        with self._lock:
            root = self._project(project_path)
            relative = self._relative(file_path)
            try:
                content, snapshot, reason = self._read(root, relative)
                self._project(project_path)
                return self._document(relative, content, snapshot, reason)
            except FileNotFoundError:
                raise ValueError("source path does not exist") from None
            except OSError:
                raise SourceEditingError("SOURCE_READ_ONLY", "Source could not be opened safely.") from None

    def _verify(self, project_path: str, relative: str, baseline: _Baseline) -> None:
        root = self._project(project_path)
        try:
            _, snapshot, reason = self._read(root, relative)
        except (OSError, ValueError, SourceEditingError):
            raise SourceConflictError() from None
        if reason is not None:
            raise _read_only()
        if snapshot != baseline.snapshot:
            raise SourceConflictError()

    def save(self, project_path: str, file_path: str, text: str,
             revision: str) -> EditableSource:
        with self._lock:
            root = self._project(project_path)
            relative = self._relative(file_path)
            if not revision.startswith(self._epoch + "."):
                raise _project_changed()
            baseline = self._documents.get(revision)
            if baseline is None or baseline.file_path != relative:
                raise SourceConflictError()
            if baseline.read_only_reason is not None:
                raise _read_only()
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            ending = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}[baseline.newline]
            committed = normalized.replace("\n", ending)
            try:
                content = encode_text(relative, committed, baseline.encoding)
                if len(content) > MAX_SOURCE_BYTES:
                    raise ValueError("source exceeds size limit")
                decoded, encoding, _, reason = self._decode(content, relative)
                if (decoded != committed or reason is not None
                        or codecs.lookup(encoding).name != codecs.lookup(baseline.encoding).name):
                    raise _read_only()
            except UnicodeError:
                raise _read_only() from None
            self._verify(project_path, relative, baseline)
            path = root / relative
            temporary: Path | None = None
            temp_identity: tuple[int, int] | None = None
            try:
                # Opening for writing without truncation checks actual ACL write access.
                with path.open("r+b") as target:
                    if (_identity(os.fstat(target.fileno())) != baseline.snapshot.identity
                            or opened_file_path(target) != path):
                        raise SourceConflictError()
                descriptor, name = tempfile.mkstemp(prefix=".codeviz-source-", suffix=".tmp", dir=path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as output:
                    temp_identity = _identity(os.fstat(output.fileno()))
                    output.write(content)
                    output.flush()
                    _copy_security(temporary, baseline.snapshot.security)
                    os.chmod(temporary, stat.S_IMODE(baseline.snapshot.mode))
                    os.fsync(output.fileno())
                if (_identity(temporary.lstat()) != temp_identity or temporary.is_symlink()
                        or temporary.stat().st_nlink != 1
                        or stat.S_IMODE(temporary.stat().st_mode) != stat.S_IMODE(baseline.snapshot.mode)
                        or _security(temporary) != baseline.snapshot.security):
                    raise SourceConflictError()
                self._verify(project_path, relative, baseline)
                os.replace(temporary, path)
                temporary = None
            except PermissionError:
                raise SourceEditingError("SOURCE_SAVE_FAILED", "Source could not be saved. Check file permissions.") from None
            except OSError:
                raise SourceEditingError("SOURCE_SAVE_FAILED", "Source could not be saved safely.") from None
            finally:
                if temporary is not None:
                    try:
                        if (self._directories(temporary) == baseline.snapshot.directories
                                and _identity(temporary.lstat()) == temp_identity):
                            temporary.unlink()
                    except (OSError, SourceEditingError):
                        pass
            # No fallible disk read after commit: report the committed baseline.
            snapshot = _Snapshot(sha256(content).hexdigest(), temp_identity or (0, 0),
                                 baseline.snapshot.directories, baseline.snapshot.mode,
                                 baseline.snapshot.security)
            for key in tuple(self._documents):
                if self._documents[key].file_path == relative:
                    del self._documents[key]
            return self._document(relative, content, snapshot, None, fresh=True)
