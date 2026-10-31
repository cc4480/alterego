# Tool reference

The catalog below is the MCP surface exactly as the server registers
it: **67 tools** — 63 wrapper tools (`pc-agent/tool_wrappers*.py`,
registered from `ALL_TOOLS`) plus 4 job-layer ops registered by
`server.py`. All are exposed over MCP (Streamable HTTP) at
`POST {tunnel-url}/mcp`, authenticated with the session bearer token
from `/pair`.

## Approval behavior

Every tool carries a machine-readable risk profile
(`pc-agent/tool_profiles.py`: blast radius, recoverability, side
effects, compensating action, sensitivity tags), which maps to an
approval tier. The dialog itself is shown by the tool's implementation
on the PC — a native Windows Yes/No, 30 s timeout = deny, fail closed
when there's no interactive session.

| Mark in this catalog | Tier | Meaning |
|---|---|---|
| — | `silent` | No dialog, ever |
| **Dialog** | `routine` / `ask` | Yes/No dialog; skipped in auto-approve and `dontAsk` postures |
| **DESTRUCTIVE** | `always_ask` | DESTRUCTIVE-titled dialog — **always**, even in auto-approve; only `dontAsk` silences it |

The `always_ask` set on the MCP surface is: `shell_exec`,
`shell_pwsh`, `kill_process`, `write_file`, `delete_file`,
`browser_eval`, `power`, `batch` — plus `exec_background`, which has
no profile entry and so falls back to the strictest default tier.
Tools with no profile at all fail closed the same way. Every call is
written to the audit log either way, with its risk snapshot.

Two postures change the picture globally: in `plan`, write tools
never execute (file tools return dry-run plans/diffs; others return a
would-execute stub); in `acceptEdits`, the file-edit tools
(`write_file`, `edit_file`, `create_dir`, `copy_file`, `move_file`)
are auto-approved while shell and destructive tools still ask. See
[security.md](security.md).

---

## Read — no approval (profile-restricted where noted)

| Tool | Args | Returns / notes |
|---|---|---|
| `screenshot` | `region` (optional), `scale` (default 1.0) | Primary monitor as PNG (base64) |
| `list_windows` | — | Visible windows: `hwnd`, pid, title |
| `system_info` | — | OS, hostname, username, CPU / memory |
| `list_dir` | `path` (default: profile root) | Directory items (name, dir, size). **Profile-restricted** |
| `read_file` | `path` | UTF-8 text file, max 1 MB. **Profile-restricted** |
| `read_file_range` | `path`, `start_line`, `end_line` (0 → start+50) | 1-indexed line range. **Profile-restricted** |
| `search_files` | `pattern` (regex), `path`, `file_pattern`, `max_results`, `context`, `case_insensitive`, `output_mode`, `offset` | Content search under a directory. **Profile-restricted** |
| `search_filenames` | `pattern` (glob), `path`, `max_results`, `sort_by`, `offset` | Find files by name. **Profile-restricted** |
| `clipboard_get` | — | Current Windows clipboard text |
| `uia_find` | `window`, `name_rx`, `control_type`, `max_results` | UI elements via Windows UI Automation — name, control type, bounding rect; no screenshot needed |

**Profile restriction:** the read tools resolve paths under the
user's own profile (`%USERPROFILE%`; override with
`PC_BRIDGE_PROFILE_ROOT`) and deny everything outside it, including
Windows system directories and other users' profiles.

