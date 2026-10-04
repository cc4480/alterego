"""Write tools. EVERY call requires native approval (fail closed)."""
import ctypes
import os
import subprocess
from ctypes import wintypes  # pure type aliases; windll itself is Windows-only

from approval import request_approval

MAX_OUTPUT = 65536  # truncate captured output at 64 KB


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP = 0x0002

if os.name == "nt":
    # ctypes assumes c_int for unstated returns; 64-bit pointer returns
    # would be truncated. Window handles are always 32-bit, so only the
    # memory-handle functions below need fixing.
    ctypes.windll.kernel32.GlobalAlloc.restype = ctypes.c_void_p
    ctypes.windll.kernel32.GlobalLock.restype = ctypes.c_void_p
    ctypes.windll.user32.GetClipboardData.restype = ctypes.c_void_p
    ctypes.windll.user32.SetClipboardData.restype = ctypes.c_void_p
    ctypes.windll.kernel32.OpenProcess.restype = ctypes.c_void_p


def _approved(tool: str, summary: str) -> None:
    if not request_approval(f"[{tool}]\n{summary}"):
        raise PermissionError("denied by local approval (or timed out)")


def focus_window(hwnd: int) -> dict:
    _approved("focus_window", f"Bring window handle {hwnd} to the foreground.")
    ok = bool(ctypes.windll.user32.SetForegroundWindow(int(hwnd)))
    return {"hwnd": int(hwnd), "focused": ok}


def close_window(hwnd: int) -> dict:
    """Gracefully close a window via WM_CLOSE (like clicking X).

    If the app has unsaved changes it shows its own save dialog and stays
    open — this only _requests_ the close, it never force-kills.
    """
    _approved("close_window", f"Close window handle {hwnd} (graceful, like clicking X).")
    WM_CLOSE = 0x0010
    ok = bool(ctypes.windll.user32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0))
    return {"hwnd": int(hwnd), "close_requested": ok}


def type_text(text: str) -> dict:
    if not isinstance(text, str) or not text or len(text) > 2000:
        raise ValueError("text must be a non-empty string up to 2000 chars")
    preview = text if len(text) <= 300 else text[:300] + "..."
    _approved("type_text", f"Type {len(text)} chars into the focused window:\n{preview}")
    _send_unicode(text)
    return {"typed_chars": len(text)}


def _send_unicode(text: str) -> None:
    """Type via SendInput with KEYEVENTF_UNICODE (handles all of Unicode)."""
    # Sanity: 64-bit Windows requires sizeof(INPUT) == 40 (DWORD + union).
    # A flat struct without the union measures 32 and SendInput rejects it.
    assert ctypes.sizeof(INPUT) == 40, f"INPUT size {ctypes.sizeof(INPUT)} != 40"

    KEYEVENTF_UNICODE = 0x0004
    user32 = ctypes.windll.user32
    units = text.encode("utf-16-le")  # surrogate pairs handled correctly
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i : i + 2], "little")
        for keyup in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
            inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=_KEYBDINPUT(0, code, flags, 0, None)))
            if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
                raise OSError("SendInput failed")


def shell_exec(command: str, timeout_s: int = 60) -> dict:
    if not command or len(command) > 4000:
        raise ValueError("command must be 1-4000 chars")
    timeout_s = max(1, min(int(timeout_s), 300))
    _approved("shell_exec", f"Run in cmd.exe (timeout {timeout_s}s):\n{command}")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "command": command,
            "timed_out": True,
            "stdout": (e.stdout or "")[-MAX_OUTPUT:],
            "stderr": (e.stderr or "")[-MAX_OUTPUT:],
        }
    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-MAX_OUTPUT:],
        "stderr": proc.stderr[-MAX_OUTPUT:],
    }


# ---- keyboard / mouse / window / process / clipboard ----------------------

_VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
}
_VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
_MODIFIERS = {"ctrl", "alt", "shift", "win"}


def _parse_hotkey(spec: str):
    """'ctrl+shift+s' -> ([0x11, 0x10], 0x53). Pure logic; testable anywhere."""
    parts = [p.strip().lower() for p in str(spec).split("+")]
    if not parts or any(not p for p in parts):
        raise ValueError(f"bad hotkey spec: {spec!r}")
    *mods, main = parts
    for m in mods:
        if m not in _MODIFIERS:
            raise ValueError(f"unknown modifier: {m!r} in {spec!r}")
    vk = _VK.get(main)
    if vk is None:
        if len(main) == 1 and main.isalnum():
            vk = ord(main.upper())  # VK_A..VK_Z / VK_0..VK_9 match ASCII
        else:
            raise ValueError(f"unknown key: {main!r} in {spec!r}")
    return [_VK[m] for m in mods], vk


