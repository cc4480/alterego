"""Mock write/control tools: window, mouse, keyboard, shell, clipboard.

The mock never shows approval dialogs (no display on CI) — it auto-approves
by construction. UI/input actions return success and are journaled in the
mock CALLS list so tests can assert what would have happened.
"""
from seams.providers.mock import CLIPBOARD, PROCS, record


def _ui(tool: str, args: dict, result: dict) -> dict:
    record(tool, args)
    return result


def focus_window(hwnd: int) -> dict:
    return _ui("focus_window", {"hwnd": hwnd},
               {"hwnd": hwnd, "focused": True})


def close_window(hwnd: int) -> dict:
    return _ui("close_window", {"hwnd": hwnd},
               {"hwnd": hwnd, "close_requested": True})


def type_text(text: str) -> dict:
    return _ui("type_text", {"text": text},
               {"typed_chars": len(text)})


def shell_exec(command: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Mock shell: `echo <text>` returns the text; anything else returns
    canned output. NOTHING is ever executed — the command string is data."""
    record("shell_exec", {"command": command, "timeout_s": timeout_s,
                          "cwd": cwd})
    stripped = command.strip()
    if stripped == "echo":
        stdout = ""
    elif stripped.startswith("echo "):
        stdout = stripped[5:]
    else:
        stdout = "mock output"
    return {"command": command, "returncode": 0, "stdout": stdout,
            "stderr": "", "timed_out": False}


def hotkey(keys: str) -> dict:
    return _ui("hotkey", {"keys": keys},
               {"hotkey": keys, "pressed": True})


def mouse_move(x: int, y: int) -> dict:
    return _ui("mouse_move", {"x": x, "y": y}, {"x": x, "y": y})


def mouse_click(x: int, y: int, button: str = "left") -> dict:
    return _ui("mouse_click", {"x": x, "y": y, "button": button},
               {"x": x, "y": y, "button": button, "clicked": True})


def mouse_scroll(direction: str = "down", clicks: int = 3) -> dict:
    return _ui("mouse_scroll", {"direction": direction, "clicks": clicks},
               {"direction": direction, "clicks": clicks})


def minimize_window(hwnd: int) -> dict:
    return _ui("minimize_window", {"hwnd": hwnd},
               {"hwnd": hwnd, "minimized": True, "was_visible": True})


def maximize_window(hwnd: int) -> dict:
    return _ui("maximize_window", {"hwnd": hwnd},
               {"hwnd": hwnd, "maximized": True, "was_visible": True})


def kill_process(pid: int) -> dict:
    """Remove the pid from the MOCK process list only. No real process is
    ever signaled — the mock has no access to the host process table."""
    record("kill_process", {"pid": pid})
    before = len(PROCS)
    PROCS[:] = [p for p in PROCS if p["pid"] != pid]
    return {"pid": pid, "terminated": len(PROCS) < before}


def clipboard_set(text: str) -> dict:
    CLIPBOARD["text"] = text
    record("clipboard_set", {"text": text})
    return {"clipboard_chars": len(text)}


def paste_text(text: str) -> dict:
    return _ui("paste_text", {"text": text},
               {"pasted_chars": len(text)})
