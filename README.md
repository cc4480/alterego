# pc-mcp-bridge

An MCP bridge that lets an AI operator interact with a Windows PC: the PC
runs an MCP server (read tools + approval-gated write tools), the PC dials
out through a Cloudflare quick tunnel, and the operator talks MCP
(Streamable HTTP) through it.

**Auth:** interactive pairing — the server prints a 6-digit code
(cryptographically random, single-use, 30-minute expiry, 5-attempt
lockout); the operator exchanges it at `POST /pair` for a session bearer
token over TLS. The token is persisted on the PC (`%APPDATA%\pc-mcp-bridge\session_token`)
so it survives server restarts, and lasts until logout (`POST /logout`, or
deleting the token file — which revokes instantly). No long-term secret to
distribute, nothing sensitive in chat.

**Status: working prototype, live-tested end-to-end** (2026-10-04) —
pairing, authenticated MCP session, tool calls, and screenshots verified
against a real Windows PC through a live Cloudflare tunnel.

## Docs

- [docs/setup.md](docs/setup.md) — PC owner: install, run, tunnel, pair,
  daily use, troubleshooting.
- [docs/operator.md](docs/operator.md) — operator: pairing, CLI usage,
  token handling, protocol notes.
- [docs/tools.md](docs/tools.md) — the 8 tools: args, returns, examples.

## Layout

- `pc-agent/` — Windows side. FastMCP server on `127.0.0.1:8765`, bearer-token
  auth, native Yes/No approval dialogs for every write tool, local audit log.
- `bridge/` — operator side. `pc_bridge.py`: minimal MCP client CLI
  (`pair`, `tools`, `call`, `screenshot`); `mock_mcp_server.py`: stdlib-only
  fake PC for testing the client without a PC or tunnel.

## Security model (serious, even for a prototype)

- Server binds **loopback only** (`127.0.0.1`). The only inbound path is the
  outbound Cloudflare tunnel the PC owner starts themselves.
- **Pairing, not pre-shared secrets:** the server prints a 6-digit code
  (cryptographically random, single-use, 30-minute expiry, locks after 5
  wrong guesses until restart). The operator exchanges it at `POST /pair`
  for a session bearer token delivered over TLS. The token is persisted to
  `%APPDATA%\pc-mcp-bridge\session_token` so it survives server restarts;
  re-pairing rotates it. It lasts until the owner logs the operator out —
  either `POST /logout` with the token, or deleting the token file, which
  the server checks on every request and revokes **instantly**, even while
  running (mechanical kill switch). The 6-digit code is safe to share
  because it expires in minutes and can't be reused.
- The tunnel URL is *not* treated as a secret — it's just the address.
  Authentication is the session bearer token.
- Write tools (`focus_window`, `type_text`, `shell_exec`) each pop a native
  Windows approval dialog showing the exact action; 30s timeout = deny; no
  interactive session = fail closed. Every call (read and write) is appended
  to a local audit log at `%APPDATA%\pc-mcp-bridge\audit.log`.
- Read tools are restricted to the user's own profile directory; Windows
  system dirs and other users' profiles are denied.
- The MCP SDK's DNS-rebinding host check is disabled: the tunnel hostname is
  random per session and can't be allowlisted. The bearer token, not the Host
  header, is the real authentication.

## Quick test (no PC needed)

```bash
cd bridge
python3 mock_mcp_server.py 8765 &          # fake PC
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=x python3 pc_bridge.py tools
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=x python3 pc_bridge.py screenshot --out /tmp/shot.png
```
