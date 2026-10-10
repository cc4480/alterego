# Operator guide — the controlling side

You are the operator: an AI agent (or a human with curl) driving someone
else's Windows PC through their tunnel. The PC owner runs the server and
hands you one thing in chat — safe to share:

1. A 6-digit pairing code (single-use, 30-minute expiry)

The tunnel address is permanent: `https://pc.secscan.info` (named
Cloudflare tunnel `pc-bridge`).

## 0. Health check (run first, every session)

```bash
export PC_BRIDGE_URL=https://pc.secscan.info
python3 bridge/op_call.py health
```

Reports three layers independently:

- `tunnel: UP/DOWN` — can we reach the PC at all (no auth needed)
- `server: UP` — MCP handshake succeeded
- `token: <redacted> / NONE / rejected` — session validity

If the tunnel is DOWN, the PC is off, the tunnel isn't running, or
there's a network issue — server/token checks are skipped. Diagnose
before retrying blindly.

## 1. Pair

Exchange the code for a session bearer token. With the bundled CLI:

```bash
export PC_BRIDGE_URL=https://pc.secscan.info
python3 bridge/op_call.py pair 279416        # saves the 64-hex session token
```

Or raw HTTP:

```bash
curl -s -X POST $PC_BRIDGE_URL/pair \
  -H 'Content-Type: application/json' \
  -d '{"code":"279416"}'
# -> {"token":"<64 hex chars>"}   (200), 401 (wrong code), 403 (already used)
```

**Token handling rules:**

- The token lives only in your session memory / environment — never in
  chat, logs, files, or URLs.
- It **persists on the PC across server restarts** and lasts until logout.
  A 401 on every call means the owner revoked you (or re-paired someone
  else): ask for a fresh pairing code and re-pair. Do not ask for any
  other secret — there isn't one.
- Pairing again **rotates** the token — the previous one stops working.
- When you're done, log yourself out:
  `PC_BRIDGE_URL=... python3 bridge/op_call.py logout` (revokes the
  session server-side and clears the saved token)
- Pairing codes can't be reused and lock after 5 wrong guesses (until the
  owner restarts the server).

## 2. Use the tools

```bash
python3 bridge/op_call.py tools                      # list tools
python3 bridge/op_call.py call system_info '{}'      # read tool
python3 bridge/op_call.py call screenshot '{}'       # screenshot (JSON -> /tmp/shot-full.json)
python3 bridge/op_call.py call shell_exec '{"command":"whoami"}'
```

Write tools pop a native Yes/No dialog **on the PC owner's screen** — the
call blocks until they answer (30 s timeout = deny). A denial comes back
as `{"error": "..."}`, not a crash. Approval is tiered (see tools.md):
reads never ask, routine/standard writes ask (skipped when the server runs
with `PC_BRIDGE_AUTO_APPROVE=1`), and destructive tools (`power`,
`kill_process`, `delete_file`, `shell_exec`, `shell_pwsh`, `browser_eval`,
`write_file`, `edit_file`, `batch`) **always** pop a DESTRUCTIVE-titled
dialog — auto-approve mode cannot bypass them.

See [tools.md](tools.md) for the full tool reference.

## 3. Protocol notes (if you're writing your own client)

- Transport: MCP **Streamable HTTP** — `POST {url}/mcp` with
  `Accept: application/json, text/event-stream`.
- Handshake: `initialize` → read the `Mcp-Session-Id` response header →
  send it back on every subsequent request → `notifications/initialized`
  (no id) → `tools/list` / `tools/call`.
- Responses arrive as SSE `data:` lines — take the last one, JSON-parse it.
- Auth: `Authorization: Bearer <session token>` on every request,
  including `/pair`-adjacent calls. `/health` and `/pair` are the only
  unauthenticated routes.
- Don't route the bearer token through an egress proxy (`trust_env=False`
  in httpx): some `no_proxy` forms break parsing and proxies would see
  the token.
- The tunnel hostname is random per session, so the server disables the
  MCP SDK's Host-header check — the bearer token is the real auth.

## 4. No-PC testing

```bash
cd bridge
MOCK_TOKEN=secret python3 mock_mcp_server.py 8765 &
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=secret python3 op_call.py tools
```

The mock serves the real tool schemas (`bridge/tools.json`) with canned
responses — no PC or tunnel needed. (`pc_bridge.py` is deprecated; use
`op_call.py`.)
