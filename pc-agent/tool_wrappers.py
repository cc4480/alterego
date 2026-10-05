"""MCP tool wrappers: public signatures + docstrings for the SDK schema.
Each wrapper delegates to toolcall.call for audit logging. server.py
registers ALL_TOOLS with mcp.tool().

Consumers name the tool only — toolcall.call resolves the implementation
from the active provider (seams.registry). No wrapper imports a provider.
"""
import toolcall
import arbitrate as _arb
import tools_tasks  # shim -> seams.providers.windows.tasks (MCP wrappers)

# ---- read tools -----------------------------------------------------------
def screenshot() -> dict:
    """Capture the primary monitor as PNG (base64)."""
    return toolcall.call("screenshot", {})

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


def shell_exec(command: str, timeout_s: int = 60) -> dict:
    """Run a command in cmd.exe. Requires on-PC approval."""
    return toolcall.call("shell_exec",
                         {"command": command, "timeout_s": timeout_s},
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
def write_file(path: str, content: str, dry_run: bool = False) -> dict:
    """Create/overwrite a UTF-8 text file. Approval unless dry_run."""
    a = {"path": path, "content": content, "dry_run": dry_run}
    return toolcall.call("write_file", a, write=not dry_run)


def edit_file(path: str, old_text: str, new_text: str, dry_run: bool = False) -> dict:
    """Replace first old_text with new_text. Approval unless dry_run."""
    a = {"path": path, "old_text": old_text, "new_text": new_text, "dry_run": dry_run}
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


def shell_pwsh(script: str, timeout_s: int = 60) -> dict:
    """Run a PowerShell script directly (no cmd.exe wrapping). Requires approval."""
    return toolcall.call("shell_pwsh",
                         {"script": script, "timeout_s": timeout_s}, write=True)


def batch(calls: list) -> dict:
    """Run up to 20 tool calls in one roundtrip. One approval covers all writes."""
    return toolcall.call("batch", {"calls": calls}, write=True)


def http_headers(url: str, timeout_s: int = 20) -> dict:
    """GET a URL, return status + response headers. No approval (read-only)."""
    return toolcall.call("http_headers", {"url": url, "timeout_s": timeout_s})


def dns_query(domain: str, rtype: str = "A") -> dict:
    """Resolve DNS records (A AAAA CNAME MX NS TXT DNSKEY SOA). No approval."""
    return toolcall.call("dns_query", {"domain": domain, "rtype": rtype})


def tls_info(host: str, port: int = 443, timeout_s: int = 15) -> dict:
    """TLS handshake: version, cipher, cert subject/issuer/expiry. No approval."""
    return toolcall.call("tls_info",
                         {"host": host, "port": port, "timeout_s": timeout_s})


def tcp_check(host: str, ports: list, timeout_s: int = 3) -> dict:
    """TCP connect check across ports (open/closed/filtered). No approval."""
    return toolcall.call("tcp_check",
                         {"host": host, "ports": ports,
                          "timeout_s": timeout_s})


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


ALL_TOOLS = [
    screenshot, list_windows, system_info, list_dir, read_file,
    clipboard_get, memory_recall,
    focus_window, close_window, type_text, shell_exec,
    hotkey, mouse_move, mouse_click, mouse_scroll,
    minimize_window, maximize_window, kill_process, clipboard_set,
    paste_text,
    write_file, edit_file, delete_file, create_dir,
    copy_file, move_file, file_info,
    browser_snapshot, browser_navigate, browser_click, browser_fill,
    browser_eval,
    shell_pwsh, batch,
    http_headers, dns_query, tls_info, tcp_check,
    active_window, idle_seconds, list_processes,
    notify, speak, set_volume, power, tools_tasks.task_create,
    tools_tasks.task_checkpoint, tools_tasks.task_status, _arb.arbitrate_tool,
    doctor,
]
