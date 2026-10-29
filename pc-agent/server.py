#!/usr/bin/env python3
"""pc-mcp-bridge PC agent (Windows).
MCP server, Streamable HTTP, bound to 127.0.0.1:8765 only.

Authentication: interactive pairing. On startup the server prints a 6-digit
pairing code (single-use, 30-minute expiry, 5-attempt lockout). A background
thread refreshes the code when consumed or expired — but NOT after lockout,
which requires manual re-arm (brute-force protection). The operator
POSTs the code to /pair and receives a session bearer token over TLS, used
for all subsequent tool calls. Tokens are persisted to
%APPDATA%/pc-mcp-bridge/session_token so they survive server restarts.
Multiple tokens can coexist (one per paired client); pairing appends a new
token without invalidating existing ones. Revoke one token via POST /revoke,
or all via POST /logout, or delete the token file (deleting it revokes
access immediately, even while the server runs). There is no long-term
shared secret to distribute, and nothing sensitive ever needs to travel
through chat.

Run:  python pc-agent/server.py   (from the repo root)
"""
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.gzip import (
    DEFAULT_EXCLUDED_CONTENT_TYPES,
    GZipMiddleware,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

import approval
import blob_store
import events
import hooks
from tool_wrappers import ALL_TOOLS

# --- DPI awareness (S3): one coordinate space --------------------------
# This process must be PER_MONITOR_AWARE_V2 before any HWND exists in
# it — the approval MessageBox creates one, and Windows ignores
# programmatic awareness changes made after the first window. main()
# therefore calls _apply_dpi_awareness() as its very first action.
# (The older shcore SetProcessDpiAwareness(2) call in the windows
# write provider stays as a fallback for provider-only processes;
# this process-level context supersedes it here.)

_DPI_CONTEXT_V2 = -4  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_DPI_CONTEXT_NAMES = {
    -1: "unaware",
    -2: "system",
    -3: "per_monitor",
    -4: "per_monitor_v2",
    -5: "unaware_gdi_scaled",
}
_dpi_state = {"effective": None, "set_ok": None,
              "detail": "SetProcessDpiAwarenessContext not attempted yet"}


def _read_dpi_context_raw():
    """This process's raw DPI_AWARENESS_CONTEXT value (signed), or
    None when it cannot be read (non-Windows / API unavailable)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetProcessDpiAwarenessContext.argtypes = []
        user32.GetProcessDpiAwarenessContext.restype = ctypes.c_void_p
        raw = user32.GetProcessDpiAwarenessContext()
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return None
    if raw is None:
        return None
    if raw > 2**63:  # HANDLE values -1..-5 arrive as huge unsigned
        raw -= 2**64
    return raw


def _apply_dpi_awareness() -> dict:
    """Set PER_MONITOR_AWARE_V2 for this process and record the
    outcome (return value CHECKED; the effective context is read
    back, never assumed). Safe no-op off Windows. Idempotent."""
    if os.name != "nt":
        _dpi_state.update(
            effective="not_applicable", set_ok=None,
            detail="DPI awareness is a Windows concept; skipped on "
                   f"{sys.platform}")
        return dpi_awareness_info()
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # HANDLE is pointer-sized: declare it c_void_p, never the
    # ctypes default c_int (truncates on 64-bit Windows).
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    ok = bool(user32.SetProcessDpiAwarenessContext(
        ctypes.c_void_p(_DPI_CONTEXT_V2)))
    err = ctypes.get_last_error()
    raw = _read_dpi_context_raw()
    effective = _DPI_CONTEXT_NAMES.get(raw, "unknown")
    if ok:
        detail = "SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2) ok"
    elif err == 5:  # ERROR_ACCESS_DENIED: already set (e.g. manifest)
        detail = ("SetProcessDpiAwarenessContext refused "
                  "(ERROR_ACCESS_DENIED — awareness already set); "
                  f"effective context read back as {effective}")
    else:
        detail = (f"SetProcessDpiAwarenessContext failed, "
                  f"GetLastError={err}; effective context read back "
                  f"as {effective}")
    _dpi_state.update(effective=effective, set_ok=ok, detail=detail)
    return dpi_awareness_info()


def dpi_awareness_info() -> dict:
    """Effective DPI awareness for doctor/health output. On Windows
    before main() applies it, the live context is read so the report
    is honest rather than a placeholder."""
    if os.name != "nt":
        return {"effective": "not_applicable", "set_ok": None,
                "detail": "DPI awareness is a Windows concept; "
                          f"not applicable on {sys.platform}"}
    info = dict(_dpi_state)
    if info["effective"] is None:
        raw = _read_dpi_context_raw()
        info["effective"] = _DPI_CONTEXT_NAMES.get(raw, "unknown")
    return info

HOST, PORT = "127.0.0.1", 8765
mcp = MCPServer("pc-bridge")

for _tool_fn in ALL_TOOLS:
    mcp.tool()(_tool_fn)

# --- Job layer ops (Hermes-pattern, 2026-10-23): journal-ACK intake ---
# submit_job journals the call and returns a job_id immediately; the
# tool itself executes later on a worker thread through the very same
# wrapper a sync call would use. These meta-ops are registered directly
# (not via toolcall): the journal in jobs.py is their record, and the
# inner tool execution produces the usual toolcall events + dialogs.
import jobs as _jobs


@mcp.tool()
def submit_job(tool: str, args: dict | None = None,
               idempotency_key: str = "", lineage: str = "") -> dict:
    """Submit a tool call as a background job; returns {job_id, state}
    immediately. The tool executes on the PC whether or not the caller
    stays connected — poll job_status / job_result with the job_id.
    A resubmission with the same idempotency_key returns the existing
    job and never executes twice. `lineage` records where the request
    came from: typed | relay | scheduled | agent (anything else is
    journaled as unattributed). While an ESTOP sentinel file sits in
    the bridge data dir, submissions are still accepted and journaled
    but no new job executes until the file is removed."""
    return _jobs.submit_job_op(tool, args, idempotency_key or None,
                               lineage or None)


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Job state + started/finished receipts from the journal."""
    return _jobs.job_status_op(job_id)


@mcp.tool()
def job_result(job_id: str) -> dict:
    """Job result (when terminal) from the journal / blob store."""
    return _jobs.job_result_op(job_id)


@mcp.tool()
def job_cancel(job_id: str) -> dict:
    """Cancel a queued or running job; a running job's process tree is
    fenced (only that job's processes are killed)."""
    return _jobs.job_cancel_op(job_id)


@mcp.tool()
def doctor() -> dict:
    """Read-only bridge health report: listener, tunnel, token,
    journal, and ESTOP state — every component that is not ok carries
    its one concrete fix. Never starts, stops, or changes anything."""
    report = _jobs.doctor_op()
    # S3: the effective DPI awareness rides along (jobs.py's component
    # set is its own contract; this field is added here, in server.py).
    report["dpi_awareness"] = dpi_awareness_info()
    return report

PAIRING_TTL_S = 30 * 60
MAX_PAIRING_ATTEMPTS = 5

APPDATA_DIR = Path(os.environ.get("APPDATA", os.path.expanduser("~")))
SESSION_TOKEN_FILE = APPDATA_DIR / "pc-mcp-bridge" / "session_token"

from pairing import PairingManager
_pairing = PairingManager(APPDATA_DIR)


def _refresh_pairing_code() -> None:
    """Mint a fresh pairing code and print it for the operator."""
    code = _pairing.new_code()
    print(f"PAIRING CODE: {code}")
    print(f"(single-use, expires in 30 minutes — "
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
    """Background thread: keep a usable pairing code printed.

    Refreshes when the current code is consumed or expires. Does NOT
    refresh after lockout — that would enable brute-forcing.
    """
    while True:
        time.sleep(30)
        if _pairing.is_locked():
            _pairing.write_lock()
            print()
            print("!!! PAIRING LOCKED: 5 failed attempts. "
                  "Delete %APPDATA%/pc-mcp-bridge/pairing.lock "
                  "to re-arm, or restart the server.")
            while _pairing.is_locked():
                time.sleep(30)
                if _pairing.try_rearm():
                    print("--- pairing re-armed manually: new code ---")
                    _refresh_pairing_code()
                    break
        elif _pairing.needs_refresh():
            print()
            print("--- previous pairing code consumed/expired: new code ---")
            _refresh_pairing_code()


def _load_session_tokens() -> list:
    """Read the persisted session tokens. Returns list of token strings.
    The file is the source of truth: deleting it revokes all sessions
    immediately, even while running. Backwards compatible with the old
    single-token format."""
    try:
        data = SESSION_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if not data:
            return []
        if data.startswith("["):
            return json.loads(data)
        return [data]  # old format: bare token
    except (OSError, json.JSONDecodeError):
        return []


def _load_session_token() -> str | None:
    """Legacy single-token accessor. Returns the first token, or None."""
    tokens = _load_session_tokens()
    return tokens[0] if tokens else None


def _save_session_token(token: str) -> None:
    """Append a token to the persisted list. Does NOT rotate out existing
    tokens — multiple paired clients stay connected simultaneously."""
    SESSION_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tokens = _load_session_tokens()
    if token not in tokens:
        tokens.append(token)
    SESSION_TOKEN_FILE.write_text(json.dumps(tokens) + "\n", encoding="utf-8")
    try:
        os.chmod(SESSION_TOKEN_FILE, 0o600)
    except OSError:
        pass  # %APPDATA% is already user-private via Windows ACLs


def _revoke_session_token(token: str) -> bool:
    """Remove one token. Returns True if it was present."""
    tokens = _load_session_tokens()
    if token in tokens:
        tokens.remove(token)
        SESSION_TOKEN_FILE.write_text(json.dumps(tokens) + "\n",
                                      encoding="utf-8")
        return True
    return False


def _clear_session_token() -> None:
    """Revoke ALL tokens."""
    try:
        SESSION_TOKEN_FILE.unlink()
    except OSError:
        pass


class BearerAuth:
    """Pure-ASGI bearer auth (Q4b).

    Replaces the old BaseHTTPMiddleware subclass: that base class
    ferries every request/response through a zero-buffer anyio
    rendezvous (starlette/middleware/base.py), adding a full extra
    hop that holds responses open longer. This middleware speaks
    ASGI directly — it reads the Authorization header from the
    scope and either delegates untouched or answers 401 itself.

    Behaviour is unchanged: /health, /pair and /chat are exempt;
    every other HTTP route requires `Authorization: Bearer <token>`
    matching a persisted session token (401 {"error": "unauthorized"}
    otherwise). Non-HTTP scopes (websocket /ws does its own auth,
    lifespan) pass through untouched, exactly as before.
    """

    EXEMPT_PATHS = frozenset({"/health", "/pair", "/chat"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in self.EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        auth = ""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                auth = value.decode("latin-1")
                break
        tokens = _load_session_tokens()
        valid = any(
            secrets.compare_digest(auth, f"Bearer {t}") for t in tokens
        )
        if not valid:
            await JSONResponse(
                {"error": "unauthorized"}, status_code=401
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


# Descriptive alias; BearerAuth stays the canonical name (main/tests).
BearerAuthMiddleware = BearerAuth


class JsonGzipMiddleware:
    """GZip for JSON responses (Q4d), on top of Starlette's pure-ASGI
    GZipMiddleware. Never applied to:
    - /blobs/* — raw binary payloads (already-compressed images);
      the route is bypassed by path AND its application/octet-stream
      bodies are on the exclude list (belt and braces);
    - SSE (text/event-stream) — on GZipMiddleware's default exclude
      list; compressing a stream would defeat its purpose;
    - 206 Range responses — GZipMiddleware never compresses partial
      responses.
    Small bodies (<500 B) pass through uncompressed, Starlette's
    default threshold.
    """

    def __init__(self, app):
        self.app = app
        self._gzip = GZipMiddleware(
            app,
            minimum_size=500,
            exclude_content_types=DEFAULT_EXCLUDED_CONTENT_TYPES
            + ("application/octet-stream",),
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and \
                (scope.get("path") or "").startswith("/blobs/"):
            await self.app(scope, receive, send)
            return
        await self._gzip(scope, receive, send)


def _pair_ip_allowed(request: Request) -> bool:
    """Optional IP allowlist for /pair (defense in depth).

    Reads PAIR_IP_ALLOWLIST env var: comma-separated IPs or CIDR ranges.
    Client IP comes from CF-Connecting-IP (Cloudflare), falling back to
    X-Forwarded-For, then the direct peer. Empty/unset allowlist means
    no restriction (safe default — never locks anyone out on deploy).
    """
    raw = (os.environ.get("PAIR_IP_ALLOWLIST") or "").strip()
    if not raw:
        return True
    # Real client IP through Cloudflare tunnel.
    ip_str = (request.headers.get("cf-connecting-ip")
              or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
              or (request.client.host if request.client else ""))
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


async def _pair(request: Request):
    """POST /pair {"code": "482913"} -> {"token": "<session bearer token>"}.

    The code is single-use, expires after 30 minutes, and the endpoint
    locks after 5 wrong guesses. After lockout, pairing stays closed
    until manual re-arm (delete pairing.lock) or server restart —
    it does NOT auto-refresh, preventing brute-force attacks.
    The issued token is appended to the persisted token list (survives
    restarts); existing tokens remain valid so multiple clients stay
    connected. Revoke one token via POST /revoke, all via POST /logout,
    or delete the token file.
    """
    if not _pair_ip_allowed(request):
        events.append("PairingDenied", {"reason": "ip not allowlisted"})
        return JSONResponse({"error": "pairing unavailable"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    code = str(body.get("code", "")).strip()

    ok, reason = _pairing.check(code)
    if not ok:
        if "wrong code" in reason:
            events.append("PairingDenied", {"reason": reason})
        status = 403 if "unavailable" in reason or "expired" in reason else 401
        return JSONResponse({"error": reason}, status_code=status)

    token = secrets.token_hex(32)
    _save_session_token(token)
    sid, data = events.new_session(token)
    events.append("SessionPaired", data, session_id=sid)
    return JSONResponse({"token": token})


async def _logout(request: Request):
    """POST /logout (bearer token required) -> revokes ALL sessions now."""
    _clear_session_token()
    events.append("SessionRevoked", {"reason": "logout-all"})
    return JSONResponse({"ok": True})


async def _revoke(request: Request):
    """POST /revoke (bearer token required) -> revokes just my token."""
    auth = request.headers.get("authorization", "")
    my_token = auth[7:] if auth.startswith("Bearer ") else ""
    revoked = _revoke_session_token(my_token) if my_token else False
    events.append("SessionRevoked", {"reason": "revoke-one",
                                    "revoked": revoked})
    return JSONResponse({"ok": True, "revoked": revoked})


# --- WebSocket relay: direct Cosmo <-> Cho-zen1 link ---
# Clients connect to /ws?client=<name>&token=<bearer>
# Server routes JSON messages between connected clients instantly.

_ws_clients: dict = {}  # name -> set of WebSockets (one name, many sockets)


async def _ws_relay(websocket: WebSocket):
    """WebSocket endpoint for direct agent-to-agent messaging.

    Connect: /ws?client=cosmo&token=<bearer> (or client=cho-zen1)
    Send JSON: {"to": "cho-zen1", "text": "...", "id": "..."}
    Receive JSON: {"from": "cosmo", "text": "...", "id": "...", "ts": ...}
    """
    params = dict(websocket.query_params)
    client_name = params.get("client", "").strip().lower()
    token = params.get("token", "").strip()

    # Auth: localhost (127.0.0.1) bypasses token check — physical access
    # is trusted. Remote connections must present a valid bearer token.
    client_host = websocket.client.host if websocket.client else ""
    is_localhost = client_host in ("127.0.0.1", "::1")
    tokens = _load_session_tokens()
    valid = is_localhost or any(
        secrets.compare_digest(token, t) for t in tokens
    )

    if not valid or client_name not in ("cosmo", "cho-zen1", "judith"):
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()
    _ws_clients.setdefault(client_name, set()).add(websocket)
    events.append("WsConnected", {"client": client_name})
    print(f"[ws] {client_name} connected "
          f"({sum(len(s) for s in _ws_clients.values())} sockets, "
          f"{len(_ws_clients)} names online)")

    # Notify others that someone joined
    for name, sockets in list(_ws_clients.items()):
        if name != client_name:
            for ws in list(sockets):
                try:
                    await ws.send_json({
                        "from": "system",
                        "text": f"{client_name} joined",
                        "id": f"sys-{int(time.time()*1000)}",
                        "ts": time.time(),
                    })
                except Exception:
                    pass

    try:
        while True:
            data = await websocket.receive_json()
            to = str(data.get("to", "")).strip().lower()
            text = str(data.get("text", ""))
            msg_id = str(data.get("id", ""))

            # Broadcast to all (if to is empty or "all")
            # or send to specific client
            targets = []
            if to in ("", "all"):
                targets = [n for n in _ws_clients if n != client_name]
            elif to in _ws_clients:
                targets = [to]

            delivered = []
            for t in targets:
                sent_one = False
                for ws in list(_ws_clients.get(t, ())):
                    try:
                        await ws.send_json({
                            "from": client_name,
                            "text": text,
                            "id": msg_id,
                            "ts": time.time(),
                        })
                        sent_one = True
                    except Exception:
                        pass
                if sent_one:
                    delivered.append(t)

            await websocket.send_json({"ok": True,
                                       "delivered_to": delivered,
                                       "id": msg_id})
    except WebSocketDisconnect:
        pass
    finally:
        sockets = _ws_clients.get(client_name)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del _ws_clients[client_name]
        events.append("WsDisconnected", {"client": client_name})
        print(f"[ws] {client_name} disconnected "
              f"({sum(len(s) for s in _ws_clients.values())} sockets, "
              f"{len(_ws_clients)} names online)")


async def _health(request):
    return JSONResponse({"ok": True, "service": "pc-bridge",
                         "dpi_awareness": dpi_awareness_info()})


_BLOB_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BLOB_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


async def _blob_download(request: Request):
    """GET /blobs/<sha256-hex> (S1) — bulk by reference, never inline.

    Auth is the BearerAuth middleware's job (this route is NOT
    exempt): unauthenticated requests 401 before reaching here.
    Unknown or malformed hashes 404. Supports a single Range:
    `Range: bytes=start-end` (open-ended and suffix forms included)
    answers 206 with Content-Range; no Range answers the full body
    with 200. Raw bytes, application/octet-stream, correct
    Content-Length, never gzipped (see JsonGzipMiddleware).
    """
    digest = request.path_params.get("digest", "")
    if not _BLOB_HEX_RE.match(digest):
        return JSONResponse({"error": "not found"}, status_code=404)
    data = blob_store.get(f"sha256:{digest.lower()}")
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    size = len(data)
    range_header = (request.headers.get("range") or "").strip()
    match = _BLOB_RANGE_RE.match(range_header) if range_header else None
    if match and (match.group(1) or match.group(2)):
        start_s, end_s = match.groups()
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:  # suffix range: the last N bytes
            start = max(0, size - int(end_s))
            end = size - 1
        end = min(end, size - 1)
        if start >= size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}",
                         "Accept-Ranges": "bytes"})
        return Response(
            data[start:end + 1], status_code=206,
            media_type="application/octet-stream",
            headers={"Content-Range": f"bytes {start}-{end}/{size}",
                     "Accept-Ranges": "bytes"})
    # No (or unparseable / multi-) Range header: full body. Per
    # RFC 9110 an invalid Range is ignored, not an error.
    return Response(data, media_type="application/octet-stream",
                    headers={"Accept-Ranges": "bytes"})


async def _chat_page(request):
    """Serve the agent chat UI. Open http://127.0.0.1:8765/chat"""
    try:
        html = Path(__file__).parent.joinpath("chat.html").read_text(
            encoding="utf-8")
        return HTMLResponse(html)
    except OSError:
        return JSONResponse({"error": "chat.html not found"}, status_code=404)


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


def build_app():
    """Assemble the Starlette app: MCP (JSON-response mode) + the
    bridge's own routes + auth/gzip middleware. Split from main() so
    tests can build the exact production app without uvicorn."""
    # The SDK auto-enables DNS-rebinding protection for localhost servers,
    # which 421s any Host header that isn't localhost — including our
    # Cloudflare tunnel hostname (random per session, can't be allowlisted).
    # Disabled here: the server binds 127.0.0.1-only, every route (except
    # /health and /pair) requires the session bearer token, and the tunnel
    # is user-initiated. The bearer token, not the Host header, is the
    # real authentication.
    #
    # Q4a: json_response=True — each POST is answered with one plain
    # JSON body with a known Content-Length (SDK _create_json_response)
    # instead of a stateful SSE stream ferried through zero-buffer
    # rendezvous hops. Session semantics are otherwise unchanged:
    # stateless_http stays False, initialize still issues an
    # mcp-session-id and later calls still present it.
    app = mcp.streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    )
    app.routes.append(Route("/health", _health))
    app.routes.append(Route("/chat", _chat_page))
    app.routes.append(Route("/pair", _pair, methods=["POST"]))
    app.routes.append(Route("/logout", _logout, methods=["POST"]))
    app.routes.append(Route("/revoke", _revoke, methods=["POST"]))
    app.routes.append(Route("/blobs/{digest}", _blob_download,
                            methods=["GET"]))
    app.routes.append(WebSocketRoute("/ws", _ws_relay))
    # Middleware added last runs outermost: auth rejects before any
    # compression work; gzip wraps only the app + routes inside it.
    app.add_middleware(JsonGzipMiddleware)
    app.add_middleware(BearerAuth)
    return app


def main():
    # S3: FIRST action, before any HWND can exist in this process
    # (the approval MessageBox creates one) — set per-monitor-v2
    # DPI awareness and check that it actually took.
    dpi = _apply_dpi_awareness()
    print(f"dpi: effective awareness = {dpi['effective']} "
          f"(set_ok={dpi['set_ok']}) — {dpi['detail']}")
    _refresh_pairing_code()
    threading.Thread(target=_pairing_refresher, daemon=True).start()
    threading.Thread(target=_heartbeat_writer, daemon=True).start()
    # Gate dontask mode: require explicit confirmation at startup
    # unless PC_BRIDGE_FULL_ACCESS_CONFIRM=0 (trusted automated startup)
    if approval.get_permission_mode() == "dontask":
        from startup_confirm import confirm_dontask_mode
        if not confirm_dontask_mode():
            print("dontask: not confirmed, falling back to default mode")
            approval.override_permission_mode("default")
    app = build_app()

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
