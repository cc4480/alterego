# PC setup — the Windows half

You run this on the Windows PC you want the operator to reach. Nothing
here phones home: the PC only ever dials **out** through a tunnel you
start yourself, and you approve every write action on your own screen.

Day to day, two things run side by side:

| Piece | What it is | Command |
|---|---|---|
| Agent | the MCP server, loopback only | `pc-agent\.venv\Scripts\python.exe pc-agent\server.py` |
| Tunnel | named Cloudflare tunnel → `https://pc.secscan.info` | `cloudflared tunnel run pc-bridge` |

## Prerequisites

- Windows 10 or 11.
- Python 3.11+ from https://www.python.org/downloads/ (check
  **"Add python.exe to PATH"** in the installer).
- git.
- `cloudflared`: `winget install cloudflare.cloudflared`, plus a named
  tunnel you own — this deployment uses the tunnel named `pc-bridge`,
  configured in `%USERPROFILE%\.cloudflared\config.yml`, publishing
  `https://pc.secscan.info`.

## 1. Install — in its own virtual environment

```powershell
git clone https://github.com/cc4480/alterego
cd alterego
python -m venv pc-agent\.venv
pc-agent\.venv\Scripts\python.exe -m pip install -r pc-agent\requirements.txt
```

**The venv is not optional decoration.** `pc-agent/requirements.txt`
pins `mcp>=2.0`, and the server is written against the 2.x API
(`MCPServer`, `streamable_http_app`). Other MCP tooling is often pinned
to `mcp<2`; whichever environment they share, one side breaks — a
global `mcp<2` install shows up here as import errors and HTTP 421
Host-header rejections. Keep the bridge in `pc-agent\.venv` and always
launch it with that interpreter. The other requirements
(`uvicorn`, `mss`, `pywin32`, `psutil`, `websocket-client`) install
into the same venv.

## 2. Start the agent

```powershell
pc-agent\.venv\Scripts\python.exe pc-agent\server.py
```

Startup output looks like:

```
PAIRING CODE: 482913
(single-use, expires in 30 minutes — the operator POSTs it to /pair to receive a session token)
pc-mcp-bridge listening on http://127.0.0.1:8765 (loopback only)
tunnel:  cloudflared tunnel --url http://127.0.0.1:8765

session: no operator paired yet
logout: POST /logout with the bearer token, or delete
        %APPDATA%/pc-mcp-bridge/session_token (revokes instantly)
events: %APPDATA%/pc-mcp-bridge/events/  (primary operation log)
```

- It listens on **loopback only** — unreachable from the network
  except through your tunnel.
- The **6-digit pairing code** is how the operator gets in. It's safe
  to share (paste it in chat): cryptographically random, single-use,
  expires in 30 minutes, and locks pairing after 5 wrong guesses.
  Locked pairing does **not** auto-refresh — delete
  `%APPDATA%\pc-mcp-bridge\pairing.lock` or restart the server to
  re-arm. No long-term secret is ever distributed.
- If a session already exists, you'll see
  `session: existing operator session restored` — tokens persist
  across restarts until revoked.