def _tap_vk(vk: int) -> None:
    user32 = ctypes.windll.user32
    for down in (True, False):
        ki = _KEYBDINPUT(wVk=vk, wScan=0,
                         dwFlags=0 if down else KEYEVENTF_KEYUP,
                         time=0, dwExtraInfo=None)
        inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki))
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
            raise OSError("SendInput failed")


def hotkey(keys: str) -> dict:
    """Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'."""
    mods, vk = _parse_hotkey(keys)
    _approved("hotkey", f"Press key combo:\n{keys}")
    user32 = ctypes.windll.user32
    sent = []
    try:
        for m in mods:  # modifiers down
            ki = _KEYBDINPUT(wVk=m, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)
            user32.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki))),
                             ctypes.sizeof(INPUT))
            sent.append(m)
        _tap_vk(vk)
    finally:
        for m in reversed(sent):  # modifiers up, even on failure
            ki = _KEYBDINPUT(wVk=m, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
            user32.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki))),
                             ctypes.sizeof(INPUT))
    return {"hotkey": keys, "pressed": True}


def _mouse_event(flags: int, data: int = 0) -> None:
    user32 = ctypes.windll.user32
    mi = _MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=None)
    inp = INPUT(INPUT_MOUSE, _INPUT_UNION(mi=mi))
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
        raise OSError("SendInput failed")


def mouse_move(x: int, y: int) -> dict:
    """Move the cursor to screen coordinates. Requires on-PC approval."""
    _approved("mouse_move", f"Move mouse to ({int(x)}, {int(y)}).")
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    return {"x": int(x), "y": int(y)}


def mouse_click(x: int, y: int, button: str = "left") -> dict:
    """Move to (x, y) and click. button: left/right/middle."""
    if button not in ("left", "right", "middle"):
        raise ValueError("button must be left, right, or middle")
    _approved("mouse_click", f"{button} click at ({int(x)}, {int(y)}).")
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    down_up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
               "middle": (0x0020, 0x0040)}[button]
    for flags in down_up:
        _mouse_event(flags)
    return {"x": int(x), "y": int(y), "button": button, "clicked": True}


def mouse_scroll(direction: str = "down", clicks: int = 3) -> dict:
    """Scroll the wheel under the cursor. direction: up/down."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    clicks = max(1, min(int(clicks), 20))
    _approved("mouse_scroll", f"Scroll {direction} x{clicks} at cursor.")
    delta = 120 * clicks * (-1 if direction == "down" else 1)
    _mouse_event(0x0800, delta)  # MOUSEEVENTF_WHEEL
    return {"direction": direction, "clicks": clicks}


def minimize_window(hwnd: int) -> dict:
    """Minimize a window. Requires on-PC approval."""
    _approved("minimize_window", f"Minimize window handle {hwnd}.")
    ok = bool(ctypes.windll.user32.ShowWindow(int(hwnd), 6))  # SW_MINIMIZE
    return {"hwnd": int(hwnd), "minimized": True, "was_visible": ok}


def maximize_window(hwnd: int) -> dict:
    """Maximize a window. Requires on-PC approval."""
    _approved("maximize_window", f"Maximize window handle {hwnd}.")
    ok = bool(ctypes.windll.user32.ShowWindow(int(hwnd), 3))  # SW_MAXIMIZE
    return {"hwnd": int(hwnd), "maximized": True, "was_visible": ok}


def kill_process(pid: int) -> dict:
    """Terminate a process by PID. Unsaved work in it is lost."""
    pid = int(pid)
    if pid <= 0:
        raise ValueError("pid must be positive")
    if pid == os.getpid():
        raise ValueError("refusing to kill the bridge itself")
    _approved("kill_process", f"Terminate process PID {pid}. Unsaved work will be lost.")
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not h:
        raise OSError(f"cannot open PID {pid}")
    try:
        if not kernel32.TerminateProcess(h, 1):
            raise OSError(f"cannot terminate PID {pid}")
    finally:
        kernel32.CloseHandle(h)
    return {"pid": pid, "terminated": True}


def clipboard_set(text: str) -> dict:
    """Put text on the Windows clipboard. Requires on-PC approval."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > 100000:
        raise ValueError("text too long (100k chars max)")
    preview = text if len(text) <= 300 else text[:300] + "..."
    _approved("clipboard_set", f"Set clipboard ({len(text)} chars):\n{preview}")
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    CF_UNICODETEXT, GHND = 13, 0x0042
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        raise OSError("cannot open clipboard")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GHND, len(data))
        if not h:
            raise OSError("GlobalAlloc failed")
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise OSError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()
    return {"clipboard_chars": len(text)}
