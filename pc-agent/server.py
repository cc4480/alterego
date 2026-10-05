#!/usr/bin/env python3
"""pc-mcp-bridge PC agent (Windows).

MCP server, Streamable HTTP, bound to 127.0.0.1:8765 only.

Authentication: interactive pairing. On startup the server prints a 6-digit
pairing code (single-use, 30-minute expiry, 5-attempt lockout). A background
thread keeps a fresh code printed: whenever the current one is consumed,
expires, or locks out, a new one is minted automatically — no restart
needed to re-pair. The operator
POSTs the code to /pair and receives a session bearer token over TLS, used
for all subsequent tool calls. The token is persisted to
%APPDATA%/pc-mcp-bridge/session_token so it survives server restarts, and
lasts until logout: POST /logout with the token, or delete the token file
(deleting it revokes access immediately, even while the server runs).
Re-pairing rotates the token. There is no long-term shared secret to
distribute, and nothing sensitive ever needs to travel through chat.

Run:  python pc-agent/server.py   (from the repo root)
"""
import os
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

import approval
import events
import hooks
from tool_wrappers import ALL_TOOLS

HOST, PORT = "127.0.0.1", 8765
mcp = MCPServer("pc-bridge")

for _tool_fn in ALL_TOOLS:
    mcp.tool()(_tool_fn)

PAIRING_TTL_S = 30 * 60
MAX_PAIRING_ATTEMPTS = 5

APPDATA_DIR = Path(os.environ.get("APPDATA", os.path.expanduser("~")))
SESSION_TOKEN_FILE = APPDATA_DIR / "pc-mcp-bridge" / "session_token"

_pairing_code: str = ""
_pairing_expires: float = 0.0
_pairing_consumed: bool = False
_pairing_failures: int = 0


def _new_pairing_code() -> str:
    """Cryptographically random 6-digit code, zero-padded."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _refresh_pairing_code() -> None:
    """Mint a fresh pairing code and print it for the operator."""
    global _pairing_code, _pairing_expires, _pairing_consumed, _pairing_failures
    _pairing_code = _new_pairing_code()
    _pairing_expires = time.time() + PAIRING_TTL_S
    _pairing_consumed = False
    _pairing_failures = 0
    print(f"PAIRING CODE: {_pairing_code}")
    print(f"(single-use, expires in {PAIRING_TTL_S // 60:.0f} minutes — "
          "the operator POSTs it to /pair to receive a session token)")


def _heartbeat_writer() -> None:
    """Background thread: write a timestamp every 60s so the watchdog
    can tell a live server from a hung one (a hung process may still
    hold port 8765). File: %APPDATA%/pc-mcp-bridge/heartbeat.txt"""
    hb_path = APPDATA_DIR / "pc-mcp-bridge" / "heartbeat.txt"
    while True:
        try:
            with open(hb_path, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass
        time.sleep(60)


def _pairing_refresher() -> None:
    """Background thread: always keep a usable pairing code printed.

    Whenever the current code is consumed, expires, or locks out, mint a
    new one so the operator never needs a server restart just to pair."""
    while True:
        time.sleep(30)
        if (_pairing_consumed or _pairing_failures >= MAX_PAIRING_ATTEMPTS
                or time.time() > _pairing_expires):
            print()
            print("--- previous pairing code consumed/expired: new code ---")
            _refresh_pairing_code()


def _load_session_token() -> str | None:
    """Read the persisted session token. The file is the source of truth:
    deleting it revokes the operator immediately, even while running."""
    try:
        token = SESSION_TOKEN_FILE.read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def _save_session_token(token: str) -> None:
    SESSION_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(SESSION_TOKEN_FILE, 0o600)
    except OSError:
        pass  # %APPDATA% is already user-private via Windows ACLs


def _clear_session_token() -> None:
    try:
        SESSION_TOKEN_FILE.unlink()
    except OSError:
        pass


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        if request.url.path in ("/health", "/pair"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = _load_session_token()
        if not token or not secrets.compare_digest(auth, f"Bearer {token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def _pair(request: Request):
    """POST /pair {"code": "482913"} -> {"token": "<session bearer token>"}.

    The code is single-use, expires after PAIRING_TTL_S, and the endpoint
    locks after MAX_PAIRING_ATTEMPTS wrong guesses; the refresher thread
    mints a fresh code automatically afterwards.
    The issued token is persisted to disk (survives restarts) and any
    previous token is rotated out. Lasts until POST /logout or the token
    file is deleted.
    """
    global _pairing_consumed, _pairing_failures
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
        remaining = MAX_PAIRING_ATTEMPTS - _pairing_failures
        return JSONResponse(
            {"error": "wrong code", "attempts_remaining": remaining},
            status_code=401,
        )

    _pairing_consumed = True
    token = secrets.token_hex(32)
    _save_session_token(token)
    sid, data = events.new_session(token)
    events.append("SessionPaired", data, session_id=sid)
    return JSONResponse({"token": token})


async def _logout(request: Request):
    """POST /logout (bearer token required) -> revokes the session now."""
    token = _load_session_token()
    _clear_session_token()
    sid = events.new_session(token)[0] if token else None
    events.append("SessionRevoked", {"reason": "logout"}, session_id=sid)
    return JSONResponse({"ok": True})


async def _health(request):
    return JSONResponse({"ok": True, "service": "pc-bridge"})


def _server_version():
    """Git sha when available, else PC_BRIDGE_VERSION env, else unknown."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)))
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return os.environ.get("PC_BRIDGE_VERSION", "unknown")


