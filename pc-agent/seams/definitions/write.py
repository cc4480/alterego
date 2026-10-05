"""Service Definitions for write/control tools (group: write).

Every tool here requires native on-PC approval (fail closed).
"""
from seams.definitions.base import ToolDef

FOCUS_WINDOW = ToolDef(
    name="focus_window",
    group="write",
    doc="Bring a window to the foreground. Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "focused"],
    approval_tier="routine",
    side_effects="session",
)

CLOSE_WINDOW = ToolDef(
    name="close_window",
    group="write",
    doc="Gracefully close a window (like clicking X). Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "close_requested"],
    approval_tier="ask",
    side_effects="session",
)

TYPE_TEXT = ToolDef(
    name="type_text",
    group="write",
    doc="Type text into the focused window. Requires on-PC approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["typed_chars"],
    approval_tier="ask",
    side_effects="session",
)

SHELL_EXEC = ToolDef(
    name="shell_exec",
    group="write",
    doc="Run a command in cmd.exe. Requires on-PC approval.",
    args={"command": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 60, "required": False}},
    result_keys=["command", "returncode", "stdout", "stderr", "timed_out"],
    approval_tier="always_ask",
    side_effects="system",
)

HOTKEY = ToolDef(
    name="hotkey",
    group="write",
    doc="Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'.",
    args={"keys": {"type": "str", "required": True}},
    result_keys=["hotkey", "pressed"],
    approval_tier="routine",
    side_effects="session",
)

MOUSE_MOVE = ToolDef(
    name="mouse_move",
    group="write",
    doc="Move the cursor to screen coordinates. Requires on-PC approval.",
    args={"x": {"type": "int", "required": True},
          "y": {"type": "int", "required": True}},
    result_keys=["x", "y"],
    approval_tier="silent",
    side_effects="session",
)

MOUSE_CLICK = ToolDef(
    name="mouse_click",
    group="write",
    doc="Click at screen coordinates. Requires on-PC approval.",
    args={"x": {"type": "int", "required": True},
          "y": {"type": "int", "required": True},
          "button": {"type": "str", "default": "left", "required": False}},
    result_keys=["x", "y", "button", "clicked"],
    approval_tier="routine",
    side_effects="session",
)

MOUSE_SCROLL = ToolDef(
    name="mouse_scroll",
    group="write",
    doc="Scroll the wheel under the cursor. Requires on-PC approval.",
    args={"direction": {"type": "str", "default": "down",
                        "required": False},
          "clicks": {"type": "int", "default": 3, "required": False}},
    result_keys=["direction", "clicks"],
    approval_tier="routine",
    side_effects="session",
)

MINIMIZE_WINDOW = ToolDef(
    name="minimize_window",
    group="write",
    doc="Minimize a window. Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "minimized", "was_visible"],
    approval_tier="routine",
    side_effects="session",
)

MAXIMIZE_WINDOW = ToolDef(
    name="maximize_window",
    group="write",
    doc="Maximize a window. Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "maximized", "was_visible"],
    approval_tier="routine",
    side_effects="session",
)

KILL_PROCESS = ToolDef(
    name="kill_process",
    group="write",
    doc="Terminate a process by PID. Requires on-PC approval.",
    args={"pid": {"type": "int", "required": True}},
    result_keys=["pid", "terminated"],
    approval_tier="always_ask",
    side_effects="system",
)

CLIPBOARD_SET = ToolDef(
    name="clipboard_set",
    group="write",
    doc="Put text on the clipboard. Requires on-PC approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["clipboard_chars"],
    approval_tier="routine",
    side_effects="session",
)

PASTE_TEXT = ToolDef(
    name="paste_text",
    group="write",
    doc="Paste text into the focused window (reliable for long text).",
    args={"text": {"type": "str", "required": True}},
    result_keys=["pasted_chars"],
    approval_tier="ask",
    side_effects="session",
)

ALL_WRITE_DEFS = [FOCUS_WINDOW, CLOSE_WINDOW, TYPE_TEXT, SHELL_EXEC, HOTKEY,
                  MOUSE_MOVE, MOUSE_CLICK, MOUSE_SCROLL, MINIMIZE_WINDOW,
                  MAXIMIZE_WINDOW, KILL_PROCESS, CLIPBOARD_SET, PASTE_TEXT]
