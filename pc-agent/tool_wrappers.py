"""MCP tool wrappers: public signatures + docstrings for the SDK schema.
Each wrapper delegates to toolcall.call for audit logging. server.py
registers ALL_TOOLS with mcp.tool().

Consumers name the tool only — toolcall.call resolves the implementation
from the active provider (seams.registry). No wrapper imports a provider.
"""
import toolcall
import arbitrate as _arb
import tools_tasks  # shim -> seams.providers.windows.tasks (MCP wrappers)

# ---- read tools (in tool_wrappers_read.py, file size limit) ---------------
from tool_wrappers_read import (  # noqa: E402
    screenshot, list_windows, system_info, list_dir, read_file,
    clipboard_get, search_files, search_filenames, read_file_range,
    uia_find)

# ---- write tools (each pops a native approval dialog on the PC) -----------
def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground. Requires on-PC approval."""
    return toolcall.call("focus_window", {"hwnd": hwnd}, write=True)

def close_window(hwnd: int) -> dict:
    """Gracefully close a window (like clicking X). Requires on-PC approval."""
    return toolcall.call("close_window", {"hwnd": hwnd}, write=True)


def type_text(text: str) -> dict:
    """Type text into the focused window. Requires on-PC approval."""
    return toolcall.call("type_text", {"text": text}, write=True)


def shell_exec(command: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Run a command in cmd.exe. Optional cwd must be inside the user
    profile. Requires on-PC approval."""
    return toolcall.call("shell_exec",
                         {"command": command, "timeout_s": timeout_s,
                          "cwd": cwd},
                         write=True)


def hotkey(keys: str) -> dict:
    """Press a key combo: 'enter', 'esc', 'tab', 'ctrl+c', 'alt+f4', 'win+r'."""
    return toolcall.call("hotkey", {"keys": keys}, write=True)


def mouse_move(x: int, y: int) -> dict:
    """Move the cursor to screen coordinates. Requires on-PC approval."""
    return toolcall.call("mouse_move", {"x": x, "y": y}, write=True)


def mouse_click(x: int, y: int, button: str = "left") -> dict:
    """Click at screen coordinates. Requires on-PC approval."""
    return toolcall.call("mouse_click",
                         {"x": x, "y": y, "button": button}, write=True)


def uia_click(window: str, name_rx: str, control_type: str = "",
              index: int = None, button: str = "left") -> dict:
    """Click a UI element by name via UI Automation. Requires on-PC approval.

    index omitted = the element must resolve uniquely (tiered matching:
    exact name, then anchored regex, then partial); ambiguity is an
    error listing the candidates, never a silent first pick."""
    return toolcall.call("uia_click",
                         {"window": window, "name_rx": name_rx,
                          "control_type": control_type, "index": index,
                          "button": button}, write=True)


def uia_set_text(window: str, name_rx: str, text: str,
                 control_type: str = "", index: int = None) -> dict:
    """Set text of a UI element via UI Automation. Requires on-PC approval.

    Same resolution discipline as uia_click; success requires the field
    to read back with the requested text."""
    return toolcall.call("uia_set_text",
                         {"window": window, "name_rx": name_rx, "text": text,
                          "control_type": control_type, "index": index},
                         write=True)


def mouse_scroll(direction: str = "down", clicks: int = 3) -> dict:
    """Scroll the wheel under the cursor. Requires on-PC approval."""
    return toolcall.call("mouse_scroll",
                         {"direction": direction, "clicks": clicks},
                         write=True)


def minimize_window(hwnd: int) -> dict:
    """Minimize a window. Requires on-PC approval."""
    return toolcall.call("minimize_window", {"hwnd": hwnd}, write=True)


def maximize_window(hwnd: int) -> dict:
    """Maximize a window. Requires on-PC approval."""
    return toolcall.call("maximize_window", {"hwnd": hwnd}, write=True)


