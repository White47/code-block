"""Opt-in, closed primitive observations. Never an authority or response store."""

from __future__ import annotations

import json
import os
import secrets
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from queue import Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Iterator

from ._delta_reasons import REASONS, DeltaRejection, validated_rejection

BASENAME = "closed-validation-diagnostics.json"
_CODES = ("invalid_schema", "unprovided_id", "invalid_evidence", "relation_required",
          "invalid_relation", "static_requires_ids")
_SHAPE = (
    "payloadSizeValid", "utf8Valid", "bomAbsent", "jsonValid", "duplicateKeysAbsent",
    "finiteNumbersOnly", "rootObject", "rootKeysExact", "sectionsArray", "sectionCount",
    "sectionOrderValid", "totalClaimsWithinLimit", "sectionKeysExact", "claimsArray",
    "claimKeysExact", "textValid", "basisEnumValid", "evidenceIdsValid", "relationIdValid",
    "unknownMarkerPresent",
)
_CURRENT: ContextVar[Attempt | None] = ContextVar("closed_validation_observer", default=None)


def _index(value: object, maximum: int) -> bool:
    return value is None or (type(value) is int and 0 <= value <= maximum)


def _position(section: object, claim: object) -> bool:
    return (_index(section, 6) and _index(claim, 19)
            and (section is not None or claim is None))


def _keys(value: object, expected: set[str] | tuple[str, ...]) -> bool:
    return (type(value) is dict and all(type(key) is str for key in value)
            and set(value) == set(expected))


class Attempt:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.stage = "parse"
        self.shape: dict[str, bool | int | None] = dict.fromkeys(_SHAPE)
        self.position: tuple[int | None, int | None] = (None, None)
        self.failure: tuple[int | None, int | None] | None = None
        self.issues: list[dict[str, Any]] = []
        self.counts = dict.fromkeys(_CODES, 0)
        self.truncated = False

    def finish(self, stage: str, issues: tuple[tuple[str, int | None, int | None], ...]) -> None:
        if type(stage) is not str or stage not in ("parse", "evidence") or type(issues) is not tuple:
            return
        self.stage = stage
        for entry in issues[:61]:
            if (type(entry) is not tuple or len(entry) != 3
                    or type(entry[0]) is not str or entry[0] not in _CODES
                    or not _position(entry[1], entry[2])):
                continue
            code, section, claim = entry
            self.counts[code] = min(65537, self.counts[code] + 1)
            if len(self.issues) < 8:
                self.issues.append({"code": code, "section": section, "claim": claim})
            else:
                self.truncated = True

    def record(self) -> dict[str, Any]:
        return {"phase": self.phase, "stage": self.stage, "shape": self.shape.copy(),
                "shapeFailureLocation": None if self.failure is None else list(self.failure),
                "issues": self.issues.copy(), "issueCounts": self.counts.copy(),
                "issuesTruncated": self.truncated}


@contextmanager
def observation(attempt: Attempt | None) -> Iterator[None]:
    if type(attempt) is not Attempt:
        yield
        return
    token = _CURRENT.set(attempt)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def location(section: int | None, claim: int | None) -> None:
    current = _CURRENT.get()
    if current is not None and _position(section, claim):
        current.position = (section, claim)


def fact(name: str, value: object) -> None:
    current = _CURRENT.get()
    if current is None or type(name) is not str or name not in _SHAPE:
        return
    if name == "sectionCount":
        if type(value) is int and value >= 0:
            current.shape[name] = min(value, 65537)
    elif type(value) is bool:
        if current.shape[name] is not False:
            current.shape[name] = value
        if not value and current.failure is None:
            current.failure = current.position


