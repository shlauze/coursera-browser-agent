"""Store the Groq key using Windows encryption for the current user."""

import ctypes
import os
import json
from ctypes import wintypes
from pathlib import Path


KEY_FILE = Path(__file__).resolve().parent.parent / "data" / ".groq-key.dpapi"


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data, *, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("The saved Groq key is available only on this Windows account.")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    description_type = ctypes.POINTER(wintypes.LPWSTR) if decrypt else wintypes.LPCWSTR
    function.argtypes = [ctypes.POINTER(_DataBlob), description_type,
                         ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                         wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def save_key(key):
    key = key.strip()
    if not key:
        raise ValueError("The Groq key is empty.")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _protect(key.encode("utf-8"))
    temporary = KEY_FILE.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(KEY_FILE)


def load_key():
    if not KEY_FILE.is_file():
        return ""
    return _protect(KEY_FILE.read_bytes(), decrypt=True).decode("utf-8")


def save_monitored_key(name, key, store):
    """Encrypted inventory only. The selected worker key is unchanged."""
    from .groq_limits import register_key
    key = key.strip()
    if not key:
        raise ValueError('The Groq key is empty.')
    identity = register_key(store, key, name)
    path = KEY_FILE.parent / '.groq-monitor-keys.dpapi'
    keys = json.loads(_protect(path.read_bytes(), decrypt=True)) if path.exists() else {}
    keys[identity] = key
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(_protect(json.dumps(keys).encode('utf-8')))
    temporary.replace(path)
    return identity
