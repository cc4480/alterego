# Architecture

How AlterEgo is put together, end to end. Everything here describes the
code as it ships in this repo (`pc-agent/` + `bridge/`).

## The shape of the system

```
┌────────────────────┐        ┌──────────────────────┐        ┌─────────────────────────┐
│  Operator side     │        │  Cloudflare tunnel   │        │  Windows PC             │
│                    │        │                      │        │                         │
│  Muse agent        │  TLS   │  named tunnel        │  local │  pc-agent/server.py     │
│  (Cho-zen1)        ├───────►│  `pc-bridge`         ├───────►│  MCP server (Streamable │
│                    │ bearer │  pc.secscan.info     │  http  │  HTTP) 127.0.0.1:8765   │
│  bridge/op_call.py │ token  │  (PC dials OUT)      │        │                         │
└────────────────────┘        └──────────────────────┘        └─────────────────────────┘
```

Three facts drive every design decision:

1. **The PC only dials out.** The server binds `127.0.0.1:8765` and is
   unreachable from the LAN. The named Cloudflare tunnel is started by
   the PC owner and connects outward; it is the only inbound path.
2. **The human is on the PC side.** Approval dialogs render on the PC's
   interactive desktop. A call that needs approval blocks until the
   person at the PC answers (or 30 s pass — a deny).
3. **Everything is recorded.** Each tool call flows through one pipeline
   that appends typed events to an event-sourced log before, during, and
   after execution.

## The PC half (`pc-agent/`)

### `server.py` — the entry point

- Builds an MCP server (`mcp.server.mcpserver.MCPServer`, the `mcp` 2.x
  API) named `pc-bridge` and serves it as **Streamable HTTP** via
  uvicorn on `127.0.0.1:8765`.
- Registers the 63 wrapper tools from `tool_wrappers.ALL_TOOLS`, plus
  four job-layer ops defined inline (`submit_job`, `job_status`,
  `job_result`, `job_cancel`) — **67 tools** on the MCP surface.
- Adds plain HTTP routes beside the MCP endpoint:
  `GET /health` (unauthenticated liveness), `GET /chat` (the chat UI),
  `POST /pair`, `POST /logout`, `POST /revoke`, and the WebSocket relay
  at `/ws`. A bearer-auth middleware guards everything except
  `/health`, `/pair`, and `/chat`.
- On startup it prints the pairing code, starts a pairing-code refresher
  thread and a 60-second heartbeat writer (the watchdog uses the
  heartbeat file to tell a live server from a hung one), and — if the
  permission posture is `dontAsk` — demands an on-PC confirmation click
  before dialogs are disabled (see [security.md](security.md)).
- The MCP SDK's DNS-rebinding (Host header) protection is disabled on
  purpose: the tunnel hostname can't be allowlisted, the server is
  loopback-only, and the bearer token — not the Host header — is the
  authentication.

### Wrappers, pipeline, providers — the "seams"

A tool call crosses three layers, each with one job:

```
MCP request
   │
   ▼
tool_wrappers*.py      public signatures + docstrings (the MCP schema).
   │                   No implementation logic lives here.
   ▼
toolcall.call()        THE PIPELINE: resolve → risk snapshot →
   │                   PreToolUse hooks → plan-mode short-circuit →
   │                   execute → PostToolUse hooks → typed events
   │                   (ToolCalled / ApprovalRequested / ToolCompleted /
   │                    ToolDenied / ToolFailed).
   ▼
seams.registry         maps the tool name to the active provider's
   │                   implementation (PC_BRIDGE_PROVIDER: windows | mock).
   ▼
seams.providers.*      the implementations, grouped: read, write, files,
                       pc, browser, recon, support, tasks, misc.
```

- **Definitions** (`pc-agent/seams/definitions/`) declare every tool the
  system knows — **73 definitions** across the nine groups, each with a
  name, group, doc string, and argument schema.