def encode_records(records: list[dict[str, Any]]) -> bytes | None:
    """Validate again at the persistence boundary; never stringify unknown values."""
    try:
        if type(records) is not list or len(records) > 2:
            return None
        for record in records:
            if _keys(record, {"phase", "stage", "reason"}):
                if (type(record["phase"]) is not str
                        or record["phase"] not in ("ordinary", "final", "repair")
                        or type(record["stage"]) is not str or record["stage"] != "transport"
                        or type(record["reason"]) is not str or record["reason"] not in REASONS):
                    return None
                continue
            if not _keys(record, {
                    "phase", "stage", "shape", "shapeFailureLocation", "issues",
                    "issueCounts", "issuesTruncated"}):
                return None
            if (type(record["phase"]) is not str or record["phase"] not in ("final", "repair")
                    or type(record["stage"]) is not str or record["stage"] not in ("parse", "evidence")
                    or type(record["issuesTruncated"]) is not bool):
                return None
            shape = record["shape"]
            if not _keys(shape, _SHAPE):
                return None
            for name, value in shape.items():
                if value is None:
                    continue
                if name == "sectionCount":
                    if type(value) is not int or not 0 <= value <= 65537:
                        return None
                elif type(value) is not bool:
                    return None
            failure = record["shapeFailureLocation"]
            if failure is not None and (type(failure) is not list or len(failure) != 2
                                        or not _position(*failure)):
                return None
            counts = record["issueCounts"]
            if (not _keys(counts, _CODES)
                    or any(type(v) is not int or not 0 <= v <= 65537 for v in counts.values())):
                return None
            issues = record["issues"]
            if type(issues) is not list or len(issues) > 8:
                return None
            for issue in issues:
                if (not _keys(issue, {"code", "section", "claim"})
                        or type(issue["code"]) is not str or issue["code"] not in _CODES
                        or not _position(issue["section"], issue["claim"])):
                    return None
        raw = json.dumps(records, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) <= 4096:
            return raw
        # Drop whole detail entries, never bytes, while retaining aggregate counts.
        reduced = [r if r["stage"] == "transport"
                   else {**r, "issues": [], "issuesTruncated": True} for r in records]
        raw = json.dumps(reduced, ensure_ascii=True, separators=(",", ":")).encode()
        return raw if len(raw) <= 4096 else None
    except Exception:
        return None


class _Transport:
    def __init__(self, phase: str, value: DeltaRejection) -> None:
        self.phase = phase
        self.value = value

    def record(self) -> dict[str, Any]:
        return {"phase": self.phase, "stage": "transport", "reason": self.value.reason}


class Capture:
    def __init__(self) -> None:
        self.attempts: list[Attempt | _Transport] = []
        self.closed = False
        self._transport_seen = False

    def transport(self, phase: str, value: object) -> None:
        trusted = validated_rejection(value)
        if (self.closed or self._transport_seen or type(phase) is not str
                or phase not in ("ordinary", "final", "repair") or trusted is None):
            return
        self._transport_seen = True
        if len(self.attempts) < 2:
            self.attempts.append(_Transport(phase, trusted))

    def begin(self, phase: str) -> Attempt | None:
        if (self.closed or type(phase) is not str or phase not in ("final", "repair")
                or len(self.attempts) >= 2
                or any(a.phase == phase for a in self.attempts)):
            return None
        attempt = Attempt(phase)
        self.attempts.append(attempt)
        return attempt

    def encode(self) -> bytes | None:
        return encode_records([a.record() for a in self.attempts])


def _safe(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    return (not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))


@contextmanager
def _pin_windows(root: Path) -> Iterator[None]:
    """Prevent directory rename/reparse replacement throughout pathname I/O."""
    import ctypes
    from ctypes import wintypes

    load_library = getattr(ctypes, "WinDLL", None)
    if load_library is None:
        raise OSError("Windows directory pin unavailable")
    kernel = load_library("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    handles = []
    try:
        for directory in reversed((root, *root.parents)):
            directory.mkdir(exist_ok=True)
            # LIST_DIRECTORY is required for sharing restrictions: attribute-only
            # opens do not pin against rename. Keep SHARE_READ only and no-follow.
            handle = create(str(directory), 0x81, 1, None, 3, 0x02200000, None)
            if handle is None or handle == ctypes.c_void_p(-1).value:
                error_code = getattr(ctypes, "get_last_error", lambda: 0)()
                raise OSError(error_code, "Directory pin unavailable")
            handles.append(handle)
            attributes = AttributeTag()
            if (not info(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes))
                    or attributes.attributes & 0x400 or not attributes.attributes & 0x10):
                raise OSError("Unsafe diagnostic directory")
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def _nt_set_info(handle: Any, value: Any, size: int, kind: int) -> bool:
    import ctypes
    from ctypes import wintypes

    class IoStatus(ctypes.Structure):
        _fields_ = [("status_or_pointer", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    load_library = getattr(ctypes, "WinDLL", None)
    if load_library is None:
        raise OSError("Windows file operation unavailable")
    ntdll = load_library("ntdll")
    operation = ntdll.NtSetInformationFile
    operation.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID,
                          wintypes.ULONG, ctypes.c_int]
    operation.restype = ctypes.c_long
    status = IoStatus()
    return bool(operation(handle, ctypes.byref(status), ctypes.byref(value), size, kind) == 0)


def _rename_windows(handle: Any) -> bool:
    """Rename the held source to the fixed basename in its own parent directory."""
    import ctypes
    from ctypes import wintypes

    class Rename(ctypes.Structure):
        _fields_ = [("replace", ctypes.c_ubyte), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]

    encoded = BASENAME.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(Rename.name.offset + len(encoded))
    value = Rename.from_buffer(buffer)
    value.replace = 1
    value.root = None
    value.length = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + Rename.name.offset, encoded, len(encoded))
    return _nt_set_info(handle, buffer, len(buffer), 10)


