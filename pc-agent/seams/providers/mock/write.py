"""Mock write/control tools: window, mouse, keyboard, shell, clipboard.

The mock never shows approval dialogs (no display on CI) — it auto-approves
by construction. UI/input actions return success and are journaled in the
mock CALLS list so tests can assert what would have happened.
"""
from seams.providers.mock import (
    CLIPBOARD, PROCS, UIA, find_uia_element, focused_uia_element,
    record, resolve_uia_window,
)


def _ui(tool: str, args: dict, result: dict) -> dict:
    record(tool, args)
    return result


def focus_window(hwnd: int) -> dict:
    """Mock focus, mirroring the Windows contract: when UIA
    ["focus_fail"] is set the foreground stays on the competing
    window and the op raises, naming the actual foreground window."""
    hwnd = int(hwnd)
    if UIA["focus_fail"]:
        actual = UIA["foreground_hwnd"]
        title = UIA["window_titles"].get(actual, "")
        raise RuntimeError(
            f"focus_window failed: hwnd {hwnd} did not become "
            f"foreground; actual foreground is hwnd {actual} "
            f"({title!r}); SetForegroundWindow returned 0")
    record("focus_window", {"hwnd": hwnd})
    UIA["foreground_hwnd"] = hwnd
    return {"hwnd": hwnd, "focused": True}


def _focus_gate(window=None, name_rx: str = "", control_type: str = "",
                index: int = 0) -> dict:
    """Mock focus gate, same contract as the Windows provider: raise
    (typing nothing) unless the foreground hwnd is the target window
    and the focused element's RuntimeId is the target element's."""
    target_hwnd = resolve_uia_window(window) if window else None
    target_rid = ""
    if name_rx:
        target_rid = find_uia_element(
            name_rx, control_type, index)["runtime_id"]
        if target_hwnd is None:
            target_hwnd = 12345  # fixture elements live in Mock Window
    fg = UIA["foreground_hwnd"]
    rid = UIA["focused_runtime_id"]
    if (target_hwnd is not None and fg != target_hwnd) or \
            (target_rid and rid != target_rid):
        title = UIA["window_titles"].get(fg, "")
        raise RuntimeError(
            f"focus gate failed: foreground hwnd {fg} ({title!r}) vs "
            f"target hwnd {target_hwnd}; focused element RuntimeId "
            f"{rid!r} vs target {target_rid!r} — aborting, "
            f"nothing typed")
    return {"hwnd": target_hwnd, "runtime_id": target_rid}


def close_window(hwnd: int) -> dict:
    return _ui("close_window", {"hwnd": hwnd},
               {"hwnd": hwnd, "close_requested": True})


def _target_record(base: dict, window, name_rx, control_type,
                   index) -> dict:
    args = dict(base)
    if window is not None:
        args["window"] = window
    if name_rx:
        args["name_rx"] = name_rx
    if control_type:
        args["control_type"] = control_type
    if index:
        args["index"] = index
    return args


def type_text(text: str, window: str | None = None, name_rx: str = "",
              control_type: str = "", index: int = 0) -> dict:
    targeted = window is not None or bool(name_rx)
    if not targeted:
        return _ui("type_text", {"text": text},
                   {"typed_chars": len(text), "verified": False})
    _focus_gate(window, name_rx, control_type, index)
    if name_rx:
        # Typing lands in the addressed element; spot read-back.
        el = find_uia_element(name_rx, control_type, index)
        el["value"] = (el["value"] or "") + text
        if text not in el["value"]:
            raise RuntimeError(
                "type_text verification failed: typed text not in "
                f"target value (read back {el['value']!r})")
    args = _target_record({"text": text}, window, name_rx,
                          control_type, index)
    return _ui("type_text", args,
               {"typed_chars": len(text), "verified": True})


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