def kill_process(pid: int) -> dict:
    """Terminate a process by PID. Requires on-PC approval."""
    return toolcall.call("kill_process", {"pid": pid}, write=True)


def clipboard_set(text: str) -> dict:
    """Put text on the Windows clipboard. Requires on-PC approval."""
    return toolcall.call("clipboard_set", {"text": text}, write=True)


def paste_text(text: str) -> dict:
    """Paste text into the focused window (reliable for long text)."""
    return toolcall.call("paste_text", {"text": text}, write=True)


# ---- file tools (absolute paths anywhere; each needs approval) ------------
def write_file(path: str, content: str, dry_run: bool = False,
               expected_sha256: str | None = None) -> dict:
    """Create/overwrite a UTF-8 text file. Approval unless dry_run.
    expected_sha256 fails the write if the file changed since read."""
    a = {"path": path, "content": content, "dry_run": dry_run,
         "expected_sha256": expected_sha256}
    return toolcall.call("write_file", a, write=not dry_run)


def edit_file(path: str, old_text: str, new_text: str, dry_run: bool = False,
              require_unique: bool = True, read_before: bool = True,
              expected_sha256: str | None = None) -> dict:
    """Replace old_text with new_text. Fails on multiple matches unless
    require_unique=False. Always returns a diff preview.
    expected_sha256 fails the edit if the file changed since read.
    Approval unless dry_run."""
    a = {"path": path, "old_text": old_text, "new_text": new_text,
         "dry_run": dry_run, "require_unique": require_unique,
         "read_before": read_before, "expected_sha256": expected_sha256}
    return toolcall.call("edit_file", a, write=not dry_run)


def delete_file(path: str, dry_run: bool = False) -> dict:
    """Permanently delete a file. Approval unless dry_run."""
    a = {"path": path, "dry_run": dry_run}
    return toolcall.call("delete_file", a, write=not dry_run)


def create_dir(path: str, dry_run: bool = False) -> dict:
    """Create a directory (parents too). Approval unless dry_run."""
    a = {"path": path, "dry_run": dry_run}
    return toolcall.call("create_dir", a, write=not dry_run)


def copy_file(src: str, dst: str, dry_run: bool = False) -> dict:
    """Copy a file (metadata preserved). Approval unless dry_run."""
    a = {"src": src, "dst": dst, "dry_run": dry_run}
    return toolcall.call("copy_file", a, write=not dry_run)


def move_file(src: str, dst: str, dry_run: bool = False) -> dict:
    """Move/rename a file. Approval unless dry_run."""
    a = {"src": src, "dst": dst, "dry_run": dry_run}
    return toolcall.call("move_file", a, write=not dry_run)


def file_info(path: str) -> dict:
    """Size and timestamps for a file or directory (no approval)."""
    return toolcall.call("file_info", {"path": path})


def memory_recall(query: str, limit: int = 5) -> dict:
    """Search the on-PC memory archive (transcripts + subjects). No approval."""
    return toolcall.call("memory_recall", {"query": query, "limit": limit})