def _write_windows(root: Path, raw: bytes) -> bool:
    import ctypes
    from ctypes import wintypes

    handle = None
    renamed = False
    load_library = getattr(ctypes, "WinDLL", None)
    if load_library is None:
        raise OSError("Windows file operation unavailable")
    kernel = load_library("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    write = kernel.WriteFile
    write.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    write.restype = wintypes.BOOL
    try:
        destination = root / BASENAME
        if not _safe(destination, directory=False):
            return False
        temporary = root / (".closed-validation-" + secrets.token_hex(12))
        # CREATE_NEW, WRITE|DELETE, no sharing: retain this identity through rename.
        handle = create(str(temporary), 0x40010000, 0, None, 1, 0x100, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            handle = None
            return False
        buffer = ctypes.create_string_buffer(raw)
        written = wintypes.DWORD()
        if not write(handle, buffer, len(raw), ctypes.byref(written), None) or written.value != len(raw):
            return False
        renamed = _rename_windows(handle)
        return renamed
    except Exception:
        return False
    finally:
        if handle is not None:
            try:
                if not renamed:
                    # Dispose the same held file object; do not reopen a pathname.
                    deletion = ctypes.c_ubyte(1)
                    _nt_set_info(handle, deletion, ctypes.sizeof(deletion), 13)
            finally:
                close(handle)


def _write_posix(root: Path, raw: bytes) -> bool:
    """Resolve each component without symlinks, then use only rooted dir_fd I/O."""
    directory_fd: int | None = None
    temporary: str | None = None
    try:
        directory_flag = getattr(os, "O_DIRECTORY", None)
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if type(directory_flag) is not int or type(nofollow_flag) is not int:
            return False
        flags = os.O_RDONLY | directory_flag | nofollow_flag
        directory_fd = os.open(root.anchor, flags)
        for component in root.parts[1:]:
            try:
                next_fd = os.open(component, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        temporary = ".closed-validation-" + secrets.token_hex(12)
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                          | nofollow_flag, 0o600, dir_fd=directory_fd)
        with os.fdopen(file_fd, "wb") as stream:
            stream.write(raw)
        try:
            destination = os.stat(BASENAME, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(destination.st_mode):
                return False
        os.replace(temporary, BASENAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        return True
    except Exception:
        return False
    finally:
        if directory_fd is not None:
            try:
                if temporary is not None:
                    os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)


def _write(root: Path, raw: bytes) -> bool:
    try:
        if type(raw) is not bytes or len(raw) > 4096:
            return False
        if not all(_safe(parent, directory=True) for parent in (root, *root.parents)):
            return False
        if os.name == "nt":
            with _pin_windows(root):
                return _write_windows(root, raw)
        return _write_posix(root, raw)
    except Exception:
        return False


class DiagnosticsSink:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = Lock()
        self._capture: Capture | None = None
        self._published = False
        self._queue: Queue[bytes] = Queue(maxsize=1)
        self._written = Event()
        Thread(target=self._worker, name="closed-validation-sink", daemon=True).start()

    def claim(self) -> Capture | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self._capture is not None:
                return None
            self._capture = Capture()
            return self._capture
        finally:
            self._lock.release()

    def publish(self, capture: Capture) -> None:
        if capture is not self._capture or self._published:
            return
        self._published = True
        raw = capture.encode()
        capture.closed = True
        capture.attempts.clear()
        if raw is not None:
            try:
                self._queue.put_nowait(raw)
            except Full:
                pass

    def _worker(self) -> None:
        try:
            raw = self._queue.get()
            _write(self._root, raw)
        except Exception:
            pass
        finally:
            self._written.set()


def startup(root: Path, enabled: str | None) -> DiagnosticsSink | None:
    if type(enabled) is not str or enabled != "1":
        return None
    try:
        root = root.absolute()
        if not _write(root, b"[]"):
            return None
        return DiagnosticsSink(root)
    except Exception:
        return None