- **Providers** implement definitions per platform. The `windows`
  provider is the real one; the `mock` provider is an in-memory fake
  (virtual filesystem, processes, windows) that the whole test suite
  runs against — no PC needed. Provider selection is lazy, cached for
  the life of the process, and fails loud: an unknown provider name or
  an unimplemented tool is a `RuntimeError`, never a silent degrade.
- The one naming quirk: the MCP surface registers `arbitrate_tool`,
  while the pipeline, risk profiles, and audit log know it as
  `arbitrate` (the registry translates).

### Where approval actually happens

The pipeline does *not* show dialogs. Each Windows provider
implementation calls an internal `_approved(tool, summary)` gate before
acting, which calls `approval.request_approval()` with the tool's tier
from its risk profile (`pc-agent/tool_profiles.py`). The dialog is a
native Win32 `MessageBoxW` (Yes/No, system-modal): explicit **Yes** is
the only path through; timeout, dialog error, or a non-interactive
session all fail closed as a `PermissionError`, which the pipeline
turns into a `{"error": ...}` tool result — a denial never crashes the
server. The pipeline separately *predicts* the gate outcome to record
an `ApprovalRequested` event.

### Risk profiles (`tool_profiles.py`)

Every tool declares: **blast radius** (`none | session | user_data |
system | external`), **recoverability** (0–10), **side effects**, a
**compensating action** (how a human undoes it), and **sensitivity
tags**. The profile maps to an approval tier:

| Tier | Rule of thumb | Dialog behavior |
|---|---|---|
| `silent` | blast radius `none` | provider never asks |
| `routine` | session-scope, recoverability ≥ 8 | asks (skipped in auto-approve) |
| `ask` | user-data writes | asks (skipped in auto-approve) |
| `always_ask` | destructive tag, or recoverability ≤ 3 | **always asks**, DESTRUCTIVE title, even in auto-approve |

Tools with no profile entry get the most restrictive default
(`system`, recoverability 0, `always_ask`). Every event-log entry
carries the tool's risk snapshot, and the `arbitrate_tool` tool scores
candidate tool *sequences* with these profiles before an agent commits
to a plan (weights in `pc-agent/arbitrate.py`).

### The job layer (`jobs.py`)

Synchronous calls are a bad fit for long work on a flaky tunnel, so the
server also runs a journal-first job system (Hermes-pattern):

- `submit_job` **journals the call first** (SQLite, `jobs.db` in the
  bridge data dir) and returns a `job_id` immediately. Execution
  happens later on a worker thread, through the *same* wrapper a
  synchronous call would use — same approvals, same events.
- A pool of up to **8 workers** dispatches queued jobs. Terminal states:
  `succeeded`, `failed`, `killed`, `cancelled`.
- **Idempotency:** resubmitting with the same idempotency key returns
  the existing job and never executes twice. A dropped connection loses
  nothing — the operator polls `job_status` / `job_result` (large
  results ride the blob store) or re-attaches with `op_call.py resume`.
- **Lineage:** each submission records where it came from (`typed`,
  `relay`, `scheduled`, `agent`); anything else is journaled as
  unattributed.
- **ESTOP:** while a file named `ESTOP` sits next to `jobs.db`,
  submissions are still accepted and journaled, but no new job starts.
  Jobs already running are never killed by ESTOP.
- A watchdog supervises *progress* (output growth / heartbeat), not
  wall-clock deadlines.

### The event log (`events.py`)

The primary operation record — an append-only, event-sourced log:

