# PC setup — the Windows half

You run this on the Windows PC you want the operator to reach. Nothing here
phones home: the PC only ever dials **out** through a tunnel you start
yourself, and you approve every write action on your own screen.

You need two terminal windows open while the operator works:

| Terminal | Runs | Command |
|---|---|---|
| 1 — agent | the MCP server | `python pc-agent\server.py` |
| 2 — tunnel | Cloudflare quick tunnel | `cloudflared tunnel --url http://127.0.0.1:8765` |

## Prerequisites

- Windows 10/11, Python 3.11+ from https://www.python.org/downloads/
  (check **"Add python.exe to PATH"** in the installer), and git.
- `cloudflared`: `winget install cloudflare.cloudflared`

## 1. Install

```powershell
git clone https://github.com/cc4480/pc-mcp-bridge
cd pc-mcp-bridge
python -m pip install -r pc-agent\requirements.txt
```

Use `python -m pip` (not bare `pip`): on machines with several Pythons it
guarantees the packages land where *that* `python` looks. If you get
`ModuleNotFoundError: No module named 'mcp'` later, this step is what fixes it.

## 2. Start the agent

```powershell
python pc-agent\server.py
```

It prints something like:

```
pc-mcp-bridge listening on http://127.0.0.1:8765 (loopback only)
tunnel:  cloudflared tunnel --url http://127.0.0.1:8765

PAIRING CODE: 279416
(single-use, expires in 30 minutes — the operator POSTs it to /pair to receive a session token)
audit: %APPDATA%/pc-mcp-bridge/events/ (one JSONL file per day)
```

- It listens on **loopback only** — unreachable from the network except
  through your tunnel.
- The **6-digit pairing code** is how the operator gets in. It's safe to
  share (paste it in chat): it's cryptographically random, single-use,
  expires 30 minutes after the server starts, and locks after 5 wrong
  guesses. No long-term secret is ever distributed.
- Every tool call (read and write) is appended as a typed event to the
  event-sourced operation log at `%APPDATA%\pc-mcp-bridge\events\`.
  Query with `query_events`, replay sessions with `replay_session`.

## 3. Open the tunnel (second terminal)

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

Copy the `https://<random>.trycloudflare.com` URL it prints. The tunnel URL
is just an address, not a credential — sharing it is safe.

## 4. Hand two things to the operator

Both are safe to share in chat:

1. The `https://....trycloudflare.com` tunnel URL.
2. The 6-digit pairing code from step 2.

The operator exchanges the code at `POST /pair` and receives a session
bearer token over TLS. That token never touches chat — it lives only in the
operator's session memory, and it's persisted on your PC at
`%APPDATA%\pc-mcp-bridge\session_token` so it **survives server restarts**.
It lasts until you log the operator out (see below) — restarting the
server does *not* disconnect them.

## Logging the operator out

Either of these revokes access immediately:

1. Delete `%APPDATA%\pc-mcp-bridge\session_token` — the server checks the
   file on every request, so this works even while the server is running.
2. Ask the operator to `POST /logout` with their token (or run
   `python3 bridge/pc_bridge.py logout`).

Pairing again with a fresh code rotates the token — the old one stops
working.

## Daily use

**Starting fresh** (one paste — kills any leftover server, then starts):

```powershell
$pid8765 = (Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess; if ($pid8765) { Stop-Process -Id $pid8765 -Force; "killed old server ($pid8765)" }; python pc-agent\server.py
```

**Updating to the latest code:**

```powershell
cd <your>\pc-mcp-bridge
git pull
# then start fresh with the one-paste command above
```

**Stopping:** close both terminals (or Ctrl+C in each). The tunnel URL dies
with it. Note: stopping the server does **not** log the operator out — the
session token persists. See "Logging the operator out" above when you want
them gone.

## What to expect while the operator works

- Read tools (screenshot, window list, system info, files in your profile)
  just work — no prompts.
- Every write tool (`focus_window`, `type_text`, `shell_exec`) pops a
  native Windows **Yes/No dialog on your screen** showing the exact action.
  30 seconds with no answer = **denied**. No interactive session = fail
  closed. You are the final say on everything the operator tries to do.

## Auto-approve mode (your own testing only)

If you're driving a long automation and don't want to click Yes on every
step, start the server with the dialogs off:

```powershell
$env:PC_BRIDGE_AUTO_APPROVE=1; python pc-agent\server.py
```

- The server prints a loud warning banner so you know dialogs are off.
- Every auto-approved call is marked `AUTO-APPROVED, no dialog shown` in
  the event log.
- Pairing and the bearer token are **still required** — this only skips
  the per-action prompts, it doesn't open the server to anyone.
- Restart normally (without the env var) to bring the dialogs back.
- Never run someone else's PC in this mode, and never leave it on
  unattended.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'mcp'` | `python -m pip install -r pc-agent\requirements.txt` |
| `[Errno 10048] ... only one usage of each socket address` (port 8765) | An old server is still running — use the kill-first one-paste command above |
| Operator gets 401 on everything | The session token was revoked (owner logged you out or re-paired). Ask for a fresh pairing code and re-pair |
| Pairing code rejected / expired | Codes are single-use and expire after 30 min. Restart the server for a fresh one |
| `cloudflared` not recognized | `winget install cloudflare.cloudflared`, then open a new terminal |
| Tunnel URL stopped working | Quick tunnels get a new random URL on every restart — copy the new one to the operator and re-pair |
| `pip install` fails on `pywin32` | Run the terminal as Administrator once, or `pip install --only-binary :all: pywin32` |
