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
    platform_notes="Windows: SetForegroundWindow (+AttachThreadInput to "
                   "bypass the foreground lock). Linux/X11: ewmh/wmctrl. "
                   "Wayland: no standard API — honest gap. macOS: "
                   "NSWorkspace/AppleScript (accessibility permission).",
)

CLOSE_WINDOW = ToolDef(
    name="close_window",
    group="write",
    doc="Gracefully close a window (like clicking X). Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "close_requested"],
    approval_tier="ask",
    side_effects="session",
    platform_notes="Windows: WM_CLOSE (graceful, apps show save dialogs). "
                   "Linux/X11: close via ewmh; Wayland gap. macOS: "
                   "AppleScript quit. The 'graceful close' semantic ports; "
                   "the mechanism doesn't.",
)

TYPE_TEXT = ToolDef(
    name="type_text",
    group="write",
    doc="Type text into the focused window. Requires on-PC approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["typed_chars"],
    approval_tier="ask",
    side_effects="session",
    platform_notes="Windows: SendInput with KEYEVENTF_UNICODE (full "
                   "Unicode). Linux: pynput or uinput (needs X11 or uinput "
                   "permissions). macOS: Quartz event taps (accessibility "
                   "permission). paste_text is the reliable route for long "
                   "text on any platform.",
)

# Branch shapes (provider returns one or the other, never both):
#   success -> {command, returncode, stdout, stderr}
#   timeout -> {command, timed_out, stdout, stderr}
# result_keys is the union of the branches.
SHELL_EXEC = ToolDef(
    name="shell_exec",
    group="write",
    doc="Run a command in cmd.exe. Optional cwd (must be inside the user "
        "profile). Requires on-PC approval.",
    args={"command": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 60, "required": False},
          "cwd": {"type": "str", "required": False,
                  "default": None}},
    result_keys=["command", "returncode", "stdout", "stderr", "timed_out"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="The shell is platform-native: cmd.exe on Windows, "
                   "bash/sh on Linux, zsh on macOS. The contract shape "
                   "ports; the command vocabulary does not — a provider "
                   "must document which shell it runs.",
)

HOTKEY = ToolDef(
    name="hotkey",
    group="write",
    doc="Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'.",
    args={"keys": {"type": "str", "required": True}},
    result_keys=["hotkey", "pressed"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: virtual-key codes via SendInput. Linux: pynput "
                   "combos (X11 keycodes; uinput needs permissions). macOS: "
                   "Quartz key events. The key-name vocabulary in the "
                   "definition is Windows-flavored ('win+r', 'alt+f4') — a "
                   "non-Windows provider documents its own names, never "
                   "fakes them.",
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
    platform_notes="Windows: SetCursorPos (physical pixels, DPI-aware). "
                   "Linux/X11: XWarpPointer via pynput/ewmh; Wayland "
                   "compositors restrict synthetic input — gap. macOS: "
                   "Quartz CGWarpMouseCursorPosition (accessibility "
                   "permission). Physical-pixel coordinates everywhere.",
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
    platform_notes="Same platform story as mouse_move; the button "
                   "vocabulary (left/right/middle) is universal.",
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
    platform_notes="Windows: MOUSEEVENTF_WHEEL. Linux: pynput scroll or X11 "
                   "buttons 4/5; Wayland gap. macOS: Quartz scroll events. "
                   "The direction vocabulary (up/down) is universal.",
)

MINIMIZE_WINDOW = ToolDef(
    name="minimize_window",
    group="write",
    doc="Minimize a window. Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "minimized", "was_visible"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: ShowWindow(SW_MINIMIZE). Linux/X11: "
                   "ewmh/wmctrl; Wayland gap. macOS: AppleScript/Quartz.",
)

MAXIMIZE_WINDOW = ToolDef(
    name="maximize_window",
    group="write",
    doc="Maximize a window. Requires on-PC approval.",
    args={"hwnd": {"type": "int", "required": True}},
    result_keys=["hwnd", "maximized", "was_visible"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: ShowWindow(SW_MAXIMIZE). Linux/X11: "
                   "ewmh/wmctrl; Wayland gap. macOS: AppleScript/Quartz.",
)

KILL_PROCESS = ToolDef(
    name="kill_process",
    group="write",
    doc="Terminate a process by PID. Requires on-PC approval.",
    args={"pid": {"type": "int", "required": True}},
    result_keys=["pid", "terminated"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="Windows: TerminateProcess (forceful). Linux/macOS: "
                   "os.kill / SIGTERM-then-SIGKILL (POSIX). The "
                   "'never kill the bridge itself' guard and the "
                   "no-graceful-shutdown semantic port verbatim.",
)

CLIPBOARD_SET = ToolDef(
    name="clipboard_set",
    group="write",
    doc="Put text on the clipboard. Requires on-PC approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["clipboard_chars"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: win32 clipboard. Linux: xclip/xsel "
                   "(X11) or wl-clipboard (Wayland). macOS: pbcopy. The "
                   "100k-char cap is a contract detail that ports.",
)

PASTE_TEXT = ToolDef(
    name="paste_text",
    group="write",
    doc="Paste text into the focused window (reliable for long text).",
    args={"text": {"type": "str", "required": True}},
    result_keys=["pasted_chars"],
    approval_tier="ask",
    side_effects="session",
    platform_notes="Windows recipe: set clipboard + Ctrl+V via SendInput, "
                   "then restore the prior clipboard. Any platform with "
                   "clipboard-set + key-synthesis can implement the same "
                   "recipe; inherits the gaps of clipboard_set and hotkey.",
)

ALL_WRITE_DEFS = [FOCUS_WINDOW, CLOSE_WINDOW, TYPE_TEXT, SHELL_EXEC, HOTKEY,
                  MOUSE_MOVE, MOUSE_CLICK, MOUSE_SCROLL, MINIMIZE_WINDOW,
                  MAXIMIZE_WINDOW, KILL_PROCESS, CLIPBOARD_SET, PASTE_TEXT]
