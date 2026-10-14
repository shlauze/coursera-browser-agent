"""Short-lived readers with explicit Windows read/write/delete sharing."""
from contextlib import contextmanager
import os


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@contextmanager
def open_read_shared(path):
    """Open a binary reader, sharing read/write/delete access with other handles.

    Writers must still publish whole files atomically and retry brief Windows
    replacement conflicts. No write access is requested.
    """
    if os.name != "nt":
        with open(path, "rb") as stream:
            yield stream
        return

    filename = os.fsdecode(os.fspath(path))
    if "\0" in filename:
        raise ValueError("embedded null character in path")
    handle = _create_file(filename, 0x80000000, 0x00000001 | 0x00000002 | 0x00000004,
                          None, 3, 0x00000080, None)
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        # Ownership transfers only after open_osfhandle succeeds.
        _close_handle(handle)
        raise
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        # The CRT descriptor now owns the Windows handle; close it only once.
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    with stream:
        yield stream
