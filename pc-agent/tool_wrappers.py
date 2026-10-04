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


def clipboard_get() -> dict:
    """Read text from the Windows clipboard (no approval)."""
    return toolcall.call("clipboard_get", tools_read.clipboard_get, {})


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


def hotkey(keys: str) -> dict:
    """Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'."""
    return toolcall.call("hotkey", tools_write.hotkey,
                         {"keys": keys}, write=True)


def mouse_move(x: int, y: int) -> dict:
    """Move the cursor to screen coordinates. Requires on-PC approval."""
    return toolcall.call("mouse_move", tools_write.mouse_move,
                         {"x": x, "y": y}, write=True)


def mouse_click(x: int, y: int, button: str = "left") -> dict:
    """Click at screen coordinates. Requires on-PC approval."""
    return toolcall.call("mouse_click", tools_write.mouse_click,
                         {"x": x, "y": y, "button": button}, write=True)


def mouse_scroll(direction: str = "down", clicks: int = 3) -> dict:
    """Scroll the wheel under the cursor. Requires on-PC approval."""
    return toolcall.call("mouse_scroll", tools_write.mouse_scroll,
                         {"direction": direction, "clicks": clicks}, write=True)


def minimize_window(hwnd: int) -> dict:
    """Minimize a window. Requires on-PC approval."""
    return toolcall.call("minimize_window", tools_write.minimize_window,
                         {"hwnd": hwnd}, write=True)


def maximize_window(hwnd: int) -> dict:
    """Maximize a window. Requires on-PC approval."""
    return toolcall.call("maximize_window", tools_write.maximize_window,
                         {"hwnd": hwnd}, write=True)


def kill_process(pid: int) -> dict:
    """Terminate a process by PID. Requires on-PC approval."""
    return toolcall.call("kill_process", tools_write.kill_process,
                         {"pid": pid}, write=True)


def clipboard_set(text: str) -> dict:
    """Put text on the Windows clipboard. Requires on-PC approval."""
    return toolcall.call("clipboard_set", tools_write.clipboard_set,
                         {"text": text}, write=True)


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


def copy_file(src: str, dst: str) -> dict:
    """Copy a file (metadata preserved). Requires on-PC approval."""
    return toolcall.call("copy_file", tools_files.copy_file,
                         {"src": src, "dst": dst}, write=True)


def move_file(src: str, dst: str) -> dict:
    """Move/rename a file. Requires on-PC approval."""
    return toolcall.call("move_file", tools_files.move_file,
                         {"src": src, "dst": dst}, write=True)


def file_info(path: str) -> dict:
    """Size and timestamps for a file or directory (no approval)."""
    return toolcall.call("file_info", tools_files.file_info, {"path": path})


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
    clipboard_get,
    focus_window, close_window, type_text, shell_exec,
    hotkey, mouse_move, mouse_click, mouse_scroll,
    minimize_window, maximize_window, kill_process, clipboard_set,
    write_file, edit_file, delete_file, create_dir,
    copy_file, move_file, file_info,
    browser_snapshot, browser_navigate, browser_click, browser_fill,
    browser_eval,
]