## Write / input — each pops the approval dialog on the PC

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `focus_window` | `hwnd` | Dialog | Bring a window to the foreground |
| `close_window` | `hwnd` | Dialog | Gracefully close a window (like clicking X) |
| `minimize_window` | `hwnd` | Dialog | Minimize a window |
| `maximize_window` | `hwnd` | Dialog | Maximize a window |
| `type_text` | `text` | Dialog | Type text into the focused window |
| `hotkey` | `keys` (e.g. `"ctrl+c"`, `"alt+f4"`, `"enter"`) | Dialog | Press a key combo |
| `mouse_move` | `x`, `y` | Dialog | Move the cursor to screen coordinates |
| `mouse_click` | `x`, `y`, `button` (`left`/`right`/`middle`) | Dialog | Click at screen coordinates |
| `mouse_scroll` | `direction` (`up`/`down`), `clicks` | Dialog | Scroll the wheel under the cursor |
| `uia_click` | `window`, `name_rx`, `control_type`, `index`, `button` | Dialog | Click a UI element by name via UI Automation |
| `uia_set_text` | `window`, `name_rx`, `text`, `control_type`, `index` | Dialog | Set a UI element's text via UI Automation |
| `clipboard_set` | `text` | Dialog | Put text on the Windows clipboard |
| `paste_text` | `text` | Dialog | Paste into the focused window (reliable for long text) |
| `shell_exec` | `command`, `timeout_s` (default 60), `cwd` (profile-restricted) | **DESTRUCTIVE** | Run a command in `cmd.exe` |
| `kill_process` | `pid` | **DESTRUCTIVE** | Terminate a process by PID (refuses the bridge's own PID) |

## Files — absolute paths anywhere on the machine

Unlike the read tools, the file tools are **not** profile-restricted —
the approval dialog showing the full path is the guardrail. Text
files (UTF-8); writes are byte-exact with no newline translation, so
write → read round-trips cleanly. `dry_run=True` computes the full
plan — including a unified diff for overwrites — with no dialog and
zero side effects. `expected_sha256` on `write_file` / `edit_file`
fails the write if the file changed since it was read.

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `write_file` | `path`, `content`, `dry_run`, `expected_sha256` | **DESTRUCTIVE** (unless `dry_run`) | Create/overwrite a text file |
| `edit_file` | `path`, `old_text`, `new_text`, `dry_run`, `require_unique`, `read_before`, `expected_sha256` | Dialog (unless `dry_run`) | Replace text; fails on multiple matches unless `require_unique=False`; always returns a diff preview |
| `delete_file` | `path`, `dry_run` | **DESTRUCTIVE** (unless `dry_run`) | Permanently delete a file |
| `create_dir` | `path`, `dry_run` | Dialog (unless `dry_run`) | Create a directory (parents too) |
| `copy_file` | `src`, `dst`, `dry_run` | Dialog (unless `dry_run`) | Copy a file, metadata preserved |
| `move_file` | `src`, `dst`, `dry_run` | Dialog (unless `dry_run`) | Move/rename a file |
| `file_info` | `path` | — | Size and timestamps for a file or directory |

## PC awareness & control

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `active_window` | — | — | Foreground window: hwnd, title, pid, process |
| `idle_seconds` | — | — | Seconds since last keyboard/mouse input |
| `list_processes` | — | — | Running processes (pid + exe) |
| `notify` | `title`, `message`, `timeout_s` | Dialog | Windows tray balloon notification |
| `speak` | `text` | Dialog | Speak text aloud through the PC speakers |
| `set_volume` | `level` (0–100) | — † | Set master volume |
| `power` | `action`: `lock` / `sleep` / `restart` / `shutdown` | **DESTRUCTIVE** | Lock, sleep, restart, or shut down the PC |

† `set_volume`'s wrapper passes the write flag (so `plan` posture
stubs it), but the current Windows implementation sets the volume
without showing a dialog. Same for `exec_cancel` below: it kills the
background job's process tree directly. The audit log records both.

## Browser — drive a live Edge tab via CDP

Edge must be launched with remote debugging enabled. Proven working
flags (verified 2026-10-04 against Edge 154):

```bat
msedge.exe --remote-debugging-port=9222 --user-data-dir=C:\edge-cdp --remote-allow-origins=*
```

Notes from live testing — `--remote-debugging-port` alone is not
enough:

- Without a **separate `--user-data-dir`**, a relaunch reuses the
  existing browser process and the flag is silently ignored (nothing
  listens on 9222).
- Without **`--remote-allow-origins=*`**, Edge 154 rejects the
  bridge's WebSocket with `403 Forbidden`.
- Requires `websocket-client` on the PC (in `requirements.txt`).

The bridge attaches to a live tab and sees DOM that UI Automation
cannot reach. `browser_snapshot`, `browser_click`, and `browser_fill`
recurse through **open shadow roots** and **same-origin iframes**;
snapshot tags such elements `[shadow]` / `[iframe]` and reports
`cross_origin_iframes` (iframes that could not be pierced).

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `browser_snapshot` | `url_contains` (tab filter) | — | List interactive elements of the live Edge tab |
| `browser_navigate` | `url`, `url_contains` | Dialog | Navigate the tab to a URL |
| `browser_click` | `text`, `url_contains` | Dialog | Click the element containing `text` |
| `browser_fill` | `label`, `text`, `url_contains` | Dialog | Fill the field matching `label` |
| `browser_eval` | `js`, `url_contains` | **DESTRUCTIVE** | Run JavaScript in the tab |

