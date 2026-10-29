"""Service Definitions for read-only observation tools (group: read)."""
from seams.definitions.base import ToolDef

SCREENSHOT = ToolDef(
    name="screenshot",
    group="read",
    doc="Capture the virtual all-monitors screen (base64). Optional "
        "region {x, y, width, height} captures part of the screen. "
        "scale (0.1-1.0) downscales via Pillow; when omitted, the long "
        "edge is fitted to ~1280px (explicit 1.0 = native resolution). "
        "format: 'webp' (default, lossless), 'jpeg' (quality 95) or "
        "'png'. png_base64 is the legacy field, populated only for "
        "format='png' (None otherwise); image_base64 always carries "
        "the encoded bytes.",
    args={"region": {"type": "dict", "required": False,
                     "default": None},
          "scale": {"type": "float", "required": False,
                    "default": None},
          "format": {"type": "str", "required": False,
                     "default": "webp"}},
    result_keys=["image_base64", "mime", "format", "width", "height",
                 "scale", "png_base64"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="mss is cross-platform (Windows/macOS/Linux) — a Linux "
                   "provider can reuse the same code. macOS needs "
                   "screen-recording permission; headless servers have no "
                   "display to capture. Region/scale/format are pure "
                   "post-processing (Pillow), fully portable.",
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

UIA_FIND = ToolDef(
    name="uia_find",
    group="read",
    doc="Find UI elements in a window via Windows UI Automation — match "
        "by name regex and/or control type. Returns name, control type, "
        "automation id, and bounding rect for each match. No screenshots "
        "needed. window: window title regex (or hwnd digits).",
    args={"window": {"type": "str", "required": True},
          "name_rx": {"type": "str", "required": False, "default": ""},
          "control_type": {"type": "str", "required": False, "default": ""},
          "max_results": {"type": "int", "required": False, "default": 50}},
    result_keys=["ok", "count", "elements"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows-only: UIAutomationClient via PowerShell. "
                   "Elements carry value, has_keyboard_focus and "
                   "runtime_id alongside name/type/rect. Honest gap on "
                   "other platforms — omit rather than fake.",
)

UIA_GET = ToolDef(
    name="uia_get",
    group="read",
    doc="Read one UI element in a window via Windows UI Automation — "
        "same addressing as uia_find/uia_set_text (name regex + optional "
        "control type + index). Returns the element's value/text, "
        "has_keyboard_focus, runtime_id, and available patterns. This "
        "is the read-back half of the verified-write contract. window: "
        "window title regex (or hwnd digits).",
    args={"window": {"type": "str", "required": True},
          "name_rx": {"type": "str", "required": False, "default": ""},
          "control_type": {"type": "str", "required": False, "default": ""},
          "index": {"type": "int", "required": False, "default": 0}},
    result_keys=["ok", "name", "control_type", "automation_id", "value",
                 "text", "has_keyboard_focus", "runtime_id", "patterns",
                 "x", "y", "width", "height"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Windows-only: UIAutomationClient via PowerShell. "
                   "Honest gap on other platforms — omit rather than fake.",
)

SCREENSHOT_TILES = ToolDef(
    name="screenshot_tiles",
    group="read",
    doc="Capture the screen as raw-RGB tiles stored in the blob store. "
        "Returns a manifest {grid, tile_size, tile_width, tile_height, "
        "width, height, full_frame_sha256, delta, tiles}; each tile entry "
        "carries {index, x, y, width, height, blob_ref, size, sha256} "
        "(fetch bytes via GET /blobs/<sha>). delta=True returns only "
        "changed tiles (plus changed/unchanged index lists) against the "
        "previous frame; a grid/region/resolution change falls back to a "
        "full manifest. Clients verify full_frame_sha256 to detect "
        "desync and self-heal with one full frame.",
    args={"region": {"type": "dict", "required": False,
                     "default": None},
          "tile_size": {"type": "int", "required": False,
                        "default": 512},
          "grid": {"type": "dict", "required": False,
                   "default": None},
          "delta": {"type": "bool", "required": False,
                    "default": False}},
    result_keys=["grid", "tile_size", "tile_width", "tile_height",
                 "width", "height", "full_frame_sha256", "delta",
                 "tiles"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="mss is cross-platform; tiles are raw RGB bytes so "
                   "hashing/delta needs no image codec. Windows provider "
                   "stores tiles via blob_store; the mock keeps them in "
                   "memory under the same refs.",
)

ALL_READ_DEFS = [SCREENSHOT, LIST_WINDOWS, SYSTEM_INFO, LIST_DIR,
                 READ_FILE, CLIPBOARD_GET, SEARCH_FILES,
                 SEARCH_FILENAMES, READ_FILE_RANGE, UIA_FIND, UIA_GET,
                 SCREENSHOT_TILES]
