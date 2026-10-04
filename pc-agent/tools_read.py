"""Read-only tools. File access is restricted to the user's own profile."""
import base64
import ctypes
import getpass
import os
import platform
from pathlib import Path

MAX_READ_BYTES = 1_000_000  # 1 MB cap on read_file

if os.name == "nt":
    # 64-bit pointer args/returns must be declared (see tools_write.py).
    _ku, _kk, _KP = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.c_void_p
    _ku.GetClipboardData.argtypes = [ctypes.c_uint]; _ku.GetClipboardData.restype = _KP
    _kk.GlobalLock.argtypes = [_KP]; _kk.GlobalLock.restype = _KP
    _kk.GlobalUnlock.argtypes = [_KP]


def _user_profile() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))).resolve()


def _system_root() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()


def _check_path(raw: str) -> Path:
    """Resolve and enforce the allowlist. Raises ValueError on denial."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _user_profile() / p
    p = p.resolve()
    profile = _user_profile()
    if p != profile and profile not in p.parents:
        raise ValueError(f"denied: outside your user profile ({profile})")
    sysroot = _system_root()
    if p == sysroot or sysroot in p.parents:
        raise ValueError("denied: Windows system directory")
    return p


def screenshot() -> dict:
    import mss
    import mss.tools

    with mss.mss() as sct:
        img = sct.grab(sct.monitors[0])
        png = mss.tools.to_png(img.rgb, img.size)
    return {
        "png_base64": base64.b64encode(png).decode("ascii"),
        "width": img.width,
        "height": img.height,
    }


def list_windows() -> dict:
    import win32gui
    import win32process

    out = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        out.append({"hwnd": hwnd, "pid": pid, "title": title})

    win32gui.EnumWindows(_cb, None)
    return {"windows": out}


def system_info() -> dict:
    import psutil

    u = platform.uname()
    return {
        "system": u.system,
        "release": u.release,
        "version": u.version,
        "machine": u.machine,
        "hostname": u.node,
        "user": getpass.getuser(),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "mem_percent": psutil.virtual_memory().percent,
    }


def list_dir(path: str = "") -> dict:
    p = _check_path(path or ".")
    if not p.is_dir():
        raise ValueError("not a directory")
    items = []
    for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        try:
            items.append(
                {
                    "name": child.name,
                    "dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                }
            )
        except OSError:
            continue
    return {"path": str(p), "items": items}


def read_file(path: str) -> dict:
    p = _check_path(path)
    if not p.is_file():
        raise ValueError("not a file")
    if p.stat().st_size > MAX_READ_BYTES:
        raise ValueError("file too large (>1MB)")
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not a UTF-8 text file")
    return {"path": str(p), "content": text}


def clipboard_get() -> dict:
    """Read text from the Windows clipboard (no approval; it's a read)."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return {"text": "", "note": "clipboard unavailable"}
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return {"text": ""}
        p = kernel32.GlobalLock(h)
        try:
            text = ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
        return {"text": text or ""}
    finally:
        user32.CloseClipboard()
