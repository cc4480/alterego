"""Write tools. EVERY call requires native approval (fail closed)."""
import ctypes
import os
import subprocess
from ctypes import wintypes  # pure type aliases; windll itself is Windows-only

import subproc
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
    # ctypes assumes c_int args/returns: declare 64-bit pointer args/returns
    # for the process APIs (HWNDs stay 32-bit, fine undeclared).
    _k32, _P = ctypes.windll.kernel32, ctypes.c_void_p
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = _P
    _k32.TerminateProcess.argtypes = [_P, wintypes.UINT]
    _k32.CloseHandle.argtypes = [_P]
    # Per-monitor DPI awareness: mouse coords are true physical pixels.
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception: pass


def _approved(tool: str, summary: str) -> None:
    if not request_approval(f"[{tool}]\n{summary}"):
        raise PermissionError("denied by local approval (or timed out)")


def focus_window(hwnd: int) -> dict:
    _approved("focus_window", f"Bring window handle {hwnd} to the foreground.")
    u32, hwnd = ctypes.windll.user32, int(hwnd)
    u32.ShowWindow(hwnd, 9)  # SW_RESTORE: unminimize first
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    fg = u32.GetForegroundWindow()
    ft = u32.GetWindowThreadProcessId(fg, None)
    if fg and ft != me:
        u32.AttachThreadInput(me, ft, True)  # bypass the foreground lock
    u32.SetForegroundWindow(hwnd)
    u32.BringWindowToTop(hwnd)
    if fg and ft != me:
        u32.AttachThreadInput(me, ft, False)
    return {"hwnd": hwnd, "focused": u32.GetForegroundWindow() == hwnd}


def close_window(hwnd: int) -> dict:
    """Gracefully close a window via WM_CLOSE (apps with unsaved work show their save dialog)."""
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
    # Win64 requires sizeof(INPUT) == 40; a flat struct is 32 and SendInput rejects it.
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
    returncode, stdout, stderr, timed_out = subproc.run_noinherit(
        command, timeout_s, shell=True)
    if timed_out:
        return {
            "command": command,
            "timed_out": True,
            "stdout": stdout[-MAX_OUTPUT:],
            "stderr": stderr[-MAX_OUTPUT:],
        }
    return {
        "command": command,
        "returncode": returncode,
        "stdout": stdout[-MAX_OUTPUT:],
        "stderr": stderr[-MAX_OUTPUT:],
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


def _key_event(vk: int, down: bool) -> None:
    ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0 if down else KEYEVENTF_KEYUP,
                     time=0, dwExtraInfo=None)
    inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki))
    if ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
        raise OSError("SendInput failed")


def _tap_vk(vk: int) -> None:
    _key_event(vk, True); _key_event(vk, False)


def hotkey(keys: str) -> dict:
    """Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'."""
    mods, vk = _parse_hotkey(keys)
    _approved("hotkey", f"Press key combo:\n{keys}")
    try:
        for m in mods:
            _key_event(m, True)
        _tap_vk(vk)
    finally:
        for m in reversed(mods):
            _key_event(m, False)
    return {"hotkey": keys, "pressed": True}


def _mouse_event(flags: int, data: int = 0) -> None:
    user32 = ctypes.windll.user32
    mi = _MOUSEINPUT(dx=0, dy=0, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=None)
    inp = INPUT(INPUT_MOUSE, _INPUT_UNION(mi=mi))
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) != 1:
        raise OSError("SendInput failed")


def mouse_move(x: int, y: int) -> dict:
    """Move the cursor to physical screen pixels. Requires on-PC approval."""
    _approved("mouse_move", f"Move mouse to ({int(x)}, {int(y)}).")
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    return {"x": int(x), "y": int(y)}


def mouse_click(x: int, y: int, button: str = "left") -> dict:
    """Move to physical screen pixels (x, y) and click. button: left/right/middle."""
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
    import clipboard
    clipboard.set_text(text)
    return {"clipboard_chars": len(text)}


def paste_text(text: str) -> dict:
    """Paste into the focused window via the clipboard; restores the prior clipboard."""
    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    if len(text) > 100000:
        raise ValueError("text too long (100k chars max)")
    _approved("paste_text", f"Paste {len(text)} chars into the focused window.")
    import clipboard
    import time
    old = clipboard.get_text()
    try:
        clipboard.set_text(text)
        _key_event(_VK["ctrl"], True)
        try:
            _tap_vk(ord("V"))
        finally:
            _key_event(_VK["ctrl"], False)
        time.sleep(0.5)  # let the paste land before restoring clipboard
    finally:
        clipboard.set_text(old)
    return {"pasted_chars": len(text)}
