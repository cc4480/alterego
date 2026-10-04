# pc-mcp-bridge

A prototype MCP bridge that lets an AI operator interact with a Windows PC:
the PC runs an MCP server (read tools + approval-gated write tools), the PC
dials out through a Cloudflare quick tunnel, and the operator talks MCP
(Streamable HTTP) through it.

**Auth:** interactive pairing — the server prints a 6-digit code (single-use,
5-minute expiry, 5-attempt lockout); the operator exchanges it at `/pair` for
a session bearer token. No long-term secret to distribute.

**Status: prototype.** The Linux bridge half is tested here. The Windows
`pc-agent/` half is tested by the user on their PC (see `docs/setup.md`).

## Layout

- `pc-agent/` — Windows side. FastMCP server on `127.0.0.1:8765`, bearer-token
  auth, native Yes/No approval dialogs for every write tool, local audit log.
- `bridge/` — operator side (this VM). Minimal MCP client CLI + a stdlib-only
  mock MCP server for testing the client without a PC.
- `docs/setup.md` — exact PC setup steps for the user.

## Security model (serious, even for a prototype)

- Server binds **loopback only** (`127.0.0.1`). The only inbound path is the
  outbound Cloudflare tunnel the user starts themselves.
- **Pairing, not pre-shared secrets:** the server prints a 6-digit code
  (cryptographically random, single-use, 5-minute expiry, locks after 5 wrong
  guesses until restart). The operator exchanges it at `POST /pair` for a
  session bearer token delivered over TLS. The session token lives only in
  the operator's session memory and dies with the server. Nothing
  long-lived ever travels through chat; the 6-digit code is safe to share
  because it expires in minutes and can't be reused.
- The tunnel URL is *not* treated as a secret — it's just the address.
  Authentication is the session bearer token.
- Write tools (`focus_window`, `type_text`, `shell_exec`) each pop a native
  Windows approval dialog showing the exact action; 30s timeout = deny; no
  interactive session = fail closed. Every call (read and write) is appended
  to a local audit log.
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
