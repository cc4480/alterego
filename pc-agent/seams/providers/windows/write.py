"""Write tools. EVERY call requires native approval (fail closed)."""
import ctypes
import os
import re
import subprocess
import time
from ctypes import wintypes  # pure type aliases; windll itself is Windows-only

import subproc
from approval import request_approval
from seams.providers.windows.read import (
    UIA_ENUM_MAX_RESULTS, _check_path, _resolve_cwd, _uia_failure,
    _uia_invoke, _uia_payload)

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
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def _window_title(hwnd: int) -> str:
    """Title of a window handle ('' when unknown). Pure ctypes."""
    u32 = ctypes.windll.user32
    n = u32.GetWindowTextLengthW(int(hwnd))
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(int(hwnd), buf, n + 1)
    return buf.value


def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground — verified, or an error.

    Every Win32 return value is checked and GetForegroundWindow() is
    polled for ~500 ms. Failure raises RuntimeError naming the actual
    foreground window (hwnd + title): a silent {"focused": false}
    success payload is the bug this replaces (a background process can
    be denied foreground even when it follows the rules).
    """
    _approved("focus_window", f"Bring window handle {hwnd} to the foreground.")
    u32, hwnd = ctypes.windll.user32, int(hwnd)
    u32.ShowWindow(hwnd, 9)  # SW_RESTORE: unminimize first
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    fg = u32.GetForegroundWindow()
    ft = u32.GetWindowThreadProcessId(fg, None)
    errors = []
    attached = False
    if fg and ft != me:
        # bypass the foreground lock
        if u32.AttachThreadInput(me, ft, True):
            attached = True
        else:
            errors.append("AttachThreadInput(attach) returned 0")
    try:
        if not u32.SetForegroundWindow(hwnd):
            errors.append("SetForegroundWindow returned 0")
        if not u32.BringWindowToTop(hwnd):
            errors.append("BringWindowToTop returned 0")
    finally:
        if attached and not u32.AttachThreadInput(me, ft, False):
            errors.append("AttachThreadInput(detach) returned 0")
    deadline = time.monotonic() + 0.5
    while True:
        if u32.GetForegroundWindow() == hwnd:
            return {"hwnd": hwnd, "focused": True}
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    actual = u32.GetForegroundWindow()
    detail = ("; " + "; ".join(errors)) if errors else ""
    raise RuntimeError(
        f"focus_window failed: hwnd {hwnd} did not become foreground; "
        f"actual foreground is hwnd {actual} "
        f"({_window_title(actual)!r}){detail}")


def close_window(hwnd: int) -> dict:
    """Gracefully close a window via WM_CLOSE (apps with unsaved work show their save dialog)."""
    _approved("close_window", f"Close window handle {hwnd} (graceful, like clicking X).")
    WM_CLOSE = 0x0010
    ok = bool(ctypes.windll.user32.PostMessageW(int(hwnd), WM_CLOSE, 0, 0))
    return {"hwnd": int(hwnd), "close_requested": ok}


def _resolve_target_hwnd(window) -> int | None:
    """Window spec (hwnd digits or title regex) -> hwnd, or None."""
    if window is None:
        return None
    s = str(window).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    from seams.providers.windows.read import list_windows
    for w in list_windows()["windows"]:
        if re.search(s, w["title"]):
            return int(w["hwnd"])
    raise RuntimeError(f"focus gate: window not found: {window!r}")


def _focus_gate(window=None, name_rx: str = "", control_type: str = "",
                index: int | None = None, timeout_s: float = 0.5) -> dict:
    """Verify input will land on the target — or raise, typing nothing.

    Gate: the foreground hwnd must be the target window AND (when an
    element is addressed) the UIA FocusedElement's RuntimeId must equal
    the target element's. Polled for ~timeout_s, then RuntimeError
    naming the actual foreground window / focused element.
    """
    target_hwnd = _resolve_target_hwnd(window)
    target_rid = ""
    if name_rx:
        info = _uia_invoke(_uia_payload(
            "get", window, name_rx, control_type, index))
        if not info.get("ok"):
            raise _uia_failure(info, "focus gate: element not found")
        target_rid = info.get("runtime_id", "")
        if target_hwnd is None and info.get("window_hwnd"):
            target_hwnd = int(info["window_hwnd"])
    u32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout_s
    fg = rid = None
    while True:
        fg = u32.GetForegroundWindow()
        fg_ok = target_hwnd is None or fg == target_hwnd
        rid_ok = True
        if target_rid:
            foc = _uia_invoke({"op": "focused", "window": window,
                               "name_rx": "", "control_type": "",
                               "index": 0, "max_results": 1})
            rid = foc.get("runtime_id", "") if foc.get("ok") else ""
            rid_ok = rid == target_rid
        if fg_ok and rid_ok:
            return {"hwnd": target_hwnd, "runtime_id": target_rid}
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    raise RuntimeError(
        f"focus gate failed: foreground hwnd {fg} "
        f"({_window_title(fg) if fg else ''!r}) vs target hwnd "
        f"{target_hwnd}; focused element RuntimeId {rid!r} vs target "
        f"{target_rid!r} — aborting, nothing typed")


def _target_args(window, name_rx, control_type, index) -> dict:
    """Normalize the optional target shared by type_text/hotkey."""
    if window is not None and (not isinstance(window, str)
                               or not window.strip()):
        raise ValueError("window must be a non-empty title regex or hwnd")
    if name_rx and not isinstance(name_rx, str):
        raise ValueError("name_rx must be a regex string")
    return {"window": window, "name_rx": name_rx or "",
            "control_type": control_type or "", "index": int(index)}


def type_text(text: str, window: str | None = None, name_rx: str = "",
              control_type: str = "", index: int = 0) -> dict:
    """Type text. With a target, a focus gate runs first (abort on
    failure — never type blind) and the landing is spot-read back where
    the element exposes a value. Without a target the result is
    labelled verified:false."""
    if not isinstance(text, str) or not text or len(text) > 2000:
        raise ValueError("text must be a non-empty string up to 2000 chars")
    tgt = _target_args(window, name_rx, control_type, index)
    targeted = tgt["window"] is not None or bool(tgt["name_rx"])
    preview = text if len(text) <= 300 else text[:300] + "..."
    _approved("type_text", f"Type {len(text)} chars into the focused window:\n{preview}")
    if not targeted:
        _send_unicode(text)
        return {"typed_chars": len(text), "verified": False}
    _focus_gate(**tgt)
    _send_unicode(text)
    verified = True  # gate passed; upgraded by read-back when feasible
    if tgt["name_rx"]:
        after = _uia_invoke(_uia_payload(
            "get", tgt["window"], tgt["name_rx"], tgt["control_type"],
            tgt["index"]))
        value = after.get("value") if after.get("ok") else None
        if isinstance(value, str) and "ValuePattern" in (
                after.get("patterns") or []):
            if text not in value:
                raise RuntimeError(
                    f"type_text verification failed: typed text not in "
                    f"target value (read back {value!r})")
    return {"typed_chars": len(text), "verified": verified}


def _send_inputs(inputs: list) -> None:
    """One batched SendInput call for a whole event sequence.

    Per-code-unit calls let real user keystrokes interleave mid-string;
    a single call injects the sequence atomically."""
    # Win64 requires sizeof(INPUT) == 40; a flat struct is 32 and SendInput rejects it.
    assert ctypes.sizeof(INPUT) == 40, f"INPUT size {ctypes.sizeof(INPUT)} != 40"
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(
        len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput injected {sent} of {len(inputs)} events")


def _send_unicode(text: str) -> None:
    """Type via SendInput with KEYEVENTF_UNICODE (handles all of Unicode)."""
    KEYEVENTF_UNICODE = 0x0004
    units = text.encode("utf-16-le")  # surrogate pairs handled correctly
    inputs = []
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i : i + 2], "little")
        for keyup in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
            inputs.append(INPUT(
                INPUT_KEYBOARD,
                _INPUT_UNION(ki=_KEYBDINPUT(0, code, flags, 0, None))))
    _send_inputs(inputs)


def shell_exec(command: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Run a command in cmd.exe. cwd: optional dir inside the user profile."""
    if not command or len(command) > 4000:
        raise ValueError("command must be 1-4000 chars")
    timeout_s = max(1, min(int(timeout_s), 300))
    run_cwd = _resolve_cwd(cwd)  # None ok; validates profile root + is_dir
    where = f" in {run_cwd}" if run_cwd else ""
    _approved("shell_exec",
              f"Run in cmd.exe (timeout {timeout_s}s){where}:\n{command}")
    returncode, stdout, stderr, timed_out = subproc.run_noinherit(
        command, timeout_s, shell=True, cwd=run_cwd)
    out = {"command": command, "stdout": stdout[-MAX_OUTPUT:],
           "stderr": stderr[-MAX_OUTPUT:]}
    if timed_out:
        out["timed_out"] = True
    else:
        out["returncode"] = returncode
    return out


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


