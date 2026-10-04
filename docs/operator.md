# Operator guide — the controlling side

You are the operator: an AI agent (or a human with curl) driving someone
else's Windows PC through their tunnel. The PC owner runs the server and
hands you two things in chat — both are safe to share:

1. The tunnel URL, e.g. `https://<random>.trycloudflare.com`
2. A 6-digit pairing code (single-use, 30-minute expiry)

## 1. Pair

Exchange the code for a session bearer token. With the bundled CLI:

```bash
export PC_BRIDGE_URL=https://<random>.trycloudflare.com
python3 bridge/pc_bridge.py pair 279416        # prints the 64-hex session token
export PC_BRIDGE_TOKEN=<paste the printed token>
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
- It dies when the PC server restarts. If every call starts returning 401,
  the server was restarted: ask the owner for a fresh pairing code and
  re-pair. Do not ask for any other secret — there isn't one.
- Pairing codes can't be reused and lock after 5 wrong guesses (until the
  owner restarts the server).

## 2. Use the tools

```bash
python3 bridge/pc_bridge.py tools                            # list tools
python3 bridge/pc_bridge.py call system_info '{}'            # read tool
python3 bridge/pc_bridge.py screenshot --out /tmp/shot.png   # screenshot
python3 bridge/pc_bridge.py call shell_exec '{"command":"whoami"}'
```

Write tools pop a native Yes/No dialog **on the PC owner's screen** — the
call blocks until they answer (30 s timeout = deny). A denial comes back
as `{"error": "..."}`, not a crash.

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
python3 mock_mcp_server.py 8765 &
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=x python3 pc_bridge.py tools
```

The mock serves the same tool shapes without a PC or tunnel.
