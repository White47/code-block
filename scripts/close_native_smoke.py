"""Close only a CodeViz native window belonging to the supplied test process."""

import argparse
import ctypes
from ctypes import wintypes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    args = parser.parse_args()
    if args.pid <= 0:
        parser.error("PID must be positive")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    closed = []

    @callback_type
    def visit(handle, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value == args.pid:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            if title.value == "CodeViz":
                if not user32.PostMessageW(handle, 0x0010, 0, 0):
                    raise ctypes.WinError(ctypes.get_last_error())
                closed.append(int(handle))
        return True

    user32.EnumWindows(visit, 0)
    if not closed:
        raise SystemExit("No matching CodeViz test window; no other process was changed.")
    print(f"Requested native window close for PID {args.pid}: {closed}")


if __name__ == "__main__":
    main()