### Chrome CDP session hand (`cdp_*`) — implemented, not yet on the MCP surface

The deterministic presentation hand: where `browser_*` attaches
opportunistically to Edge for one-shot DOM access, `cdp_*` owns a
**visible Chrome session across calls** — per-target WebSockets
(never flatten-mode attach), a background reader routing responses by
id, timeouts that fail loudly, and readback-verified actions
(navigation proven by load event + readback; typing by value-readback
equality; graceful `Browser.close`). It launches Chrome on a prepared
presentation profile (`%LocalAppData%\AlterEgo\cdp-profiles\presentation`);
the user's real Chrome profile is refused.

**Status at this commit:** the ten ops below are implemented in the
seams layer (definitions, Windows + mock providers, risk profiles)
with a fake-socket unit suite (`pc-agent/test_chrome_cdp.py`) and an
on-PC smoke script (`pc-agent/smoke_chrome_cdp.py`, **not yet run on
a PC**) — but they are **not registered in `tools/list`** yet. The
table describes the implemented seams behavior, including the
approval gate each op will pass through.

| Tool | Args | Approval (seams) | What it does |
|---|---|---|---|
| `cdp_launch` | `profile_dir`, `debug_port`, `start_url` (optional) | Dialog | Launch Chrome on the presentation profile and attach |
| `cdp_connect` | `debug_port` (optional) | Dialog | Attach to a Chrome already serving a debug port |
| `cdp_targets` | `target_id` (optional) | — | List page targets; switch active target when given |
| `cdp_navigate` | `url`, `timeout_s` (default 30) | Dialog | Navigate and prove the landing (url + title) |
| `cdp_snapshot` | — | — | Interactive elements with stable CSS selectors |
| `cdp_click` | `selector` or `text` | Dialog | Click by identity; returns the landing readback |
| `cdp_type` | `text`, `selector` or `label`, `clear_first` | Dialog | Type via `Input.insertText`; readback must equal the typed text |
| `cdp_evaluate` | `js` | **DESTRUCTIVE** | Run JavaScript in the tab |
| `cdp_readback` | — | — | URL, title, ready state, active element |
| `cdp_close` | `delete_profile` (optional) | Dialog | Graceful `Browser.close` (launched) or detach (attached) |

## Recon & web — network reads

Read-only in effect, but they touch the network (risk-profile blast
radius `external`). `web_fetch` / `web_search` additionally ask for
approval in the `default` posture (a routine-tier dialog, skipped in
auto-approve / `dontAsk`).

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `http_headers` | `url`, `timeout_s` (default 20) | — | GET a URL; return status + all response headers |
| `dns_query` | `domain`, `rtype` (A/AAAA/CNAME/MX/NS/TXT/DNSKEY/SOA) | — | Raw DNS over UDP |
| `tls_info` | `host`, `port` (default 443), `timeout_s` (default 15) | — | TLS version, cipher, cert subject/issuer/expiry |
| `tcp_check` | `host`, `ports`, `timeout_s` (default 3) | — | TCP connect check: open / closed / filtered per port |
| `web_fetch` | `url`, `max_bytes` (default 100000), `timeout_s` (default 15) | Dialog* | Fetch a URL and return readable text. **SSRF-guarded**: public IPs only, every redirect hop re-checked |
| `web_search` | `query`, `max_results` (default 10), `backend` (`auto`) | Dialog* | Web search without an API key (DuckDuckGo/Bing scraping; optional self-hosted SearXNG via `SEARXNG_URL`) |

