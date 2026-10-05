"""Service Definitions for PC awareness/control tools (group: pc)."""
from seams.definitions.base import ToolDef

ACTIVE_WINDOW = ToolDef(
    name="active_window",
    group="pc",
    doc="Foreground window: hwnd, title, pid, process. No approval.",
    args={},
    result_keys=["hwnd", "title", "pid", "process"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows: GetForegroundWindow. Linux/X11: ewmh "
                   "active-window; Wayland: no standard API — honest gap. "
                   "macOS: accessibility API.",
)

IDLE_SECONDS = ToolDef(
    name="idle_seconds",
    group="pc",
    doc="Seconds since last keyboard/mouse input. No approval.",
    args={},
    result_keys=["idle_seconds"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows: GetLastInputInfo. Linux/X11: XScreenSaver "
                   "extension; Wayland gap. macOS: IOHIDSystem idle time.",
)

LIST_PROCESSES = ToolDef(
    name="list_processes",
    group="pc",
    doc="Running processes (pid + exe). No approval.",
    args={},
    result_keys=["count", "processes"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows: Toolhelp32 snapshot (ctypes). Linux: /proc "
                   "parsing (stdlib) or psutil; macOS: psutil. psutil is "
                   "the clean cross-platform route — the ctypes snapshot "
                   "is Windows-only by choice, not necessity.",
)

NOTIFY = ToolDef(
    name="notify",
    group="pc",
    doc="Tray balloon notification. Requires approval.",
    args={"title": {"type": "str", "required": True},
          "message": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 5, "required": False}},
    result_keys=["notified", "title"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: tray balloon (WinForms NotifyIcon). Linux: "
                   "notify-send (best-effort; missing binary -> clear "
                   "error). macOS: osascript display notification. The "
                   "1-30s timeout clamping is a contract detail that ports.",
)

SPEAK = ToolDef(
    name="speak",
    group="pc",
    doc="Speak text aloud through the PC speakers. Requires approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["spoken_chars"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: SAPI via PowerShell. Linux: espeak-ng or "
                   "spd-say (best-effort). macOS: say. The 1-500 char cap "
                   "ports.",
)

SET_VOLUME = ToolDef(
    name="set_volume",
    group="pc",
    doc="Set master volume 0-100. Requires approval.",
    args={"level": {"type": "int", "required": True}},
    result_keys=["volume"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Windows: waveOutSetVolume. Linux: pactl/amixer "
                   "(PulseAudio/PipeWire/ALSA differ — the provider "
                   "documents which). macOS: osascript set volume. The "
                   "0-100 scale is the portable contract.",
)

POWER = ToolDef(
    name="power",
    group="pc",
    doc="lock | sleep | restart | shutdown. Requires approval.",
    args={"action": {"type": "str", "required": True}},
    result_keys=["power"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="Windows: LockWorkStation / SetSuspendState / "
                   "shutdown.exe. Linux: loginctl/systemctl. macOS: pmset "
                   "/ osascript. Same approval tier (always_ask) on every "
                   "platform — the blast radius doesn't change.",
)

ALL_PC_DEFS = [ACTIVE_WINDOW, IDLE_SECONDS, LIST_PROCESSES, NOTIFY,
               SPEAK, SET_VOLUME, POWER]