def _vk_input(vk: int, down: bool) -> INPUT:
    ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0 if down else KEYEVENTF_KEYUP,
                     time=0, dwExtraInfo=None)
    return INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki))


def _key_event(vk: int, down: bool) -> None:
    _send_inputs([_vk_input(vk, down)])


def _tap_vk(vk: int) -> None:
    _send_inputs([_vk_input(vk, True), _vk_input(vk, False)])


def hotkey(keys: str, window: str | None = None, name_rx: str = "",
           control_type: str = "", index: int = 0) -> dict:
    """Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'.

    With a target, the focus gate runs first (abort on failure); the
    whole combo is injected as ONE batched SendInput call. Without a
    target the result is labelled verified:false."""
    mods, vk = _parse_hotkey(keys)
    tgt = _target_args(window, name_rx, control_type, index)
    targeted = tgt["window"] is not None or bool(tgt["name_rx"])
    _approved("hotkey", f"Press key combo:\n{keys}")
    if targeted:
        _focus_gate(**tgt)
    inputs = ([_vk_input(m, True) for m in mods]
              + [_vk_input(vk, True), _vk_input(vk, False)]
              + [_vk_input(m, False) for m in reversed(mods)])
    _send_inputs(inputs)
    return {"hotkey": keys, "pressed": True, "verified": targeted}


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


