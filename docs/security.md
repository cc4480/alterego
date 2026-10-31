# Security model

AlterEgo gives a remote agent real hands on a real PC, so the security
model is the product. This page describes the mechanisms as the code
implements them (`pc-agent/server.py`, `pairing.py`, `approval.py`,
`tool_profiles.py`, `toolcall.py`, `hooks.py`, `command_rules.py`,
`jobs.py`).

## The trust boundary

- The server binds **`127.0.0.1:8765` only**. It is unreachable from
  the LAN. The sole inbound path is the Cloudflare tunnel the PC
  owner starts themselves, dialing *out* from the PC.
- The tunnel URL is an **address, not a credential**. Everything
  except `GET /health`, `POST /pair`, and `GET /chat` requires a
  session bearer token; the auth middleware compares presented tokens
  against the persisted list with `secrets.compare_digest`
  (constant-time) on **every request**.
- The MCP SDK's DNS-rebinding (Host header) protection is disabled
  deliberately: the tunnel hostname can't be allowlisted, the server
  is loopback-only, and the bearer token is the real authentication.

## Pairing — how a token is born

- On startup the server prints a **6-digit pairing code**
  (`secrets.randbelow`, zero-padded). Codes are **single-use** and
  expire after **30 minutes**.
- **Brute-force lockout:** 5 wrong guesses lock pairing. A locked
  pairing does **not** auto-refresh — that would hand an attacker
  unlimited guesses. The owner re-arms by deleting
  `%APPDATA%\pc-mcp-bridge\pairing.lock` or restarting the server.
  A background thread mints a fresh code only when the current one is
  consumed or expires *without* lockout.
- `POST /pair` with a valid code returns a fresh session token
  (`secrets.token_hex(32)` — 64 hex chars) over the TLS tunnel.
- **Optional defense in depth:** `PAIR_IP_ALLOWLIST` restricts
  `/pair` to comma-separated IPs/CIDRs, matched against the
  Cloudflare `CF-Connecting-IP` header. Unset = no restriction.

## Session tokens — storage and revocation

