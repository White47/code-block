"""Windows recycle-only IFileOperation adapter, with no deletion fallback."""

import ctypes
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

FOFX_RECYCLEONDELETE = 0x00080000
FOF_NOERRORUI = 0x0400
FOFX_EARLYFAILURE = 0x00100000
_FLAGS = FOFX_RECYCLEONDELETE | FOF_NOERRORUI | FOFX_EARLYFAILURE | 0x0010 | 0x0004 | 0x2000
_E_ABORT = -2147467260
_E_NOINTERFACE = -2147467262
_TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(UUID(value).bytes_le)


def _check(result: int) -> None:
    if result < 0:
        raise OSError("System recycling failed. Refresh the tree before proceeding.")


def _method(pointer: ctypes.c_void_p, index: int, result_type: Any,
            *argument_types: Any) -> Any:
    if sys.platform != "win32":
        raise NotImplementedError("Windows COM is unavailable")
    # COM's native ABI requires dynamic ctypes prototypes. Keep this boundary local.
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)
    return prototype(table[index])


def recycle_path(path: Path) -> None:
    if sys.platform != "win32" or sys.getwindowsversion().major < 6:
        raise NotImplementedError("System recycling is unavailable on this platform.")
    version = sys.getwindowsversion()
    if (version.major, version.minor) < (6, 2):
        raise NotImplementedError("Recycle-only operations require modern Windows.")
    path = Path(path).absolute()
    info = path.lstat()
    if (path == Path(path.anchor) or str(path).startswith("\\\\") or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_nlink != 1):
        raise OSError("This entry cannot be recycled safely.")
    _recycle_windows(path)


def _recycle_windows(path: Path) -> None:
    if sys.platform != "win32":
        raise NotImplementedError("Windows COM is unavailable")
    void = ctypes.c_void_p
    dword = ctypes.c_uint32
    hresult = ctypes.c_int32
    boolean = ctypes.c_int32
    ole = ctypes.WinDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    initialize = ole.CoInitializeEx
    initialize.argtypes = (void, dword)
    initialize.restype = hresult
    _check(int(initialize(None, 2)))
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    operation = void()
    item = void()
    callback_refs: list[Any] = []
    try:
        create = ole.CoCreateInstance
        create.argtypes = (ctypes.POINTER(_GUID), void, dword, ctypes.POINTER(_GUID),
                           ctypes.POINTER(void))
        create.restype = hresult
        clsid = _guid("3ad05575-8857-4850-9277-11b85bdb8e09")
        iid_operation = _guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
        _check(int(create(ctypes.byref(clsid), None, 1, ctypes.byref(iid_operation),
                          ctypes.byref(operation))))
        create_item = shell.SHCreateItemFromParsingName
        create_item.argtypes = (ctypes.c_wchar_p, void, ctypes.POINTER(_GUID), ctypes.POINTER(void))
        create_item.restype = hresult
        iid_item = _guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
        _check(int(create_item(str(path), None, ctypes.byref(iid_item), ctypes.byref(item))))

        class Sink(ctypes.Structure):
            _fields_ = [("vtable", ctypes.POINTER(void))]

        sink = Sink()
        sink_pointer = ctypes.cast(ctypes.byref(sink), void)
        references = [1]
        outcomes: list[tuple[int, bool]] = []
        finishes: list[int] = []
        vetoed = [False]
        iid_sink = UUID("04b0f1a7-9490-44bc-96e1-4296a31252e2").bytes_le
        iid_unknown = UUID("00000000-0000-0000-c000-000000000046").bytes_le

        def query(this: int, requested: int, output: int) -> int:
            destination = ctypes.cast(output, ctypes.POINTER(void))
            destination[0] = None
            if ctypes.string_at(requested, 16) not in (iid_sink, iid_unknown):
                return _E_NOINTERFACE
            destination[0] = this
            references[0] += 1
            return 0

        def add_ref(this: int) -> int:
            references[0] += 1
            return references[0]

        def release(this: int) -> int:
            references[0] -= 1
            return references[0]

        def ok(*arguments: object) -> int:
            return 0

        def finish(this: int, result: int) -> int:
            finishes.append(result)
            return 0

        def pre_delete(this: int, flags: int, source: int) -> int:
            # Veto a shell decision to permanently delete before it can happen.
            if not flags & _TSF_DELETE_RECYCLE_IF_POSSIBLE:
                vetoed[0] = True
                return _E_ABORT
            return 0

        def post_delete(this: int, flags: int, source: int, result: int, recycled: int) -> int:
            # A non-null destination is the Shell's actual recycle receipt.
            outcomes.append((result, bool(recycled)))
            return 0 if result >= 0 and recycled else _E_ABORT

        # SDK IFileOperationProgressSink vtable order, including IUnknown.
        signatures: list[tuple[Any, Any, tuple[Any, ...]]] = [
            (query, hresult, (void, ctypes.POINTER(void))),
            (add_ref, dword, ()), (release, dword, ()),
            (ok, hresult, ()), (finish, hresult, (hresult,)),
            (ok, hresult, (dword, void, ctypes.c_wchar_p)),
            (ok, hresult, (dword, void, ctypes.c_wchar_p, hresult, void)),
            (ok, hresult, (dword, void, void, ctypes.c_wchar_p)),
            (ok, hresult, (dword, void, void, ctypes.c_wchar_p, hresult, void)),
            (ok, hresult, (dword, void, void, ctypes.c_wchar_p)),
            (ok, hresult, (dword, void, void, ctypes.c_wchar_p, hresult, void)),
            (pre_delete, hresult, (dword, void)),
            (post_delete, hresult, (dword, void, hresult, void)),
            (ok, hresult, (dword, void, ctypes.c_wchar_p)),
            (ok, hresult, (dword, void, ctypes.c_wchar_p, ctypes.c_wchar_p, dword, hresult, void)),
            (ok, hresult, (dword, dword)),
            (ok, hresult, ()), (ok, hresult, ()), (ok, hresult, ()),
        ]
        for callback, result_type, arguments in signatures:
            prototype = ctypes.WINFUNCTYPE(result_type, void, *arguments)
            callback_refs.append(prototype(callback))
        vtable = (void * len(callback_refs))(*(ctypes.cast(callback, void).value
                                             for callback in callback_refs))
        sink.vtable = vtable
        _check(int(_method(operation, 5, hresult, dword)(operation, _FLAGS)))
        _check(int(_method(operation, 18, hresult, void, void)(operation, item, sink_pointer)))
        _check(int(_method(operation, 21, hresult)(operation)))
        aborted = boolean()
        _check(int(_method(operation, 22, hresult, ctypes.POINTER(boolean))(
            operation, ctypes.byref(aborted))))
        if (aborted.value or vetoed[0] or not outcomes
                or any(result < 0 or not receipt for result, receipt in outcomes)
                or any(result < 0 for result in finishes)):
            raise OSError("System recycling did not complete. Refresh the tree before proceeding.")
    finally:
        if item.value:
            _method(item, 2, dword)(item)
        if operation.value:
            _method(operation, 2, dword)(operation)
        # Keep callbacks and their vtable alive through the last COM Release.
        callback_refs.clear()
        ole.CoUninitialize()