def _paste_readback(text: str) -> bool:
    """True only when the pasted text is verifiably landed: the
    focused element's UIA value/text contains it, OR the helper's
    expect-escalation found it in an ancestor of the focused element
    or elsewhere in the focused element's window (verified_via
    "ancestor"/"window" — e.g. Notepad, where focus sits on a
    pattern-less Pane and the text lives in a sibling Document).
    Any failure to resolve (no UIA, no focused element, no element
    containing the text) is False — never an implied success."""
    try:
        foc = _uia_invoke({"op": "focused", "window": "", "name_rx": "",
                           "control_type": "", "index": 0,
                           "max_results": 1, "expect": text})
    except Exception:
        return False
    if not foc.get("ok"):
        return False
    for key in ("value", "text"):
        candidate = foc.get(key)
        if isinstance(candidate, str) and text in candidate:
            return True
    return foc.get("verified_via") in ("ancestor", "window")


def paste_text(text: str) -> dict:
    """Paste into the focused window via the clipboard; restores the
    prior clipboard. Verified-write bar: after pasting, the landing
    is read back via UIA with expect-escalation — verified:true when
    the focused element's value/text contains the pasted text, or
    when the helper finds it in an ancestor of the focused element
    or elsewhere in its window (Notepad's text lives in a Document
    sibling of the focused, pattern-less Pane); verified:false when
    the landing differs, the text is nowhere in the window, or no
    read-back is possible. pasted_chars alone is no longer the
    success signal."""
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
        # Ctrl+V as ONE batched SendInput call -- the codebase rule
        # (see _send_inputs): split per-event calls let other input
        # interleave mid-chord and the paste intermittently never
        # lands (proven live 2026-10-29: 3 split-call pastes lost,
        # batched hotkey ctrl+v and the batched paste both landed).
        _send_inputs([_vk_input(_VK["ctrl"], True),
                      _vk_input(ord("V"), True),
                      _vk_input(ord("V"), False),
                      _vk_input(_VK["ctrl"], False)])
        time.sleep(0.5)  # let the paste land before restoring clipboard
        verified = _paste_readback(text)
    finally:
        clipboard.set_text(old)
    return {"pasted_chars": len(text), "verified": verified}


