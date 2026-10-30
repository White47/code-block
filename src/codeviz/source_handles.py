"""Bounded source reads verified against native handles and pathname observations.

These checks do not provide an adversarial ABA sandbox or an I/O deadline.
"""

import os
import stat
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

_MESSAGES = {
    "INVALID_SOURCE_PATH": "source path is invalid",
    "UNSUPPORTED_PLATFORM": "source verification is unsupported on this platform",
    "SYMLINK_UNSUPPORTED": "linked source paths are not supported",
    "NOT_REGULAR_FILE": "source is not a regular file",
    "FILE_TOO_LARGE": "source file exceeds size limit",
    "SOURCE_CHANGED": "source changed during reading",
    "SOURCE_VERIFICATION_FAILED": "source verification failed",
    "READ_ERROR": "source could not be read",
}


@dataclass(frozen=True, slots=True)
class SourceRead:
    raw_bytes: bytes
    content_hash: str


class SourceReadError(ValueError):
    """A stable public error without native errors or private path details."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


def _windows_handle_path(handle: int) -> Path:
    if sys.platform != "win32":
        raise SourceReadError("UNSUPPORTED_PLATFORM") from None
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    api.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    api.restype = wintypes.DWORD
    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = api(handle, buffer, size, 0)
        if length == 0:
            raise OSError("cannot verify opened source path")
        if length < size:
            raw = buffer.value
            if raw.startswith("\\\\?\\UNC\\"):
                return Path("\\\\" + raw[8:])
            if raw.startswith("\\\\?\\"):
                return Path(raw[4:])
            raise ValueError("opened source path is not a DOS path")
        size = length + 1
    raise ValueError("opened source path is too long to verify")


def opened_file_path(source: BinaryIO) -> Path:
    """Resolve the opened handle, not a name that may have changed before open."""
    if sys.platform == "win32":
        import msvcrt

        return _windows_handle_path(msvcrt.get_osfhandle(source.fileno()))
    if sys.platform == "linux":
        return (Path("/proc/self/fd") / str(source.fileno())).resolve(strict=True)
    raise ValueError("opened source path cannot be verified on this platform")


def _components(relative: str) -> list[str]:
    if (not isinstance(relative, str) or not relative or "\0" in relative
            or "\\" in relative or PureWindowsPath(relative).drive
            or relative.startswith("/")):
        raise SourceReadError("INVALID_SOURCE_PATH") from None
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SourceReadError("INVALID_SOURCE_PATH") from None
    if sys.platform == "win32":
        devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        devices.update(f"{prefix}{number}" for prefix in ("COM", "LPT")
                       for number in "123456789¹²³")
        if any(":" in part or part.endswith((".", " "))
               or part.split(".", 1)[0].upper() in devices for part in parts):
            raise SourceReadError("INVALID_SOURCE_PATH") from None
    return parts


def _identity(info: os.stat_result) -> tuple[int, int]:
    if not info.st_ino or not info.st_dev:
        raise SourceReadError("SOURCE_VERIFICATION_FAILED") from None
    return info.st_dev, info.st_ino


def _state(info: os.stat_result) -> tuple[int, int, int, int, int]:
    device, inode = _identity(info)
    return device, inode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _matches_opened(before: os.stat_result, opened: os.stat_result) -> bool:
    if sys.platform == "win32":
        # Observed on native Windows Python 3.12.14: pathname ctime matched
        # birth time while fstat ctime differed; this is not a runtime guarantee.
        # Compare birth time across APIs and ctime within each API; there is no
        # comparable pre-open ChangeTime baseline to prove initial stability.
        return (_state(before)[:4] == _state(opened)[:4]
                and getattr(before, "st_birthtime_ns", before.st_ctime_ns)
                == getattr(opened, "st_birthtime_ns", opened.st_ctime_ns))
    return _state(before) == _state(opened)


def _unlinked(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise SourceReadError("SYMLINK_UNSUPPORTED") from None
    _identity(info)


def _observe(root: Path, parts: list[str], max_bytes: int) -> list[os.stat_result]:
    current = root
    observations = []
    for index in range(len(parts) + 1):
        info = current.lstat()
        _unlinked(info)
        if index < len(parts):
            if not stat.S_ISDIR(info.st_mode):
                raise SourceReadError("NOT_REGULAR_FILE") from None
            current = current / parts[index]
        else:
            if not stat.S_ISREG(info.st_mode):
                raise SourceReadError("NOT_REGULAR_FILE") from None
            if info.st_size > max_bytes:
                raise SourceReadError("FILE_TOO_LARGE") from None
        observations.append(info)
    return observations


def _open_windows(path: Path) -> int:
    """Own the native handle until a successful transfer to the CRT."""
    if sys.platform != "win32":
        raise SourceReadError("UNSUPPORTED_PLATFORM") from None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    file_type = kernel.GetFileType
    file_type.argtypes = (wintypes.HANDLE,)
    file_type.restype = wintypes.DWORD

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    information = kernel.GetFileInformationByHandleEx
    information.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    information.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SourceReadError("READ_ERROR") from None
    try:
        if file_type(handle) != 1:
            raise SourceReadError("NOT_REGULAR_FILE") from None
        attributes = AttributeTag()
        if not information(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise SourceReadError("SOURCE_VERIFICATION_FAILED") from None
        if attributes.attributes & 0x400:
            raise SourceReadError("SYMLINK_UNSUPPORTED") from None
        try:
            actual = _windows_handle_path(handle)
        except (OSError, ValueError):
            raise SourceReadError("SOURCE_VERIFICATION_FAILED") from None
        if actual != path:
            raise SourceReadError("SOURCE_VERIFICATION_FAILED") from None
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


def _verify_path(source: BinaryIO, expected: Path, *, changed: bool = False) -> None:
    try:
        actual = opened_file_path(source)
    except (OSError, ValueError):
        raise SourceReadError("SOURCE_VERIFICATION_FAILED") from None
    if actual != expected:
        raise SourceReadError("SOURCE_CHANGED" if changed else "SOURCE_VERIFICATION_FAILED") from None


def _verify_observations(root: Path, parts: list[str], before: list[os.stat_result],
                         max_bytes: int) -> None:
    try:
        after = _observe(root, parts, max_bytes)
    except (OSError, SourceReadError):
        raise SourceReadError("SOURCE_CHANGED") from None
    if [_state(info) for info in after] != [_state(info) for info in before]:
        raise SourceReadError("SOURCE_CHANGED") from None


def _read_opened(fd: int, path: Path, root: Path, parts: list[str],
                 before: list[os.stat_result], max_bytes: int) -> SourceRead:
    try:
        opened = os.fstat(fd)
        _unlinked(opened)
        if not stat.S_ISREG(opened.st_mode):
            raise SourceReadError("NOT_REGULAR_FILE") from None
        if opened.st_size > max_bytes:
            raise SourceReadError("FILE_TOO_LARGE") from None
        if not _matches_opened(before[-1], opened):
            raise SourceReadError("SOURCE_CHANGED") from None
        source = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with source:
        _verify_path(source, path)
        _verify_observations(root, parts, before, max_bytes)
        chunks = []
        total = 0
        while True:
            chunk = source.read(min(65536, max_bytes - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise SourceReadError("FILE_TOO_LARGE") from None
            if total > opened.st_size:
                raise SourceReadError("SOURCE_CHANGED") from None
        if total != opened.st_size or _state(os.fstat(source.fileno())) != _state(opened):
            raise SourceReadError("SOURCE_CHANGED") from None
        _verify_path(source, path, changed=True)
        _verify_observations(root, parts, before, max_bytes)
        raw = b"".join(chunks)
        return SourceRead(raw, sha256(raw).hexdigest())


def read_source_bytes(project_root: Path, relative_path: str, *, max_bytes: int) -> SourceRead:
    """Read a normalized relative path beneath a trusted, resolved project root."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    parts = _components(relative_path)
    if sys.platform not in ("win32", "linux"):
        raise SourceReadError("UNSUPPORTED_PLATFORM") from None
    root = project_root
    path = root.joinpath(*parts)
    try:
        before = _observe(root, parts, max_bytes)
        if sys.platform == "win32":
            fd = _open_windows(path)
            return _read_opened(fd, path, root, parts, before, max_bytes)
        with ExitStack() as directories:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, flags)
            directories.callback(os.close, directory)
            if _state(os.fstat(directory)) != _state(before[0]):
                raise SourceReadError("SOURCE_CHANGED") from None
            for index, part in enumerate(parts[:-1], 1):
                directory = os.open(part, flags, dir_fd=directory)
                directories.callback(os.close, directory)
                if _state(os.fstat(directory)) != _state(before[index]):
                    raise SourceReadError("SOURCE_CHANGED") from None
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            return _read_opened(fd, path, root, parts, before, max_bytes)
    except SourceReadError:
        raise
    except OSError:
        raise SourceReadError("READ_ERROR") from None
