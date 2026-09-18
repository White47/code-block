"""Verify the actual file behind an open handle on supported platforms."""

import sys
from pathlib import Path
from typing import BinaryIO


def opened_file_path(source: BinaryIO) -> Path:
    """Resolve the opened handle, not a name that may have changed before open."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        api = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        api.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        api.restype = wintypes.DWORD
        size = 260
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            length = api(msvcrt.get_osfhandle(source.fileno()), buffer, size, 0)
            if length == 0:
                raise OSError(ctypes.get_last_error(), "cannot verify opened source path")
            if length < size:
                raw = buffer.value
                if raw.startswith("\\\\?\\UNC\\"):
                    return Path("\\\\" + raw[8:])
                if raw.startswith("\\\\?\\"):
                    return Path(raw[4:])
                raise ValueError("opened source path is not a DOS path")
            size = length + 1
        raise ValueError("opened source path is too long to verify")
    descriptor = Path("/proc/self/fd") / str(source.fileno())
    if not descriptor.exists():
        raise ValueError("opened source path cannot be verified on this platform")
    return descriptor.resolve(strict=True)
