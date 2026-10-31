# Configuration

Every knob the code actually reads, in one place. Server variables
are read **at process start** (several are captured at import time) —
restart the server after changing them.

## Server (`pc-agent/`, on the Windows PC)

| Variable | Values / default | What it does |
|---|---|---|
| `PC_BRIDGE_PERMISSION_MODE` | `default` (unset) · `plan` · `acceptEdits` · `dontAsk` | The approval posture. See [security.md](security.md#permission-postures-pc_bridge_permission_mode) |
| `PC_BRIDGE_AUTO_APPROVE` | `1` | Skips routine/ask dialogs; `always_ask` (destructive) tools still ask |
| `PC_BRIDGE_FULL_ACCESS` | `1` | Legacy spelling of `dontAsk` (used when no explicit mode is set) |
| `PC_BRIDGE_FULL_ACCESS_CONFIRM` | `0` | Skips the on-PC confirmation dialog for a `dontAsk` startup (trusted automated starts only) |
| `PAIR_IP_ALLOWLIST` | comma-separated IPs/CIDRs, unset = open | Restricts `POST /pair` to matching client IPs (via `CF-Connecting-IP`) |
| `PC_BRIDGE_PROVIDER` | `windows` (default) · `mock` | Which seams provider implements the tools. The validator also accepts `linux`, but no Linux provider ships in this repo — selecting it fails loud at first tool resolution |
| `PC_BRIDGE_MOCK_MODE` | `strict` (default) · `lenient` | Mock provider only: `strict` raises for unimplemented tools; `lenient` returns a generic mock result |
| `PC_BRIDGE_EVENTS_DIR` | path | Read-side override: where `query_events` / `replay_session` look for the event log |
| `EVENT_LOG_ENABLED` | `0` disables | Emergency rollback: skip all event-log writes |
| `PC_BRIDGE_MEMORY_DIR` | path | Where `memory_recall` searches (default `%APPDATA%\pc-mcp-bridge\memory\`) |
| `PC_BRIDGE_PROFILE_ROOT` | path | Root the read tools are confined to (default `%USERPROFILE%`) |
| `SEARXNG_URL` | base URL | Enables the SearXNG backend for `web_search` |
| `PC_BRIDGE_VERSION` | string | Version string reported in the `ServerStarted` event when no git checkout is available |
| `APPDATA` | (Windows standard) | Root of the bridge data dir: `%APPDATA%\pc-mcp-bridge\` |

## Operator (`bridge/`, on the controlling machine)

| Variable | Values / default | What it does |
|---|---|---|
| `PC_BRIDGE_URL` | **required** | Base URL of the bridge, e.g. `https://pc.secscan.info`. `op_call.py` refuses to run without it |
| `PC_BRIDGE_TOKEN` | session token | Overrides the saved token file (`~/.config/pc-bridge/token`, mode 0600) |
| `PC_BRIDGE_JOB_TIMEOUT` | seconds, default `3600` | Default poll timeout for `op_call.py calljob` / `resume` |
| `MOCK_TOKEN` | default `secret` | The bearer token the mock server expects (no-PC testing only) |

## Data directory (`%APPDATA%\pc-mcp-bridge\`)

| File | Purpose |
|---|---|
| `session_token` | Persisted session tokens (JSON list). Delete = revoke all sessions, instantly |
| `events\` | Event-sourced operation log — one JSONL file per UTC day |
| `blobs\` | Content-addressed blob store for oversized event values (by SHA-256) |
| `jobs.db` | Job-layer journal (SQLite) |
| `ESTOP` | Emergency-stop sentinel for the job layer — presence = engaged |
| `heartbeat.txt` | Server liveness timestamp, rewritten every 60 s (the watchdog's proof of life) |
| `pairing.lock` | Written when pairing locks after 5 failed attempts. Delete to re-arm |
| `command_rules.json` | Optional shell allow/deny patterns (below) |
| `memory\` | The `memory_recall` archive (transcripts + subject index) |
| `watchdog.log` | Written by `scripts\watchdog.ps1` |

## `command_rules.json`

Owner-side allow/deny rules for `shell_exec` and `shell_pwsh`,
enforced as a PreToolUse hook before the approval dialog:

```json
{"allow": ["git *", "npm test"], "deny": ["rm -rf *", "format *"]}
```

- Patterns use `fnmatch` wildcards, matched case-insensitively
  against the whitespace-normalized command.
- **Deny wins** over allow. If an `allow` list exists, commands not
  matching it are denied; with no file (or empty lists) everything
  is allowed and the approval dialog is the only gate.
- A malformed file is treated as "no rules" — check your JSON if
  rules seem ignored.

## Hooks

Custom PreToolUse / PostToolUse policy is registered in Python —
see [../pc-agent/HOOKS.md](../pc-agent/HOOKS.md). Hooks live in
`pc-agent/hooks.py` or a `hooks_local.py` you keep out of version
control; every hook has a 5-second timeout and fails safe (a
PreToolUse timeout denies the call).

## The tunnel

The named tunnel itself is Cloudflare configuration, not bridge
configuration: `%USERPROFILE%\.cloudflared\config.yml` maps the
`pc-bridge` tunnel to `https://pc.secscan.info`, ingress
`http://127.0.0.1:8765`. The bridge never reads it (except the
`doctor` tool, which checks it exists).
