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
