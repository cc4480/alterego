"""MCP tool wrappers: public signatures + docstrings for the SDK schema.

Each wrapper delegates to toolcall.call for audit logging. server.py
registers ALL_TOOLS with mcp.tool(); adding a tool means adding a wrapper
here and (if new logic) a function in the matching tools_* module.
"""
import toolcall
import tools_read
import tools_write
import tools_browser
import tools_files


# ---- read tools -----------------------------------------------------------
def screenshot() -> dict:
    """Capture the primary monitor as PNG (base64)."""
    return toolcall.call("screenshot", tools_read.screenshot, {})


def list_windows() -> dict:
    """List visible windows: handle, pid, title."""
    return toolcall.call("list_windows", tools_read.list_windows, {})


def system_info() -> dict:
    """OS, host, user, CPU/memory."""
    return toolcall.call("system_info", tools_read.system_info, {})


def list_dir(path: str = "") -> dict:
    """List a directory. Restricted to the user's profile."""
    return toolcall.call("list_dir", tools_read.list_dir, {"path": path})


def read_file(path: str) -> dict:
    """Read a UTF-8 text file (<=1MB). Restricted to the user's profile."""
    return toolcall.call("read_file", tools_read.read_file, {"path": path})


# ---- write tools (each pops a native approval dialog on the PC) -----------
def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground. Requires on-PC approval."""
    return toolcall.call("focus_window", tools_write.focus_window,
                         {"hwnd": hwnd}, write=True)


def close_window(hwnd: int) -> dict:
    """Gracefully close a window (like clicking X). Requires on-PC approval."""
    return toolcall.call("close_window", tools_write.close_window,
                         {"hwnd": hwnd}, write=True)


def type_text(text: str) -> dict:
    """Type text into the focused window. Requires on-PC approval."""
    return toolcall.call("type_text", tools_write.type_text,
                         {"text": text}, write=True)


def shell_exec(command: str, timeout_s: int = 60) -> dict:
    """Run a command in cmd.exe. Requires on-PC approval."""
    return toolcall.call("shell_exec", tools_write.shell_exec,
                         {"command": command, "timeout_s": timeout_s},
                         write=True)


# ---- file tools (absolute paths anywhere; each needs approval) ------------
def write_file(path: str, content: str) -> dict:
    """Create/overwrite a UTF-8 text file (parents created). Requires approval."""
    return toolcall.call("write_file", tools_files.write_file,
                         {"path": path, "content": content}, write=True)


def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Replace first occurrence of old_text with new_text. Requires approval."""
    return toolcall.call("edit_file", tools_files.edit_file,
                         {"path": path, "old_text": old_text,
                          "new_text": new_text}, write=True)


def delete_file(path: str) -> dict:
    """Permanently delete a file. Requires on-PC approval."""
    return toolcall.call("delete_file", tools_files.delete_file,
                         {"path": path}, write=True)


def create_dir(path: str) -> dict:
    """Create a directory (parents too). Requires on-PC approval."""
    return toolcall.call("create_dir", tools_files.create_dir,
                         {"path": path}, write=True)


# ---- browser tools (CDP; Edge needs --remote-debugging-port=9222) ----------
def browser_snapshot(url_contains: str = "") -> dict:
    """List interactive elements of the live Edge tab (no approval)."""
    return toolcall.call("browser_snapshot", tools_browser.browser_snapshot,
                         {"url_contains": url_contains})


def browser_navigate(url: str, url_contains: str = "") -> dict:
    """Navigate the Edge tab to a URL. Requires on-PC approval."""
    return toolcall.call("browser_navigate", tools_browser.browser_navigate,
                         {"url": url, "url_contains": url_contains},
                         write=True)


def browser_click(text: str, url_contains: str = "") -> dict:
    """Click the element containing text in the Edge tab. Requires approval."""
    return toolcall.call("browser_click", tools_browser.browser_click,
                         {"text": text, "url_contains": url_contains},
                         write=True)


def browser_fill(label: str, text: str, url_contains: str = "") -> dict:
    """Fill the field matching label in the Edge tab. Requires approval."""
    return toolcall.call("browser_fill", tools_browser.browser_fill,
                         {"label": label, "text": text,
                          "url_contains": url_contains}, write=True)


def browser_eval(js: str, url_contains: str = "") -> dict:
    """Run JavaScript in the Edge tab. Requires on-PC approval."""
    return toolcall.call("browser_eval", tools_browser.browser_eval,
                         {"js": js, "url_contains": url_contains}, write=True)


ALL_TOOLS = [
    screenshot, list_windows, system_info, list_dir, read_file,
    focus_window, close_window, type_text, shell_exec,
    write_file, edit_file, delete_file, create_dir,
    browser_snapshot, browser_navigate, browser_click, browser_fill,
    browser_eval,
]
