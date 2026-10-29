"""Read-only tool wrappers.

Split from tool_wrappers.py to keep files under 300 lines.
Each wrapper delegates to toolcall.call for audit logging.
"""
import toolcall


def screenshot(region: dict | None = None, scale: float | None = None,
               format: str = "webp") -> dict:
    """Capture the screen (base64). Optional region; scale omitted =
    auto-fit long edge to ~1280px (explicit 1.0 = native). format:
    'webp' (default, lossless), 'jpeg' or 'png'."""
    a = {"format": format}
    if region is not None:
        a["region"] = region
    if scale is not None:
        a["scale"] = scale
    return toolcall.call("screenshot", a)

def screenshot_tiles(region: dict | None = None, tile_size: int = 512,
                     grid: dict | None = None,
                     delta: bool = False) -> dict:
    """Capture the screen as blob-referenced raw-RGB tiles + manifest.
    delta=True returns only tiles changed since the previous frame."""
    a = {"tile_size": tile_size, "delta": delta}
    if region is not None:
        a["region"] = region
    if grid is not None:
        a["grid"] = grid
    return toolcall.call("screenshot_tiles", a)


def camera_snapshot(device: str | int | None = None,
                    max_width: int | None = None) -> dict:
    """Capture ONE still frame from a PC camera as PNG (base64).

    Placement is read-side (screenshot's sibling), but this is NOT a
    silent read: the call goes through toolcall with write=True so the
    ApprovalRequested audit event fires and plan mode cannot execute
    it, and the provider runs its own always_ask approval gate before
    opening the camera (privacy MF-1/MF-2). device: camera index or
    name; default first camera. One call = one frame."""
    a = {}
    if device is not None:
        a["device"] = device
    if max_width is not None:
        a["max_width"] = max_width
    return toolcall.call("camera_snapshot", a, write=True)

def list_windows() -> dict:
    """List visible windows: handle, pid, title."""
    return toolcall.call("list_windows", {})

def system_info() -> dict:
    """OS, host, user, CPU/memory."""
    return toolcall.call("system_info", {})

def list_dir(path: str = "") -> dict:
    """List a directory. Restricted to the user's profile."""
    return toolcall.call("list_dir", {"path": path})

def read_file(path: str) -> dict:
    """Read a UTF-8 text file (<=1MB). Restricted to the user's profile."""
    return toolcall.call("read_file", {"path": path})

def clipboard_get() -> dict:
    """Read text from the Windows clipboard (no approval)."""
    return toolcall.call("clipboard_get", {})

def search_files(pattern: str, path: str, file_pattern: str = "*",
                 max_results: int = 50, context: int = 0,
                 case_insensitive: bool = False,
                 output_mode: str = "matches",
                 offset: int = 0) -> dict:
    """Content search (regex) under a dir. Profile-restricted, no approval."""
    a = {"pattern": pattern, "path": path, "file_pattern": file_pattern,
         "max_results": max_results, "context": context,
         "case_insensitive": case_insensitive, "output_mode": output_mode,
         "offset": offset}
    return toolcall.call("search_files", a)

def search_filenames(pattern: str, path: str, max_results: int = 50,
                     sort_by: str = "name", offset: int = 0) -> dict:
    """Find files by name glob under a dir. Profile-restricted, no approval."""
    a = {"pattern": pattern, "path": path, "max_results": max_results,
         "sort_by": sort_by, "offset": offset}
    return toolcall.call("search_filenames", a)

def read_file_range(path: str, start_line: int, end_line: int = 0) -> dict:
    """Read 1-indexed line range (end_line=0 → start+50). Profile-restricted."""
    a = {"path": path, "start_line": start_line, "end_line": end_line}
    return toolcall.call("read_file_range", a)

def uia_find(window: str, name_rx: str = "", control_type: str = "",
             max_results: int = 50) -> dict:
    """Find UI elements in a window via UI Automation (no screenshot)."""
    return toolcall.call("uia_find",
                         {"window": window, "name_rx": name_rx,
                          "control_type": control_type,
                          "max_results": max_results})

def uia_get(window: str, name_rx: str = "", control_type: str = "",
            index: int = 0) -> dict:
    """Read one UI element via UI Automation: value/text, keyboard
    focus, RuntimeId, and available patterns (the read-back op)."""
    return toolcall.call("uia_get",
                         {"window": window, "name_rx": name_rx,
                          "control_type": control_type, "index": index})
