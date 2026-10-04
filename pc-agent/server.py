#!/usr/bin/env python3
"""pc-mcp-bridge PC agent (Windows).

MCP server, Streamable HTTP, bound to 127.0.0.1:8765 only.
Bearer-token auth via middleware; /health is the only unauthenticated route.
Run:  python pc-agent/server.py   (from the repo root)
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from auth import load_or_create_token
from audit import log_event
import tools_read
import tools_write

HOST, PORT = "127.0.0.1", 8765
mcp = MCPServer("pc-bridge")


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {self._token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _call(name, fn, args, write=False):
    """Run a tool with audit logging; errors become {error} payloads."""
    try:
        result = fn(**args)
        log_event(name, args, "ok", approved=True if write else None)
        return result
    except PermissionError as e:
        log_event(name, args, f"denied: {e}", approved=False)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface as tool error, never crash
        log_event(name, args, f"error: {type(e).__name__}: {e}")
        return {"error": f"{type(e).__name__}: {e}"}


# ---- read tools -----------------------------------------------------------
@mcp.tool()
def screenshot() -> dict:
    """Capture the primary monitor as PNG (base64)."""
    return _call("screenshot", tools_read.screenshot, {})


@mcp.tool()
def list_windows() -> dict:
    """List visible windows: handle, pid, title."""
    return _call("list_windows", tools_read.list_windows, {})


@mcp.tool()
def system_info() -> dict:
    """OS, host, user, CPU/memory."""
    return _call("system_info", tools_read.system_info, {})


@mcp.tool()
def list_dir(path: str = "") -> dict:
    """List a directory. Restricted to the user's profile."""
    return _call("list_dir", tools_read.list_dir, {"path": path})


@mcp.tool()
def read_file(path: str) -> dict:
    """Read a UTF-8 text file (<=1MB). Restricted to the user's profile."""
    return _call("read_file", tools_read.read_file, {"path": path})


# ---- write tools (each pops a native approval dialog on the PC) -----------
@mcp.tool()
def focus_window(hwnd: int) -> dict:
    """Bring a window to the foreground. Requires on-PC approval."""
    return _call("focus_window", tools_write.focus_window, {"hwnd": hwnd}, write=True)


@mcp.tool()
def type_text(text: str) -> dict:
    """Type text into the focused window. Requires on-PC approval."""
    return _call("type_text", tools_write.type_text, {"text": text}, write=True)


@mcp.tool()
def shell_exec(command: str, timeout_s: int = 60) -> dict:
    """Run a command in cmd.exe. Requires on-PC approval."""
    return _call(
        "shell_exec", tools_write.shell_exec,
        {"command": command, "timeout_s": timeout_s}, write=True,
    )


async def _health(request):
    return JSONResponse({"ok": True, "service": "pc-bridge"})


def main():
    token, created = load_or_create_token()
    app = mcp.streamable_http_app()
    app.routes.append(Route("/health", _health))
    app.add_middleware(BearerAuth, token=token)

    print(f"pc-mcp-bridge listening on http://{HOST}:{PORT} (loopback only)")
    print("tunnel:  cloudflared tunnel --url http://127.0.0.1:8765")
    if created:
        print("NEW TOKEN (store in the operator's Secure Vault, never chat):")
        print(token)
    else:
        print("token: loaded from %APPDATA%/pc-mcp-bridge/token")
    print("audit: %APPDATA%/pc-mcp-bridge/audit.log")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
