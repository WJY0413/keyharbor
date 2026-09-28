"""Windows CurrentUser DPAPI. Never serialize a decrypted credential."""
import ctypes
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI requires the original Windows user")
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    function = dll.CryptProtectData if protect else dll.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    # CRYPTPROTECT_UI_FORBIDDEN: never trigger credential UI in the service.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("DPAPI unavailable for this Windows identity")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)
        ctypes.memset(buf, 0, len(value))


def load(path: str) -> str:
    value = _crypt(Path(path).read_bytes(), False).decode("utf-8")
    if not value.strip():
        raise ValueError("Empty credential")
    return value


def save(path: str, value: str, replace: bool = False) -> None:
    if not value.strip():
        raise ValueError("Empty credential")
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt(value.encode("utf-8"), True)
    if not replace:
        # Exclusive create prevents a second profile accidentally overwriting the first.
        with dest.open("xb") as stream:
            stream.write(encrypted)
    else:
        temp = dest.with_suffix(dest.suffix + ".tmp")
        with temp.open("xb") as stream:
            stream.write(encrypted)
        os.replace(temp, dest)