- Every tool call (read and write) is appended as a typed event to
  the operation log at `%APPDATA%\pc-mcp-bridge\events\`. Query it
  with the `query_events` tool; replay a session with
  `replay_session`.

## 3. Start the named tunnel (second terminal)

```powershell
cloudflared tunnel run pc-bridge
```

The address is permanent — **https://pc.secscan.info** — so restarts
never change it and never force a re-pair. (Quick tunnels, the old
random `trycloudflare.com` flow, are gone.) Healthy output shows the
tunnel registering its connections; note that a *registered tunnel*
only proves cloudflared is up — see
[troubleshooting.md](troubleshooting.md) for the case where the
tunnel is up but the server behind it is not.

## 4. Hand the pairing code to the operator

The operator already knows the tunnel address. The only thing to
share is the 6-digit code from step 2.

The operator exchanges it at `POST /pair` and receives a session
bearer token over TLS. The token never touches chat. On your PC it is
appended to `%APPDATA%\pc-mcp-bridge\session_token`, so it **survives
server restarts** — restarting the server does *not* disconnect the
operator. Multiple operators can be paired at once; each pairing
**adds** a token without invalidating the others.

## Logging the operator out

Any of these takes effect immediately:

1. **Delete `%APPDATA%\pc-mcp-bridge\session_token`** — revokes *all*
   sessions. The server re-reads the file on every request, so this
   works even while the server is running.
2. `POST /logout` with a bearer token — revokes all sessions.
3. `POST /revoke` with a bearer token — revokes just that one token
   (or have the operator run `python3 bridge/op_call.py logout` for
   the all-sessions variant).

## Daily use

### Autostart (scheduled tasks)

`scripts\install_autostart.ps1` (run once, elevated) installs two
logon tasks that restart on failure:

- **PCBridgeServer** — runs `scripts\start_bridge.ps1` in your
  interactive session (so approval dialogs are visible and the pairing
  code is readable). The launcher refuses to stack a second server if
  127.0.0.1:8765 is already listening, and clears orphaned
  `server.py` processes first.
- **PCBridgeTunnel** — runs `cloudflared tunnel run pc-bridge`
  headless.

Both scripts carry the repo path as a variable at the top — set it to
your clone's location before running. (They enumerate only from an
elevated shell; that's expected.)

### Watchdog

`scripts\watchdog.ps1` is built to run every 5 minutes (scheduled
task `PCBridgeWatchdog`). Each cycle it checks the tunnel
(cloudflared's metrics endpoint must report ≥1 HA connection) and
the server (port 8765 listening **and** the heartbeat file
`%APPDATA%\pc-mcp-bridge\heartbeat.txt` fresher than 180 s — a hung
process can hold the port, so the port alone isn't proof of life),
restarting whichever is down and logging to
`%APPDATA%\pc-mcp-bridge\watchdog.log`.

### Updating

```powershell
cd <your clone>
git pull
# restart the server (the PCBridgeServer task, or the manual command below)
```

### Starting fresh manually

One paste — kills any leftover server on 8765, then starts:

```powershell
$pid8765 = (Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess; if ($pid8765) { Stop-Process -Id $pid8765 -Force; "killed old server ($pid8765)" }; pc-agent\.venv\Scripts\python.exe pc-agent\server.py
```

**Stopping:** close the terminals (or Ctrl+C). Stopping the server
does **not** log the operator out — the session token persists. See
"Logging the operator out" when you want them gone.

## What to expect while the operator works

- Read tools (screenshot, window list, system info, files in your
  profile) just work — no prompts.
- Write tools pop a native Windows **Yes/No dialog on your screen**
  showing the exact action. 30 seconds with no answer = **denied**.
  No interactive session = fail closed.
- Destructive tools (shell, file write/delete, process kill, power,
  page JavaScript, batch) always pop a DESTRUCTIVE-titled dialog —
  even in auto-approve mode.
- Long-running work may arrive through the **job layer**: submitted
  once, executed in the background, still behind the same approval
  dialogs. Dropping a file named `ESTOP` into
  `%APPDATA%\pc-mcp-bridge\` pauses all new job execution until you
  remove it.

## Permission postures

Set `PC_BRIDGE_PERMISSION_MODE` before starting the server:

| Mode | Behavior |
|---|---|
| `default` | Reads silent, writes ask, destructive always asks |
| `plan` | Every write runs as a dry-run — plans and diffs, zero side effects |
| `acceptEdits` | File edits auto-approved; shell and destructive tools still ask |
| `dontAsk` | **No dialogs at all** — every tool executes, including destructive ones |

`dontAsk` is gated: at startup the server shows an on-PC confirmation
dialog, and falls back to `default` if you decline or let it time out
(`PC_BRIDGE_FULL_ACCESS_CONFIRM=0` skips that gate for trusted
automated startups). The legacy `PC_BRIDGE_FULL_ACCESS=1` env var is
treated as `dontAsk`. The related `PC_BRIDGE_AUTO_APPROVE=1` skips
dialogs for routine writes but destructive tools **still ask**.

In every mode: pairing and the bearer token are still required, and
the event log records everything. Restart without the env vars to
restore dialogs. Full detail: [security.md](security.md) and
[configuration.md](configuration.md).

Something not working? [troubleshooting.md](troubleshooting.md).
