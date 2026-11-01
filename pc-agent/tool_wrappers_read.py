"""Read-only tool wrappers.

Split from tool_wrappers.py to keep files under 300 lines.
Each wrapper delegates to toolcall.call for audit logging.
"""
import toolcall


def screenshot(region: dict | None = None, scale: float = 1.0) -> dict:
    """Capture the primary monitor as PNG (base64). Optional region and scale."""
    a = {}
    if region is not None:
        a["region"] = region
    if scale != 1.0:
        a["scale"] = scale
    return toolcall.call("screenshot", a)

def list_windows(offset: int = 0, limit: int = 0) -> dict:
    """List visible windows: handle, pid, title. Paginated."""
    return toolcall.call("list_windows", {"offset": offset, "limit": limit})

def system_info() -> dict:
    """OS, host, user, CPU/memory."""
    return toolcall.call("system_info", {})

def list_dir(path: str = "", offset: int = 0, limit: int = 0) -> dict:
    """List a directory. Restricted to the user's profile. Paginated."""
    return toolcall.call("list_dir",
                         {"path": path, "offset": offset, "limit": limit})

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


def desktop_state(include_screenshot: bool = True, scale: float = 0.5,
                  max_elements: int = 25) -> dict:
    """One call: windows, focused window, its UI elements, screenshot."""
    return toolcall.call("desktop_state",
                         {"include_screenshot": include_screenshot,
                          "scale": scale, "max_elements": max_elements})


def wait_for(kind: str, target: str, window: str = "",
             control_type: str = "", timeout_s: float = 10.0,
             interval_s: float = 0.5) -> dict:
    """Poll until target appears; found=false on timeout, never an error."""
    return toolcall.call("wait_for",
                         {"kind": kind, "target": target, "window": window,
                          "control_type": control_type,
                          "timeout_s": timeout_s,
                          "interval_s": interval_s})
