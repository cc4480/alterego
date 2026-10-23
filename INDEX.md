# AlterEgo — Folder Index
<!-- Owner: bridge-folder subagent | Last indexed: 2026-10-07 -->

Windows PC MCP bridge. AI operator drives Carlos's PC through a named Cloudflare tunnel.
Live at **https://pc.secscan.info** · Repo: https://github.com/cc4480/alterego (public)

## Layout

| Path | What | Modified |
|---|---|---|
| `README.md` | Project overview, auth model, doc links | 2026-10-06 |
| `bridge/` | Operator-side client code (Linux/VM side) | 2026-10-05 |
| `bridge/op_call.py` | **Primary CLI** — health checks, tool calls via `call` subcommand | 2026-10-07 |
| `bridge/pc_bridge.py` | MCP client library | 2026-10-04 |
| `bridge/token_store.py` | Session token persistence (0600) | 2026-10-05 |
| `bridge/demo*.py`, `bridge/s1*.py` | Demo scripts from Oct 5 verification runs | 2026-10-05 |
| `bridge/mock_mcp_server.py` | Local mock for testing without the PC | 2026-10-04 |
| `pc-agent/` | Server-side code (runs on Carlos's Windows PC) | 2026-10-05 |
| `pc-agent/server.py` | MCP server entrypoint | 2026-10-05 |
| `pc-agent/tools_*.py` | Tool implementations (read/write/browser/tasks/etc.) | 2026-10-05 |
| `pc-agent/tool_profiles.py` | Blast radius, recoverability, sensitivity per tool | 2026-10-05 |
| `pc-agent/pairing.py`, `auth.py` | Pairing codes, session tokens | 2026-10-05 |
| `pc-agent/browser_cdp.py` | Edge CDP browser control | 2026-10-05 |
| `pc-agent/subproc.py` | `run_noinherit` — fixes pipe-hang on grandchildren | 2026-10-05 |
| `docs/setup.md` | PC owner guide: install, tunnel, pair, troubleshoot | 2026-10-06 |
| `docs/operator.md` | Operator guide: CLI usage, token handling | 2026-10-06 |
| `docs/tools.md` | Tool reference (args, returns, approval tiers) | 2026-10-07 |
| `scripts/` | PC-side PowerShell helpers | 2026-10-05 |
| `scripts/start_bridge.ps1` | Server launcher (called by Startup shortcut) | 2026-10-05 |
| `scripts/install_autostart.ps1` | One-time Startup folder setup | 2026-10-05 |
| `scripts/watchdog.ps1` | Process watchdog | 2026-10-05 |
| `scripts/validate_seams.py` | Seam validation | 2026-10-05 |
| `demo-evidence/` | Screenshots from live verification demos | 2026-10-05 |
| `tool-exam-2026-10-05.md` | Tool-by-tool exam notes | 2026-10-05 |

## Key facts
- **52 tools** (Carlos corrected 2026-10-07). Recent adds: `uia_find`, `uia_click`, `uia_set_text`.
- Tunnel: named `pc-bridge` → https://pc.secscan.info
- Token: `~/.config/pc-bridge/token` (0600), survives restarts
- Health check: `PC_BRIDGE_URL="https://pc.secscan.info" python3 ~/workspace/alterego/bridge/op_call.py health`
- Standing rule: run health check at start of every bridge session

## Known staleness
- `README.md` still says "49 tools" — needs update to 52 (docs/tools.md is current as of 2026-10-07)
