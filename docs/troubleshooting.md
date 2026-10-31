# Troubleshooting

Diagnose by layer, in this order: **tunnel → server → token → tool**.
`python3 bridge/op_call.py health` reports the first three
independently; on the PC, the server console and
`%APPDATA%\pc-mcp-bridge\watchdog.log` tell the rest.

## Tunnel shows "registered" but every request fails — the server is down

**Field-proven, and the most misdiagnosed failure this bridge has.**

Symptom: `cloudflared` output shows all tunnel connections
**registered**, yet requests through `https://pc.secscan.info` fail
with an error like:

```
dial tcp 127.0.0.1:8765: connectex: No connection could be made
because the target machine actively refused it
```

**Diagnosis:** the tunnel is healthy — it's the *server process*
that isn't listening on 8765. cloudflared happily registers with
nothing behind it. Restarting the tunnel fixes nothing.

**Fix:** restart the **server** on the PC — the `PCBridgeServer`
scheduled task, or:

```powershell
pc-agent\.venv\Scripts\python.exe pc-agent\server.py
```

(from the clone, in an interactive session). Confirm on the PC with
`Get-NetTCPConnection -LocalPort 8765 -State Listen`.

## `op_call.py health` says tunnel: DOWN

The PC is off, `cloudflared` isn't running, or the network path is
broken. On the PC: check the `PCBridgeTunnel` task / run
`cloudflared tunnel run pc-bridge` in a terminal and watch it
register. The watchdog script also restarts a dead tunnel — check
`watchdog.log` for its verdicts.

## Port 8765 already in use

```
[Errno 10048] error while attempting to bind ... only one usage of
each socket address is normally permitted
```

An old server is still running. `scripts\start_bridge.ps1` refuses
to stack a duplicate (and clears orphaned `server.py` processes);
to force a clean restart by hand, use the kill-first one-paste in
[setup.md](setup.md#starting-fresh-manually).

A subtler variant: the port is listening but the server is hung.
The server writes `%APPDATA%\pc-mcp-bridge\heartbeat.txt` every
60 s; if that timestamp is more than ~180 s old, treat the process
as hung and restart it (this is exactly what the watchdog does).

## 401 on everything

The session token is gone or revoked: the owner deleted
`%APPDATA%\pc-mcp-bridge\session_token`, ran `POST /logout`, or
revoked your token with `POST /revoke`. The operator CLI clears its
saved token when it sees a 401. Get a fresh pairing code from the
owner and re-pair — there is no other credential to fix.

## Pairing code rejected, expired, or locked

- Codes are **single-use** and expire **30 minutes** after they're
  minted. A background thread prints a fresh code when one is
  consumed or expires — check the server console for the newest
  `PAIRING CODE:` line.
- After **5 wrong guesses**, pairing locks and does *not* re-arm
  itself. The owner deletes `%APPDATA%\pc-mcp-bridge\pairing.lock`
  (the server notices and prints a new code) or restarts the
  server.
- `/pair` answers: `401` = wrong code (attempts remaining are in
  the error), `403` = pairing unavailable / expired / already used.

## `No module named 'mcp'`, MCPServer import errors, or HTTP 421s

You're running the wrong Python environment. The bridge is built on
the **`mcp` 2.x API** and pins `mcp>=2.0`; a global Python carrying
`mcp<2` (installed for other MCP tooling) produces exactly these
symptoms — import failures, or 421 Host-header rejections from the
older SDK's defaults. Always launch with the venv interpreter:

```powershell
pc-agent\.venv\Scripts\python.exe pc-agent\server.py
```

See the venv rule in [setup.md](setup.md#1-install--in-its-own-virtual-environment).

## Every write comes back "denied by local approval"

The approval dialog is the gate, and it fails closed:

- **The dialog can't render.** The server must run in the owner's
  **interactive session**. Started as a background service or in
  session 0, there's no desktop to show the dialog on, so every
  write is denied. Use the logon scheduled task
  (`scripts\install_autostart.ps1`) or start it from the owner's
  own terminal.
- **Nobody clicked.** 30 s with no answer = deny, by design.
- **A hook or rule denied it.** Check for a `command_rules.json`
  deny match (shell tools) or a PreToolUse hook policy; denials
  from hooks say so (`denied by hook: ...`) and are in the event
  log (`query_events` with `type=ToolDenied`).
- **It's `plan` posture, not a denial.** In `plan` mode writes
  return dry-run plans / would-execute stubs instead of executing.
  Check the startup banner and `PC_BRIDGE_PERMISSION_MODE`.

## The server started without dialogs (or: dialogs won't turn off)

The startup console prints a loud banner naming the active posture.
Dialogs off means one of: `PC_BRIDGE_PERMISSION_MODE=dontAsk`,
`PC_BRIDGE_FULL_ACCESS=1`, or (routine writes only)
`PC_BRIDGE_AUTO_APPROVE=1` is set in the server's environment.
`dontAsk` additionally requires the on-PC startup confirmation
click unless `PC_BRIDGE_FULL_ACCESS_CONFIRM=0` is set. Restart
without the variables to restore dialogs.

## Jobs are accepted but never execute

An `ESTOP` sentinel is engaged: look for the file
`%APPDATA%\pc-mcp-bridge\ESTOP` next to `jobs.db` and delete it to
resume. `doctor` reports ESTOP state, and `job_status` keeps
working while it's engaged — submissions are journaled, just not
dispatched.

## Browser tools can't reach Edge

`browser_*` needs Edge running with a debug port, and two flags are
load-bearing (see [tools.md](tools.md#browser--drive-a-live-edge-tab-via-cdp)):
a **dedicated `--user-data-dir`** (otherwise the relaunch reuses
the existing process and nothing listens on 9222) and
**`--remote-allow-origins=*`** (otherwise Edge 154 answers the
WebSocket with 403). Verify something is listening:
`Get-NetTCPConnection -LocalPort 9222 -State Listen`.

## Reading the evidence

- **Server console** — pairing codes, posture banners, lockout
  notices, WS chat connections.
- **Event log** — `%APPDATA%\pc-mcp-bridge\events\` (JSONL per UTC
  day). Query from the operator side with `query_events`, or replay
  a whole session with `replay_session`.
- **`doctor`** — run the tool: it checks Python version, the 8765
  listener, Defender exclusions, the Startup entry, tunnel config,
  disk space, and the event log, and attaches one concrete fix to
  anything that isn't ok.
- **Watchdog log** — `%APPDATA%\pc-mcp-bridge\watchdog.log` shows
  what the 5-minute watchdog found and restarted.