def shell_pwsh(script: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Run a PowerShell script directly (no cmd.exe wrapping). Optional cwd
    must be inside the user profile. Requires approval."""
    return toolcall.call("shell_pwsh",
                         {"script": script, "timeout_s": timeout_s,
                          "cwd": cwd}, write=True)


def batch(calls: list) -> dict:
    """Run up to 20 tool calls in one roundtrip. One approval covers all writes."""
    return toolcall.call("batch", {"calls": calls}, write=True)


# Background tools live in tool_wrappers_background (file size limit).
from tool_wrappers_background import (  # noqa: E402
    exec_background, exec_status, exec_cancel)


# Recon tools live in tool_wrappers_recon (file size limit).
from tool_wrappers_recon import (  # noqa: E402
    http_headers, dns_query, tls_info, tcp_check, web_fetch, web_search)


def active_window() -> dict:
    """Foreground window: hwnd, title, pid, process. No approval."""
    return toolcall.call("active_window", {})


def idle_seconds() -> dict:
    """Seconds since last keyboard/mouse input. No approval."""
    return toolcall.call("idle_seconds", {})


def list_processes() -> dict:
    """Running processes (pid + exe). No approval."""
    return toolcall.call("list_processes", {})


def notify(title: str, message: str, timeout_s: int = 5) -> dict:
    """Windows tray balloon notification. Requires approval."""
    return toolcall.call("notify",
                         {"title": title, "message": message,
                          "timeout_s": timeout_s}, write=True)


def speak(text: str) -> dict:
    """Speak text aloud through the PC speakers. Requires approval."""
    return toolcall.call("speak", {"text": text}, write=True)


def set_volume(level: int) -> dict:
    """Set master volume 0-100. Requires approval."""
    return toolcall.call("set_volume", {"level": level}, write=True)


def power(action: str) -> dict:
    """lock | sleep | restart | shutdown. Requires approval."""
    return toolcall.call("power", {"action": action}, write=True)


def doctor() -> dict:
    """Bridge health check: Python, port, Defender, Startup, tunnel, disk.
    Each check returns ok/warning/fail + a specific fix command. No approval."""
    return toolcall.call("doctor", {})


def query_events(tool: str = None, type: str = None, since: str = "7d",
                 session_id: str = None, limit: int = 50) -> dict:
    """Search the event-sourced operation log: filter by tool name, event
    type, session, and time window (5m/1h/24h/7d/30d or ISO). Read-only,
    no approval."""
    return toolcall.call("query_events",
                         {"tool": tool, "type": type, "since": since,
                          "session_id": session_id, "limit": limit})


def replay_session(session_id: str, format: str = "human") -> dict:
    """Full causal transcript of one operator session — verify reported
    operations against the log. Read-only, no approval."""
    return toolcall.call("replay_session",
                         {"session_id": session_id, "format": format})


# ---- browser tools (CDP; Edge needs --remote-debugging-port=9222) ----------
def browser_snapshot(url_contains: str = "") -> dict:
    """List interactive elements of the live Edge tab (no approval)."""
    return toolcall.call("browser_snapshot", {"url_contains": url_contains})


def browser_navigate(url: str, url_contains: str = "") -> dict:
    """Navigate the Edge tab to a URL. Requires on-PC approval."""
    return toolcall.call("browser_navigate",
                         {"url": url, "url_contains": url_contains},
                         write=True)


def browser_click(text: str, url_contains: str = "") -> dict:
    """Click the element containing text in the Edge tab. Requires approval."""
    return toolcall.call("browser_click",
                         {"text": text, "url_contains": url_contains},
                         write=True)


def browser_fill(label: str, text: str, url_contains: str = "") -> dict:
    """Fill the field matching label in the Edge tab. Requires approval."""
    return toolcall.call("browser_fill",
                         {"label": label, "text": text,
                          "url_contains": url_contains}, write=True)


def browser_eval(js: str, url_contains: str = "") -> dict:
    """Run JavaScript in the Edge tab. Requires on-PC approval."""
    return toolcall.call("browser_eval",
                         {"js": js, "url_contains": url_contains}, write=True)


# ---- Chrome CDP session hand (cdp_*; agent-owned presentation profile) ----
def cdp_launch(profile_dir: str = "", debug_port: int = 0,
               start_url: str = "") -> dict:
    """Launch Chrome on the prepared presentation profile with a CDP
    debug port and attach the session. Requires approval."""
    return toolcall.call("cdp_launch",
                         {"profile_dir": profile_dir,
                          "debug_port": debug_port, "start_url": start_url},
                         write=True)


def cdp_connect(debug_port: int = 0) -> dict:
    """Attach the CDP session to a Chrome already serving a debug port
    (no process ownership). Requires approval."""
    return toolcall.call("cdp_connect", {"debug_port": debug_port},
                         write=True)


def cdp_targets(target_id: str = "") -> dict:
    """List the session browser's page targets; switches the active
    target when target_id is given (no approval)."""
    return toolcall.call("cdp_targets", {"target_id": target_id})


def cdp_navigate(url: str, timeout_s: int = 30) -> dict:
    """Navigate the CDP session tab to a URL and verify the landing
    (url + title readback; hard timeout, never a silent hang).
    Requires approval."""
    return toolcall.call("cdp_navigate",
                         {"url": url, "timeout_s": timeout_s}, write=True)


def cdp_snapshot() -> dict:
    """Compact index of the page's interactive elements with stable CSS
    selectors — the addressing/verification primitive (no approval)."""
    return toolcall.call("cdp_snapshot", {})


def cdp_click(selector: str = "", text: str = "") -> dict:
    """Click an element by CSS selector or visible text (never
    coordinates); returns the landing readback. Requires approval."""
    return toolcall.call("cdp_click",
                         {"selector": selector, "text": text}, write=True)


def cdp_type(text: str, selector: str = "", label: str = "",
             clear_first: bool = True) -> dict:
    """Type into a field addressed by selector or label via CDP
    Input.insertText; success requires value readback equality.
    Requires approval."""
    return toolcall.call("cdp_type",
                         {"text": text, "selector": selector,
                          "label": label, "clear_first": clear_first},
                         write=True)


def cdp_evaluate(js: str) -> dict:
    """Run JavaScript in the CDP session tab. Requires on-PC approval."""
    return toolcall.call("cdp_evaluate", {"js": js}, write=True)


def cdp_readback() -> dict:
    """Read back the session page's url, title, ready_state and active
    element — verify a landing without acting (no approval)."""
    return toolcall.call("cdp_readback", {})


def cdp_close(delete_profile: bool = False) -> dict:
    """End the CDP session: a launched browser is closed gracefully via
    Browser.close; an attached browser is only detached. Optionally
    deletes the presentation profile. Requires approval."""
    return toolcall.call("cdp_close", {"delete_profile": delete_profile},
                         write=True)


def server_reload() -> dict:
    """Restart the bridge server via the supervisor (fresh code, same tunnel and tokens). Refuses when no supervisor is running. Requires approval."""
    return toolcall.call("server_reload", {}, write=True)


def supervisor_status() -> dict:
    """Supervisor health: running, child pid, restarts, heartbeat age. No approval."""
    return toolcall.call("supervisor_status", {})


def hot_reload(action: str) -> dict:
    """Hot reload: status | enable | disable | trigger. Enable/disable/trigger require approval."""
    return toolcall.call("hot_reload", {"action": action}, write=True)

ALL_TOOLS = [
    screenshot, list_windows, system_info, list_dir, read_file,
    clipboard_get, memory_recall,
    search_files, search_filenames, read_file_range,
    focus_window, close_window, type_text, shell_exec,
    hotkey, mouse_move, mouse_click, mouse_scroll,
    uia_find, uia_click, uia_set_text,
    minimize_window, maximize_window, kill_process, clipboard_set,
    paste_text,
    write_file, edit_file, delete_file, create_dir,
    copy_file, move_file, file_info,
    browser_snapshot, browser_navigate, browser_click, browser_fill,
    browser_eval,
    cdp_launch, cdp_connect, cdp_targets, cdp_navigate, cdp_snapshot,
    cdp_click, cdp_type, cdp_evaluate, cdp_readback, cdp_close,
    shell_pwsh, batch,
    exec_background, exec_status, exec_cancel,
    http_headers, dns_query, tls_info, tcp_check, web_fetch, web_search,
    active_window, idle_seconds, list_processes,
    notify, speak, set_volume, power, tools_tasks.task_create,
    tools_tasks.task_checkpoint, tools_tasks.task_status, _arb.arbitrate_tool,
    doctor, query_events, replay_session,
    server_reload, supervisor_status, hot_reload,
]