def uia_click(window: str, name_rx: str = "", control_type: str = "",
              index: int | None = None, button: str = "left",
              runtime_id: str = "") -> dict:
    """Click a UI element in a window, via UI Automation.

    Addressing: runtime_id (from uia_find) wins when given — it is
    stable across re-enumeration; otherwise name_rx + index into the
    same enumeration uia_find reports. Prefers InvokePattern (no
    mouse movement); falls back to a coordinate click at the
    element's center using physical pixels.
    """
    if button not in ("left", "right", "middle"):
        raise ValueError("button must be left, right, or middle")
    if runtime_id and not isinstance(runtime_id, str):
        raise ValueError("runtime_id must be a string")
    if not runtime_id and (not isinstance(name_rx, str) or not name_rx):
        raise ValueError("name_rx must be a non-empty regex "
                         "(or pass runtime_id from uia_find)")
    if not isinstance(window, str) or not window.strip():
        raise ValueError("window must be a non-empty title regex or hwnd")
    _approved("uia_click",
              "UIA click element /%s/ in window /%s/."
              % (runtime_id or name_rx, window))
    res = _uia_invoke(_uia_payload(
        "click", window, name_rx, control_type, index,
        runtime_id=runtime_id or ""))
    if not res.get("ok"):
        raise _uia_failure(res, "uia_click failed")
    x, y = int(res.get("x", 0)), int(res.get("y", 0))
    if res.get("method") == "coord":
        ctypes.windll.user32.SetCursorPos(x, y)
        down_up = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                   "middle": (0x0020, 0x0040)}[button]
        for flags in down_up:
            _mouse_event(flags)
    return {"ok": True, "method": res.get("method", "?"),
            "name": res.get("name", ""), "x": x, "y": y}


def _validate_uia_target(window, name_rx, text,
                         runtime_id: str = "") -> None:
    if not isinstance(text, str) or not text or len(text) > 2000:
        raise ValueError("text must be a non-empty string up to 2000 chars")
    if runtime_id and not isinstance(runtime_id, str):
        raise ValueError("runtime_id must be a string")
    if not runtime_id and (not isinstance(name_rx, str) or not name_rx):
        raise ValueError("name_rx must be a non-empty regex "
                         "(or pass runtime_id from uia_find)")
    if not isinstance(window, str) or not window.strip():
        raise ValueError("window must be a non-empty title regex or hwnd")


def _uia_get_raw(window, name_rx, control_type, index,
                 runtime_id: str = "") -> dict:
    res = _uia_invoke(_uia_payload(
        "get", window, name_rx, control_type, index,
        runtime_id=runtime_id or ""))
    if not res.get("ok"):
        raise _uia_failure(res, "uia read-back failed")
    return res


def _uia_set_text_verified(window: str, name_rx: str, text: str,
                           control_type: str = "",
                           index: int | None = None,
                           runtime_id: str = "") -> dict:
    """Verified UIA write (no approval — callers approve once).

    Mirrors the CDP fill() contract: after setting, read the field back
    and raise unless it matches exactly. The helper's focus fallback
    verifies UIA FocusedElement == target before this types into it.
    Addressing (runtime_id, else name_rx+index) is identical for the
    set and its read-back, so verification reads the same element.
    """
    res = _uia_invoke(_uia_payload(
        "set", window, name_rx, control_type, index,
        runtime_id=runtime_id or "", text=text))
    if not res.get("ok"):
        raise _uia_failure(res, "uia_set_text failed")
    if res.get("method") == "focus":
        _send_unicode(text)  # helper verified focus is on the target
    after = _uia_get_raw(window, name_rx, control_type, index,
                         runtime_id=runtime_id or "")
    value_after = after.get("value")
    if value_after != text:
        raise RuntimeError(
            f"uia_set_text verification failed: expected {text!r}, "
            f"read back {value_after!r}")
    return {"ok": True, "method": res.get("method", "?"),
            "name": res.get("name", ""), "value_after": value_after,
            "verified": True}


