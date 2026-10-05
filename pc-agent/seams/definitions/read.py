"""Service Definitions for read-only observation tools (group: read)."""
from seams.definitions.base import ToolDef

SCREENSHOT = ToolDef(
    name="screenshot",
    group="read",
    doc="Capture the primary monitor as PNG (base64). Optional region "
        "{x, y, width, height} captures part of the screen; optional "
        "scale (0.1-1.0) downscales the image to save bandwidth.",
    args={"region": {"type": "dict", "required": False,
                     "default": None},
          "scale": {"type": "float", "required": False,
                    "default": 1.0}},
    result_keys=["png_base64", "width", "height"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="mss is cross-platform (Windows/macOS/Linux) — a Linux "
                   "provider can reuse the same code. macOS needs "
                   "screen-recording permission; headless servers have no "
                   "display to capture. Region/scale are pure "
                   "post-processing (PIL), fully portable.",
)

LIST_WINDOWS = ToolDef(
    name="list_windows",
    group="read",
    doc="List visible windows: handle, pid, title.",
    args={},
    result_keys=["windows"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows: win32gui EnumWindows. Linux/X11: ewmh or "
                   "wmctrl. Wayland: no standard window-management API — "
                   "honest gap, omit rather than fake. macOS: "
                   "CGWindowListCopyWindowInfo (accessibility permission).",
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
    platform_notes="Fully portable: platform.uname() is stdlib, psutil is "
                   "cross-platform. No known gaps.",
)

LIST_DIR = ToolDef(
    name="list_dir",
    group="read",
    doc="List a directory. Restricted to the user's profile.",
    args={"path": {"type": "str", "default": "", "required": False}},
    result_keys=["path", "items"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Fully portable: pathlib. The profile-root constant is a "
                   "provider-level setting (%USERPROFILE% vs $HOME), not a "
                   "gap.",
)

READ_FILE = ToolDef(
    name="read_file",
    group="read",
    doc="Read a UTF-8 text file (<=1MB). Restricted to the user's profile. "
        "Returns sha256 of the content for staleness-checked writes.",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "content", "sha256"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Fully portable: pathlib. Same profile-root note as "
                   "list_dir; the 1MB cap is a contract detail that ports.",
)

CLIPBOARD_GET = ToolDef(
    name="clipboard_get",
    group="read",
    doc="Read text from the clipboard (no approval; it's a read).",
    args={},
    result_keys=["text", "note"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows: win32 clipboard. Linux: xclip/xsel (X11) or "
                   "wl-clipboard (Wayland) — new dep; clipboard access on "
                   "Wayland is per-app sandboxed, fail loud if unavailable. "
                   "macOS: pbpaste.",
)

SEARCH_FILES = ToolDef(
    name="search_files",
    group="read",
    doc="Search file contents for a regex/plain-text pattern under a "
        "directory. Restricted to the user's profile. context: lines of "
        "context around each match. case_insensitive: ignore case. "
        "output_mode: 'matches' (default), 'files' (unique file paths), "
        "or 'count' (match count per file). offset: skip first N results.",
    args={
        "pattern": {"type": "str", "required": True},
        "path": {"type": "str", "required": True},
        "file_pattern": {"type": "str", "required": False,
                         "default": "*"},
        "max_results": {"type": "int", "required": False, "default": 50},
        "context": {"type": "int", "required": False, "default": 0},
        "case_insensitive": {"type": "bool", "required": False,
                             "default": False},
        "output_mode": {"type": "str", "required": False,
                        "default": "matches"},
        "offset": {"type": "int", "required": False, "default": 0},
    },
    result_keys=["matches", "truncated"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Fully portable: os.walk + re are stdlib. Same "
                   "profile-root restriction as list_dir; binary files "
                   "are skipped (UTF-8 decode check). The Windows "
                   "provider shells out to rg (ripgrep) when installed "
                   "for speed, else falls back to the stdlib walk. "
                   "output_mode changes the shape of 'matches' entries: "
                   "full match dicts, {'file'} dicts, or "
                   "{'file', 'count'} dicts.",
)

SEARCH_FILENAMES = ToolDef(
    name="search_filenames",
    group="read",
    doc="Find files by name glob (e.g. '*.log') under a directory. "
        "Restricted to the user's profile. sort_by: 'name' (default) or "
        "'mtime' (newest first). offset: skip first N results.",
    args={
        "pattern": {"type": "str", "required": True},
        "path": {"type": "str", "required": True},
        "max_results": {"type": "int", "required": False, "default": 50},
        "sort_by": {"type": "str", "required": False, "default": "name"},
        "offset": {"type": "int", "required": False, "default": 0},
    },
    result_keys=["files", "truncated"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Fully portable: pathlib rglob. Same profile-root "
                   "restriction as list_dir.",
)

READ_FILE_RANGE = ToolDef(
    name="read_file_range",
    group="read",
    doc="Read specific 1-indexed line ranges from a UTF-8 text file. "
        "Restricted to the user's profile. Returns sha256 of the whole "
        "file content for staleness-checked writes.",
    args={
        "path": {"type": "str", "required": True},
        "start_line": {"type": "int", "required": True},
        "end_line": {"type": "int", "required": False, "default": 0},
    },
    result_keys=["path", "start_line", "end_line", "total_lines",
                 "content", "sha256"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Fully portable: pathlib. end_line=0 (or omitted) "
                   "means start_line+50. Same profile-root restriction "
                   "and 1MB cap as read_file.",
)

ALL_READ_DEFS = [SCREENSHOT, LIST_WINDOWS, SYSTEM_INFO, LIST_DIR,
                 READ_FILE, CLIPBOARD_GET, SEARCH_FILES,
                 SEARCH_FILENAMES, READ_FILE_RANGE]
