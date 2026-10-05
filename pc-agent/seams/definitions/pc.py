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
)

IDLE_SECONDS = ToolDef(
    name="idle_seconds",
    group="pc",
    doc="Seconds since last keyboard/mouse input. No approval.",
    args={},
    result_keys=["idle_seconds"],
    approval_tier="silent",
    side_effects="none",
)

LIST_PROCESSES = ToolDef(
    name="list_processes",
    group="pc",
    doc="Running processes (pid + exe). No approval.",
    args={},
    result_keys=["count", "processes"],
    approval_tier="silent",
    side_effects="none",
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
)

SPEAK = ToolDef(
    name="speak",
    group="pc",
    doc="Speak text aloud through the PC speakers. Requires approval.",
    args={"text": {"type": "str", "required": True}},
    result_keys=["spoken_chars"],
    approval_tier="routine",
    side_effects="session",
)

SET_VOLUME = ToolDef(
    name="set_volume",
    group="pc",
    doc="Set master volume 0-100. Requires approval.",
    args={"level": {"type": "int", "required": True}},
    result_keys=["volume"],
    approval_tier="routine",
    side_effects="session",
)

POWER = ToolDef(
    name="power",
    group="pc",
    doc="lock | sleep | restart | shutdown. Requires approval.",
    args={"action": {"type": "str", "required": True}},
    result_keys=["power"],
    approval_tier="always_ask",
    side_effects="system",
)

ALL_PC_DEFS = [ACTIVE_WINDOW, IDLE_SECONDS, LIST_PROCESSES, NOTIFY,
               SPEAK, SET_VOLUME, POWER]