def hotkey(keys: str, window: str | None = None, name_rx: str = "",
           control_type: str = "", index: int = 0) -> dict:
    targeted = window is not None or bool(name_rx)
    if targeted:
        _focus_gate(window, name_rx, control_type, index)
    args = _target_record({"keys": keys}, window, name_rx,
                          control_type, index)
    return _ui("hotkey", args,
               {"hotkey": keys, "pressed": True, "verified": targeted})


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
    """Mock paste with the verified-write bar: the paste lands in the
    focused element, then it is read back — verified only when a
    value/text-bearing focused element is resolvable AND its value
    contains the pasted text. UIA["paste_corrupt"] makes different
    text land; an unresolvable focus (unknown runtime_id, or a
    focused element without Value/Text patterns) is verified=False."""
    el = focused_uia_element()
    resolvable = el is not None and bool(
        {"ValuePattern", "TextPattern"} & set(el.get("patterns") or []))
    if resolvable:
        landed = "<different text>" if UIA["paste_corrupt"] else text
        el["value"] = (el["value"] or "") + landed
    verified = bool(resolvable and text in (el["value"] or ""))
    return _ui("paste_text", {"text": text},
               {"pasted_chars": len(text), "verified": verified})


def uia_click(window: str, name_rx: str = "", control_type: str = "",
              index: int = 0, button: str = "left",
              runtime_id: str = "") -> dict:
    """Mock click: resolves the element with the same addressing as
    the Windows helper (runtime_id wins; else name/index into the
    uia_find enumeration) and reports its name + centre coordinates.
    An address that does not resolve raises, like the helper."""
    resolve_uia_window(window)
    el = find_uia_element(name_rx, control_type, index,
                          runtime_id=runtime_id)
    args = {"window": window, "name_rx": name_rx,
            "control_type": control_type, "index": index,
            "button": button}
    if runtime_id:
        args["runtime_id"] = runtime_id
    return _ui("uia_click", args,
               {"ok": True, "method": "invoke", "name": el["name"],
                "x": el["x"] + el["width"] // 2,
                "y": el["y"] + el["height"] // 2})


def uia_set_text(window: str, name_rx: str, text: str,
                 control_type: str = "", index: int = 0,
                 runtime_id: str = "") -> dict:
    """Verified mock set: writes the element's value, reads it back,
    and raises on mismatch (an element with corrupt_write=True lands
    doubled text — the read-back must catch it). Addressing matches
    uia_click: runtime_id wins when given."""
    resolve_uia_window(window)
    el = find_uia_element(name_rx, control_type, index,
                          runtime_id=runtime_id)
    el["value"] = text + text if el.get("corrupt_write") else text
    value_after = el["value"]
    args = {"window": window, "name_rx": name_rx, "text": text,
            "control_type": control_type, "index": index}
    if runtime_id:
        args["runtime_id"] = runtime_id
    record("uia_set_text", args)
    if value_after != text:
        raise RuntimeError(
            f"uia_set_text verification failed: expected {text!r}, "
            f"read back {value_after!r}")
    return {"ok": True, "method": "value", "name": el["name"],
            "value_after": value_after, "verified": True}


def send_message(window: str, name_rx: str, text: str,
                 control_type: str = "", index: int = 0) -> dict:
    """Mock composite, same contract as the Windows provider:
    focus gate -> verified write -> Enter -> confirmation (value
    cleared + text in the message list + focus still on the control).
    Any failed check raises; a disturbed gate types nothing."""
    resolve_uia_window(window)
    el = find_uia_element(name_rx, control_type, index)
    _focus_gate(window, name_rx, control_type, index)
    # Verified write (read-back included).
    uia_set_text(window, name_rx, text, control_type, index)
    record("send_message",
           {"window": window, "name_rx": name_rx, "text": text,
            "control_type": control_type, "index": index})
    # Enter: the app moves the composer text into the message list.
    if not UIA["drop_sent_message"]:
        UIA["messages"].append(el["value"])
    el["value"] = ""
    checks = {"value_cleared": el["value"] == "",
              "in_message_list": text in UIA["messages"],
              "focus_on_control":
                  UIA["focused_runtime_id"] == el["runtime_id"]}
    if not all(checks.values()):
        failed = [k for k, v in checks.items() if not v]
        raise RuntimeError(
            f"send_message confirmation failed: {failed}; "
            f"checks={checks}")
    return {"ok": True, "verified": True, "window": window,
            "name": el["name"], "checks": checks}