def uia_set_text(window: str, name_rx: str, text: str,
                 control_type: str = "", index: int | None = None,
                 runtime_id: str = "") -> dict:
    """Set text of an edit control in a window, via UI Automation.

    Addressing: runtime_id (from uia_find) wins when given; otherwise
    name_rx + index into the same enumeration uia_find reports.
    Prefers ValuePattern.SetValue (instant, no keystrokes); falls back to
    focusing the element and typing via SendInput KEYEVENTF_UNICODE.
    Verified: the value is read back and a mismatch raises.
    """
    _validate_uia_target(window, name_rx, text, runtime_id=runtime_id)
    preview = text if len(text) <= 120 else text[:120] + "..."
    _approved("uia_set_text",
              "UIA set %d chars into /%s/ in window /%s/:\n%s"
              % (len(text), runtime_id or name_rx, window, preview))
    return _uia_set_text_verified(window, name_rx, text, control_type,
                                  index, runtime_id=runtime_id or "")


def send_message(window: str, name_rx: str, text: str,
                 control_type: str = "", index: int | None = None) -> dict:
    """Send a message through a window's composer control, verified.

    Composite: resolve target -> focus gate -> verified write ->
    Enter -> confirmation. Confirmation requires ALL of: the control's
    value cleared, the sent text appearing in the window's message list
    (a UIA descendant whose name/value carries the text), and focus
    still on the control. Any failed check is an error, not success.
    """
    _validate_uia_target(window, name_rx, text)
    preview = text if len(text) <= 120 else text[:120] + "..."
    _approved("send_message",
              "Send message via /%s/ in window /%s/:\n%s"
              % (name_rx, window, preview))
    info = _uia_get_raw(window, name_rx, control_type, index)
    target_rid = info.get("runtime_id", "")
    _focus_gate(window=window, name_rx=name_rx,
                control_type=control_type, index=index)
    _uia_set_text_verified(window, name_rx, text, control_type, index)
    _tap_vk(_VK["enter"])
    checks = {"value_cleared": False, "in_message_list": False,
              "focus_on_control": False}
    deadline = time.monotonic() + 1.0
    while True:
        after = _uia_get_raw(window, name_rx, control_type, index)
        checks["value_cleared"] = after.get("value") == ""
        # Contains probe: under the tiered name semantics the sent
        # text is not a name match, so the find op collects elements
        # whose name, value, or text contains it (case-sensitive).
        found = _uia_invoke({"op": "find", "window": window,
                             "name_rx": "", "control_type": "",
                             "contains": text,
                             "max_results": UIA_ENUM_MAX_RESULTS,
                             "return_limit": 50})
        checks["in_message_list"] = bool(found.get("ok")) and any(
            text in (el.get("name") or "") or text == el.get("value")
            for el in (found.get("elements") or []))
        foc = _uia_invoke({"op": "focused", "window": window,
                           "name_rx": "", "control_type": "",
                           "index": 0, "max_results": 1})
        checks["focus_on_control"] = bool(
            target_rid and foc.get("ok")
            and foc.get("runtime_id", "") == target_rid)
        if all(checks.values()) or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    if not all(checks.values()):
        failed = [k for k, v in checks.items() if not v]
        raise RuntimeError(
            f"send_message confirmation failed: {failed}; checks={checks}")
    return {"ok": True, "verified": True, "window": window,
            "name": info.get("name", ""), "checks": checks}