def _emit_server_started():
    """ServerStarted lifecycle event (session_id=null: infrastructure).
    Best-effort: never raises."""
    started = time.time()
    try:
        import platform
        hook_lists = hooks.list_hooks()
        events.append("ServerStarted", {
            "version": _server_version(),
            "permission_mode": approval.get_permission_mode(),
            "python": platform.python_version(),
            "hook_count": len(hook_lists.get(hooks.PRE_TOOL_USE, []))
            + len(hook_lists.get(hooks.POST_TOOL_USE, [])),
            "event_log_version": events.EVENT_LOG_VERSION,
        }, session_id=None)
    except Exception as e:  # noqa: BLE001 - startup must never fail on this
        print(f"[events] ServerStarted emit failed: {e}")
    return started


def main():
    _refresh_pairing_code()
    threading.Thread(target=_pairing_refresher, daemon=True).start()
    threading.Thread(target=_heartbeat_writer, daemon=True).start()
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
    app.routes.append(Route("/logout", _logout, methods=["POST"]))
    app.add_middleware(BearerAuth)

    print(f"pc-mcp-bridge listening on http://{HOST}:{PORT} (loopback only)")
    print("tunnel:  cloudflared tunnel --url http://127.0.0.1:8765")
    print()
    mode = approval.get_permission_mode()
    if mode == "dontask":
        print("!" * 68)
        print("!!!  DON'T-ASK MODE — ALL DIALOGS OFF                          !!!")
        print("!!!  Every tool executes WITHOUT asking, including destructive  !!!")
        print("!!!  actions. The event log still records everything.        !!!")
        print("!" * 68)
        print()
    elif mode == "plan":
        print("!" * 68)
        print("!!!  PLAN MODE — every write runs as dry-run, no side effects  !!!")
        print("!" * 68)
        print()
    elif mode == "acceptedits":
        print("!" * 68)
        print("!!!  ACCEPT-EDITS MODE — file writes auto-approved; shell and  !!!")
        print("!!!  destructive tools still show the approval dialog.          !!!")
        print("!" * 68)
        print()
    elif approval.is_auto_approve():
        print("!" * 68)
        print("!!!  AUTO-APPROVE MODE (PC_BRIDGE_AUTO_APPROVE=1) — DIALOGS OFF  !!!")
        print("!!!  Every write tool executes WITHOUT asking. For the PC     !!!")
        print("!!!  owner's own testing only. Restart without the env var     !!!")
        print("!!!  to restore approval dialogs. Auth still required.         !!!")
        print("!" * 68)
        print()
    if _load_session_token():
        print("session: existing operator session restored (persists until logout)")
    else:
        print("session: no operator paired yet")
    print("logout: POST /logout with the bearer token, or delete")
    print("        %APPDATA%/pc-mcp-bridge/session_token (revokes instantly)")
    print("events: %APPDATA%/pc-mcp-bridge/events/  (primary operation log)")
    started = _emit_server_started()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    finally:  # ServerStopping on clean shutdown (events.append never raises)
        events.append("ServerStopping", {
            "reason": "shutdown",
            "uptime_s": int(time.time() - started)}, session_id=None)


if __name__ == "__main__":
    main()
