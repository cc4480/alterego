"""Raw Windows clipboard text access.

No approval here - the calling tool (clipboard_get / clipboard_set /
paste_text) owns the approval decision.
"""
import ctypes
import os

if os.name == "nt":
    # ctypes assumes c_int args/returns; 64-bit pointer args/returns must
    # be declared or handles truncate / OverflowError follows.
    _u, _k, _P = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.c_void_p
    _u.GetClipboardData.argtypes = [ctypes.c_uint]; _u.GetClipboardData.restype = _P
    _u.SetClipboardData.argtypes = [ctypes.c_uint, _P]; _u.SetClipboardData.restype = _P
    _u.OpenClipboard.argtypes = [_P]
    _k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]; _k.GlobalAlloc.restype = _P
    _k.GlobalLock.argtypes = [_P]; _k.GlobalLock.restype = _P
    _k.GlobalUnlock.argtypes = [_P]; _k.GlobalFree.argtypes = [_P]

CF_UNICODETEXT = 13


def get_text() -> str:
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    if not u.OpenClipboard(None):
        return ""
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) or ""
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set_text(text: str) -> None:
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u.OpenClipboard(None):
        raise OSError("cannot open clipboard")
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(0x0042, len(data))  # GHND
        if not h:
            raise OSError("GlobalAlloc failed")
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)
            raise OSError("SetClipboardData failed")
    finally:
        u.CloseClipboard()
