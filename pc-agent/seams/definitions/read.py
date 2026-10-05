"""Service Definitions for read-only observation tools (group: read)."""
from seams.definitions.base import ToolDef

SCREENSHOT = ToolDef(
    name="screenshot",
    group="read",
    doc="Capture the primary monitor as PNG (base64).",
    args={},
    result_keys=["png_base64", "width", "height"],
    approval_tier="silent",
    side_effects="none",
)

LIST_WINDOWS = ToolDef(
    name="list_windows",
    group="read",
    doc="List visible windows: handle, pid, title.",
    args={},
    result_keys=["windows"],
    approval_tier="silent",
    side_effects="none",
)

SYSTEM_INFO = ToolDef(
    name="system_info",
    group="read",
    doc="OS, host, user, CPU/memory.",
    args={},
    result_keys=["system", "release", "version", "machine", "hostname",
                 "user", "cpu_percent", "mem_percent"],
    approval_tier="silent",
    side_effects="none",
)

LIST_DIR = ToolDef(
    name="list_dir",
    group="read",
    doc="List a directory. Restricted to the user's profile.",
    args={"path": {"type": "str", "default": "", "required": False}},
    result_keys=["path", "items"],
    approval_tier="silent",
    side_effects="none",
)

READ_FILE = ToolDef(
    name="read_file",
    group="read",
    doc="Read a UTF-8 text file (<=1MB). Restricted to the user's profile.",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "content"],
    approval_tier="silent",
    side_effects="none",
)

CLIPBOARD_GET = ToolDef(
    name="clipboard_get",
    group="read",
    doc="Read text from the clipboard (no approval; it's a read).",
    args={},
    result_keys=["text", "note"],
    approval_tier="silent",
    side_effects="none",
)

ALL_READ_DEFS = [SCREENSHOT, LIST_WINDOWS, SYSTEM_INFO, LIST_DIR,
                 READ_FILE, CLIPBOARD_GET]
