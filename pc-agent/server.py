#!/usr/bin/env python3
"""pc-mcp-bridge PC agent (Windows).

MCP server, Streamable HTTP, bound to 127.0.0.1:8765 only.

Authentication: interactive pairing. On startup the server prints a 6-digit
pairing code (single-use, 5-minute expiry, 5-attempt lockout). The operator
POSTs the code to /pair and receives a session bearer token over TLS, used
for all subsequent tool calls. There is no long-term shared secret to
distribute, and nothing sensitive ever needs to travel through chat.

Run:  python pc-agent/server.py   (from the repo root)
"""
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from audit import log_event
import tools_read
import tools_write

HOST, PORT = "127.0.0.1", 8765
mcp = MCPServer("pc-bridge")

PAIRING_TTL_S = 5 * 60
MAX_PAIRING_ATTEMPTS = 5

_pairing_code: str = ""
_pairing_expires: float = 0.0
_pairing_consumed: bool = False
_pairing_failures: int = 0
_session_token: str | None = None


def _new_pairing_code() -> str:
    """Cryptographically random 6-digit code, zero-padded."""
    return f"{secrets.randbelow(1_000_000):06d}"


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        if request.url.path in ("/health", "/pair"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = _session_token
        if not token or not secrets.compare_digest(auth, f"Bearer {token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def _pair(request: Request):
    """POST /pair {"code": "482913"} -> {"token": "<session bearer token>"}.

    The code is single-use, expires after PAIRING_TTL_S, and the endpoint
    locks after MAX_PAIRING_ATTEMPTS wrong guesses (until server restart).
    """
    global _pairing_consumed, _pairing_failures, _session_token
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    code = str(body.get("code", "")).strip()

    if _pairing_consumed or _pairing_failures >= MAX_PAIRING_ATTEMPTS:
        return JSONResponse({"error": "pairing unavailable"}, status_code=403)
    if time.time() > _pairing_expires:
        return JSONResponse({"error": "pairing code expired"}, status_code=403)
    if not secrets.compare_digest(code, _pairing_code):
        _pairing_failures += 1
        log_event("pair", {}, "denied: wrong code")
        remaining = MAX_PAIRING_ATTEMPTS - _pairing_failures
        return JSONResponse(
            {"error": "wrong code", "attempts_remaining": remaining},
            status_code=401,
        )

    _pairing_consumed = True
    _session_token = secrets.token_hex(32)
    log_event("pair", {}, "ok: session paired")
    return JSONResponse({"token": _session_token})


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
    global _pairing_code, _pairing_expires
    _pairing_code = _new_pairing_code()
    _pairing_expires = time.time() + PAIRING_TTL_S
    # The SDK auto-enables DNS-rebinding protection for localhost servers,
    # which 421s any Host header that isn't localhost — including our
    # Cloudflare tunnel hostname (random per session, can't be allowlisted).
    # Disabled here: the server binds 127.0.0.1-only, every route (except
    # /health and /pair) requires the session bearer token, and the tunnel
    # is user-initiated. The bearer token, not the Host header, is the
    # real authentication.
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    )
    app.routes.append(Route("/health", _health))
    app.routes.append(Route("/pair", _pair, methods=["POST"]))
    app.add_middleware(BearerAuth)

    print(f"pc-mcp-bridge listening on http://{HOST}:{PORT} (loopback only)")
    print("tunnel:  cloudflared tunnel --url http://127.0.0.1:8765")
    print()
    print(f"PAIRING CODE: {_pairing_code}")
    print(f"(single-use, expires in {PAIRING_TTL_S // 60:.0f} minutes — "
          "the operator POSTs it to /pair to receive a session token)")
    print("audit: %APPDATA%/pc-mcp-bridge/audit.log")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