- Location: `%APPDATA%\pc-mcp-bridge\events\`, one JSONL file per UTC
  day, envelope version 1. Large values spill to a blob store
  (`blob_store.py`); secrets are redacted before anything is written.
- Sessions are identified by a **token fingerprint**
  (`sess_` + first 12 hex of the token's SHA-256) — the token itself
  never appears in the log.
- Stage events (`ToolCalled`, `HookEvaluated`, `ApprovalRequested`,
  `ToolCompleted`, …) are chained by `caused_by`, so `replay_session`
  can reconstruct the full causal transcript of an operator session
  and `query_events` can search across days.
- `EVENT_LOG_ENABLED=0` is the emergency rollback: it skips all event
  writes. Event-write failures themselves never break a tool call.

### Hooks and command rules

- **Hooks** (`hooks.py`, documented in
  [../pc-agent/HOOKS.md](../pc-agent/HOOKS.md)): PreToolUse hooks can
  allow, deny (with a reason the operator sees), or rewrite a call's
  arguments; PostToolUse hooks can observe or replace results. Hooks
  have a 5-second timeout and fail safe (a PreToolUse timeout denies).
- **Command rules** (`command_rules.py`): an owner-maintained
  allow/deny pattern list for `shell_exec` / `shell_pwsh`, enforced as
  a PreToolUse hook. See [configuration.md](configuration.md).

### Browser control — two hands

- **`browser_*` (Edge, on the MCP surface):** attaches over CDP to a
  live Edge started with `--remote-debugging-port=9222` and a dedicated
  `--user-data-dir`; snapshot/click/fill recurse through open shadow
  roots and same-origin iframes.
- **`cdp_*` (Chrome session hand, seams layer):** a deterministic
  presentation hand that *owns* a Chrome session across calls —
  per-target WebSockets (never flatten-mode attach), a background
  reader routing responses by id, timeouts that fail loudly, and
  readback-verified actions (navigation proven by load event + URL/
  title readback, typing by value-readback equality, graceful
  `Browser.close`). It launches Chrome on a prepared presentation
  profile under the agent data area
  (`%LocalAppData%\AlterEgo\cdp-profiles\presentation`); the user's
  real Chrome profile is refused. **Status at this commit:** the ten
  `cdp_*` ops are implemented in definitions, both providers, and risk
  profiles, with a fake-socket unit suite and an on-PC smoke script
  (`pc-agent/smoke_chrome_cdp.py`, not yet run on a PC) — but they are
  **not yet registered on the MCP surface**, so they do not appear in
  `tools/list`. The catalog in [tools.md](tools.md) marks them
  accordingly.

### Chat (`/chat`, `/ws`)

The server hosts a small WebSocket relay for the humans and agents
around the PC: clients connect as `cosmo`, `cho-zen1`, or `judith`
(remote connections need a valid bearer token; localhost is trusted),
and messages route by name or broadcast. The browser UI is served at
`GET /chat` (`pc-agent/chat.html`).

## The operator half (`bridge/`)

- **`op_call.py`** — the durable client: Python **stdlib only**, so it
  runs anywhere. It speaks the Streamable HTTP handshake itself
  (`initialize` → session header → `tools/call`), retries transient
  transport failures with backoff (never HTTP errors — a 401 clears
  the saved token), and implements the job-mode commands
  (`submit` / `calljob` / `job` / `job-result` / `job-cancel` /
  `resume`) on top of the server-side journal.
- **`token_store.py`** — persists the session token at
  `~/.config/pc-bridge/token` with mode `0600`. `PC_BRIDGE_TOKEN` in
  the environment overrides the file.
- **`pc_bridge.py`** — the older minimal CLI (kept working; new work
  should use `op_call.py`).
- **`mock_mcp_server.py`** — a stdlib-only fake PC serving the real
  tool schemas (`bridge/tools.json`) with canned responses, for
  client development with no PC and no tunnel.

## Data on the PC

Everything the agent keeps lives under `%APPDATA%\pc-mcp-bridge\`:

| Path | What |
|---|---|
| `session_token` | persisted session tokens (JSON list) — delete to revoke all |
| `events\` | the event-sourced operation log (JSONL per day) |
| `blobs\` | content-addressed blob store for oversized event values |
| `jobs.db` | the job journal (SQLite) |
| `ESTOP` | emergency-stop sentinel (presence = engaged) |
| `heartbeat.txt` | server liveness timestamp (60 s cadence) |
| `pairing.lock` | written on pairing lockout; delete to re-arm |
| `command_rules.json` | owner's shell allow/deny patterns (optional) |
| `memory\` | the on-PC memory archive searched by `memory_recall` |

See [configuration.md](configuration.md) for the environment variables
that relocate or reshape these.