## Support — shell power tools

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `shell_pwsh` | `script` (1–8000 chars), `timeout_s` (default 60), `cwd` (profile-restricted) | **DESTRUCTIVE** | Run PowerShell directly — no `cmd.exe` wrapping |
| `batch` | `calls` (array of `{tool, args}`, max 20) | **DESTRUCTIVE** | Run up to 20 calls in one roundtrip; **one** dialog covers all writes (sub-call gates are muted for the duration); no nesting |
| `exec_background` | `command`, `timeout_s` (default 300), `cwd` (profile-restricted) | **DESTRUCTIVE** | Start a long-running shell command in the background; returns a job id |
| `exec_status` | `job_id`, `offset` (bytes) | — | Poll a background job; `offset` enables incremental output reads |
| `exec_cancel` | `job_id` | — † | Kill a running background job's whole process tree |

## Tasks & arbitration — local bookkeeping, no approval

Task records are append-only and HMAC-signed with a machine-local
key; each checkpoint hash-chains to the previous one, so tampering is
detected on read.

| Tool | Args | What it does |
|---|---|---|
| `task_create` | `goal`, `plan` (list of steps) | Create a signed task record; returns `task_id` |
| `task_checkpoint` | `task_id`, `step`, `result`, `done` | Append a signed checkpoint |
| `task_status` | `task_id` (optional) | Verify the hash chain and return status; without an id, list all tasks |
| `arbitrate_tool` | `trajectories` (list of tool-name lists) | Score/rank candidate tool sequences by risk before executing them |

`arbitrate_tool` (pipeline name: `arbitrate`) scores a trajectory
with the risk profiles: `score = 0.25·(1−blast) + 0.35·recoverability
+ 0.15·(1−side_effects) + 0.25·optionality − 0.10·human_cost`;
tiers: `> 0.8` → `auto`, `0.5–0.8` → `confirm`, `< 0.5` →
`explicit_intent`.

## Misc — memory, health, audit

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `memory_recall` | `query`, `limit` (default 5) | — | Keyword search over the on-PC memory archive (transcripts + subject index) at `%APPDATA%\pc-mcp-bridge\memory\` |
| `doctor` | — | — | Bridge health check: Python version, port 8765 listener, Defender exclusions, Startup entry, tunnel config, disk space, event log. Each check returns ok/warning/fail/unknown + a specific fix command |
| `query_events` | `tool`, `type`, `since` (`5m`/`1h`/`24h`/`7d`/`30d` or ISO; default `7d`), `session_id`, `limit` (default 50) | — | Search the event-sourced operation log |
| `replay_session` | `session_id`, `format` (`human`) | — | Full causal transcript of one operator session |

## Job layer — registered by `server.py`

These four ops are not wrappers; they are registered directly by the
server and execute through the job journal (SQLite,
`%APPDATA%\pc-mcp-bridge\jobs.db`). A submitted tool runs through the
very same wrapper a synchronous call would use — same approval
dialogs, same audit events — on a worker thread (pool of 8), whether
or not the caller stays connected.

| Tool | Args | What it does |
|---|---|---|
| `submit_job` | `tool`, `args`, `idempotency_key`, `lineage` (`typed`/`relay`/`scheduled`/`agent`) | Journal a tool call and return `{job_id, state}` immediately. Same idempotency key ⇒ same job, never executed twice |
| `job_status` | `job_id` | Job state + started/finished receipts from the journal |
| `job_result` | `job_id` | The job's result once terminal (`succeeded`/`failed`/`killed`/`cancelled`) |
| `job_cancel` | `job_id` | Cancel a queued or running job; a running job's process tree is fenced (only that job's processes are killed) |

While an `ESTOP` sentinel file sits in the data dir, submissions are
accepted and journaled but no new job executes. See
[architecture.md](architecture.md#the-job-layer-jobs.py).

---

## Examples (via `bridge/op_call.py`)

```bash
export PC_BRIDGE_URL=https://pc.secscan.info
python3 bridge/op_call.py pair 279416        # one-time; token is saved

python3 bridge/op_call.py tools
python3 bridge/op_call.py call system_info '{}'
python3 bridge/op_call.py call list_dir '{"path": "C:\\Users\\<you>\\Downloads"}'
python3 bridge/op_call.py call list_windows '{}'
python3 bridge/op_call.py call screenshot '{}'   # -> /tmp/shot-full.json
# write tools — expect a Yes/No dialog on the PC:
python3 bridge/op_call.py call shell_exec '{"command": "whoami"}'
```

Tool errors come back as `{"error": "..."}` payloads (including
operator-denied approvals) — they never crash the server.