- Tokens persist on the PC at `%APPDATA%\pc-mcp-bridge\session_token`
  (a JSON list; the file is chmod `0600` where the OS allows, and
  lives under the owner's own `%APPDATA%` ACLs regardless).
- **Pairing is additive.** Each pairing appends a token; existing
  tokens stay valid, so multiple clients can be paired at once.
  Re-pairing does *not* rotate anyone out.
- The file is re-read on every request, which makes revocation
  immediate and total:
  - delete `session_token` → **all** sessions revoked, even
    mid-flight;
  - `POST /logout` (any valid bearer) → all sessions revoked;
  - `POST /revoke` → just the presented token is removed.
- On the operator side, the token is stored at
  `~/.config/pc-bridge/token` (mode `0600`) by `bridge/token_store.py`,
  or supplied via `PC_BRIDGE_TOKEN`. It never appears in the audit
  log: events identify sessions by a SHA-256 **fingerprint**
  (`sess_` + 12 hex chars) only.

## Approval — the human gate

- Approval is requested **inside each tool's implementation**, before
  it acts, with a summary of the exact action (paths, command text,
  window titles). The dialog is a native Win32 Yes/No, system-modal.
- **Explicit Yes is the only pass.** Timeout (30 s), a dialog error,
  or a non-interactive session all fail closed; the operator receives
  `{"error": "denied by local approval (or timed out)"}`.
- Tiers come from each tool's **risk profile**
  (blast radius / recoverability / side effects / compensating
  action / tags). Destructive-tagged tools (`shell_exec`,
  `shell_pwsh`, `kill_process`, `write_file`, `delete_file`,
  `browser_eval`, `power`, `batch`, plus profile-less
  `exec_background`) are `always_ask`: they show a DESTRUCTIVE-titled
  dialog **even in auto-approve mode**. Unknown tools default to the
  strictest profile.

### Permission postures (`PC_BRIDGE_PERMISSION_MODE`)

| Posture | Effect |
|---|---|
| `default` | Reads silent; writes ask; destructive always asks |
| `plan` | Write calls never execute: file tools run with `dry_run=True` (real diffs), all other writes return a would-execute stub |
| `acceptEdits` | `write_file`, `edit_file`, `create_dir`, `copy_file`, `move_file` auto-approved; shell + destructive still ask |
| `dontAsk` | **All dialogs off**, including destructive |

Related switches:

- `PC_BRIDGE_AUTO_APPROVE=1` — skips routine/ask dialogs only;
  `always_ask` tools still ask. For the owner's own testing.
- `PC_BRIDGE_FULL_ACCESS=1` — legacy spelling of `dontAsk`.
- **The `dontAsk` startup gate:** starting in `dontAsk` requires the
  owner to click Yes on an on-PC confirmation dialog; declining (or
  a 30 s timeout) falls back to `default`. Trusted automated
  startups can opt out with `PC_BRIDGE_FULL_ACCESS_CONFIRM=0`.
  The server prints a loud banner in every dialog-off mode.

In **every** posture: authentication is still required, and the
event log records every call with its tier and outcome.

## Owner-side policy: hooks and command rules

- **Hooks** (`pc-agent/hooks.py`, guide in
  [../pc-agent/HOOKS.md](../pc-agent/HOOKS.md)): PreToolUse hooks run
  before execution and can allow, **deny with a reason** (shown to
  the operator and logged), or rewrite arguments. PostToolUse hooks
  can observe or replace results. Hooks time out at 5 s and fail
  safe: a PreToolUse timeout or hook-infrastructure error **denies**
  the call.
- **Command rules** (`command_rules.json` in the data dir):
  fnmatch allow/deny patterns matched case-insensitively against
  `shell_exec` / `shell_pwsh` commands, deny first. Example uses:
  block `format *`, allow only `git *`. With no rules file,
  everything is allowed (the approval dialog remains the gate).
  Format: [configuration.md](configuration.md#command_rulesjson).

## Scope limits baked into tools

- **Read tools are profile-bound:** paths resolve under
  `%USERPROFILE%` (override: `PC_BRIDGE_PROFILE_ROOT`); anything
  outside — including system directories and other profiles — is
  denied before any dialog. Optional `cwd` for shell tools is held
  to the same root.
- **File write tools are not profile-bound** — by design, since the
  approval dialog shows the full absolute path and the owner decides.
- `kill_process` refuses the bridge's own PID.
- `web_fetch` is **SSRF-guarded**: public IPs only, and every
  redirect hop is re-checked before following.
- The Chrome `cdp_*` hand refuses the user's real Chrome profile and
  works only on its prepared presentation profile.

## The audit trail

- Every call appends typed events (`ToolCalled`,
  `ApprovalRequested`, `ToolCompleted` / `ToolDenied` / `ToolFailed`,
  plus per-hook `HookEvaluated`) to
  `%APPDATA%\pc-mcp-bridge\events\` (JSONL, one file per UTC day).
- Arguments and results are **redacted** before writing and large
  values spill to a blob store by reference. Each entry carries the
  tool's risk snapshot and the session fingerprint — enough to
  replay a session (`replay_session`) and answer "what exactly did
  the agent do, and who approved it".
- `EVENT_LOG_ENABLED=0` disables event writes (emergency rollback
  only — you are turning off your own evidence).

## Emergency stop

Drop a file named **`ESTOP`** into `%APPDATA%\pc-mcp-bridge\`
(next to `jobs.db`). While it exists:

- `submit_job` still accepts and journals submissions, but **no new
  job starts**;
- jobs already running are **never killed** by ESTOP;
- synchronous `call` traffic is unaffected — ESTOP governs the job
  layer. (For everything-at-once, stop the server or delete
  `session_token`.)

Delete the file to resume. `doctor` reports ESTOP state.

## What this model does *not* do

- It does not sandbox the agent. An approved `shell_exec` is a real
  shell, running as the PC's user. The dialog, the postures, the
  hooks, and the log are the controls — approve like it matters.
- It does not protect a PC whose owner runs permanently in `dontAsk`
  with a leaked token. Treat the token like a key to the machine:
  revoke first (`session_token` delete), ask questions later.
