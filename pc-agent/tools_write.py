"""Write tools. EVERY call requires native approval (fail closed)."""
import subprocess

from approval import request_approval

MAX_OUTPUT = 65536  # truncate captured output at 64 KB


def _approved(tool: str, summary: str) -> None:
    if not request_approval(f"[{tool}]\n{summary}"):
        raise PermissionError("denied by local approval (or timed out)")


def focus_window(hwnd: int) -> dict:
    _approved("focus_window", f"Bring window handle {hwnd} to the foreground.")
    import ctypes

    ok = bool(ctypes.windll.user32.SetForegroundWindow(int(hwnd)))
    return {"hwnd": int(hwnd), "focused": ok}


def type_text(text: str) -> dict:
    if not isinstance(text, str) or not text or len(text) > 2000:
        raise ValueError("text must be a non-empty string up to 2000 chars")
    preview = text if len(text) <= 300 else text[:300] + "..."
    _approved("type_text", f"Type {len(text)} chars into the focused window:\n{preview}")
    _send_unicode(text)
    return {"typed_chars": len(text)}


def _send_unicode(text: str) -> None:
    """Type via SendInput with KEYEVENTF_UNICODE (handles all of Unicode)."""
    import ctypes
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

    # Sanity: 64-bit Windows requires sizeof(INPUT) == 40 (DWORD + union).
    # A flat struct without the union measures 32 and SendInput rejects it.
    assert ctypes.sizeof(INPUT) == 40, f"INPUT size {ctypes.sizeof(INPUT)} != 40"

    INPUT_KEYBOARD, KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 1, 0x0004, 0x0002
    user32 = ctypes.windll.user32
    units = text.encode("utf-16-le")  # surrogate pairs handled correctly
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i : i + 2], "little")
        for keyup in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
            inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(0, code, flags, 0, None)))
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
