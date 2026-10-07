# Tool reference

All tools are exposed over MCP (Streamable HTTP) at `POST {tunnel-url}/mcp`,
authenticated with the session bearer token from `/pair`.

## Approval tiers

Every tool carries a machine-readable risk profile (`tool_profiles.py`:
blast radius, recoverability, side effects, compensating action,
sensitivity tags). The profile maps to an approval tier:

| Tier | Meaning | Dialog? |
|---|---|---|
| `silent` | Read-only (blast radius `none`) | Never — no dialog |
| `routine` | Session-scope, reversible writes | Yes, skipped in auto-approve mode |
| `ask` | User-data writes | Yes, skipped in auto-approve mode |
| `always_ask` | Destructive (`power`, `kill_process`, `delete_file`, `shell_exec`, `shell_pwsh`, `browser_eval`, `write_file`, `edit_file`, `batch`) | **Always** — even in auto-approve mode, with a DESTRUCTIVE title |

Unknown tools fail closed to `always_ask`. Every audit-log entry records
the tool's risk snapshot (tier, blast radius, recoverability) plus the
agent's rationale when one is supplied.

## Read tools — no approval needed

| Tool | Args | Returns |
|---|---|---|
| `screenshot` | — | `{"png_base64": ..., "width": N, "height": N}` — primary monitor as PNG |
| `list_windows` | — | visible windows: handle (`hwnd`), pid, title |
| `system_info` | — | OS, hostname, username, CPU / memory |
| `list_dir` | `path` (default: profile root) | `{"path": ..., "items": [{"name", "dir", "size"}]}` |
| `read_file` | `path` (required) | UTF-8 text file, max 1 MB |
| `clipboard_get` | — | Current Windows clipboard text |
| `memory_recall` | `query` (string), `limit` (int, default 5) | Keyword search over the on-PC memory archive (transcripts + subject index), ranked with snippets |

### Support tools (faster task execution)

| Tool | Arguments | Notes |
|---|---|---|
| `shell_pwsh` | `script` (string, 1–8000 chars), `timeout_s` (int, default 60) | Run PowerShell directly — no cmd.exe wrapping or quoting layers. Requires approval. |
| `batch` | `calls` (array of `{tool, args}`, 1–20 items) | Run many tools in one roundtrip. One approval dialog covers every write in the batch; no nesting. |

### Recon tools (passive, read-only, no approval)

| Tool | Arguments | Notes |
|---|---|---|
| `http_headers` | `url` (http/https), `timeout_s` (default 20) | GET a URL, return status + all response headers — verify security headers without a scan credit |
| `dns_query` | `domain`, `rtype` (A/AAAA/CNAME/MX/NS/TXT/DNSKEY/SOA) | Raw DNS over UDP — check TXT verification records, DNSSEC (DNSKEY), mail config |
| `tls_info` | `host`, `port` (default 443) | TLS version, cipher, cert subject/issuer/expiry |
| `tcp_check` | `host`, `ports` (1–50) | TCP connect: open / closed / filtered per port |

### PC awareness & control

| Tool | Notes |
|---|---|
| `active_window` | Foreground window: hwnd, title, pid, process. No approval |
| `idle_seconds` | Seconds since last keyboard/mouse input. No approval |
| `list_processes` | Running processes (pid + exe). No approval |
| `notify` | Windows tray balloon: `title`, `message`, `timeout_s`. Approval |
| `speak` | Text-to-speech through PC speakers (1–500 chars). Approval |
| `set_volume` | Master volume 0–100. Approval |
| `power` | `lock` / `sleep` / `restart` / `shutdown`. Approval |

`list_dir` / `read_file` are restricted to the user's own profile directory
(`C:\Users\<name>\...`). Windows system directories and other users'
profiles are denied.

## Write tools — each pops a native Yes/No dialog on the PC

The dialog shows the exact action. 30 s timeout = deny. Every call is
written to the audit log either way.

| Tool | Args | What it does |
|---|---|---|
| `focus_window` | `hwnd` (int, from `list_windows`) | Brings a window to the foreground |
| `close_window` | `hwnd` (int, from `list_windows`) | Gracefully closes a window via WM_CLOSE (like clicking X; apps with unsaved changes show their save dialog) |
| `minimize_window` | `hwnd` (int) | Minimizes a window |
| `maximize_window` | `hwnd` (int) | Maximizes a window |
| `type_text` | `text` (string) | Types text into the focused window |
| `hotkey` | `keys` (string, e.g. `"ctrl+c"`, `"alt+f4"`, `"enter"`) | Presses a key combo |
| `mouse_move` | `x`, `y` (int) | Moves the cursor to physical screen pixels |
| `mouse_click` | `x`, `y` (int), `button` (`left`/`right`/`middle`) | Moves to (x, y) and clicks (physical pixels; the bridge is DPI-aware) |
| `mouse_scroll` | `direction` (`up`/`down`), `clicks` (int) | Scrolls the wheel under the cursor |
| `uia_find` | `window` (title regex or hwnd), `name_rx`, `control_type`, `max_results` | Finds UI elements via Windows UI Automation — no screenshot needed; returns name, control type, and bounding rect |
| `uia_click` | `window`, `name_rx`, `control_type`, `index`, `button` | Clicks a UI element by name (InvokePattern, falls back to coordinate click) — no screenshot needed |
| `uia_set_text` | `window`, `name_rx`, `text`, `control_type`, `index` | Sets text of an edit control by name (ValuePattern, falls back to focus+type) — no screenshot needed |
| `clipboard_set` | `text` (string) | Puts text on the Windows clipboard |
| `paste_text` | `text` (string) | Pastes text into the focused window via clipboard (reliable for long text; restores prior clipboard) |
| `kill_process` | `pid` (int) | Terminates a process (refuses the bridge's own PID) |
| `shell_exec` | `command` (string), `timeout_s` (int, default 60) | Runs a command in `cmd.exe`, returns stdout/stderr/exit code |

## File tools — full read/write access to the machine

Paths are absolute Windows paths anywhere on the machine (the read tools
are restricted to the user profile; the write tools below are not — the
on-PC approval dialog showing the full path is the guardrail). Text
files only (UTF-8, 1 MB cap, mirroring `read_file`); writes are
byte-exact, no newline translation, so write → read roundtrips cleanly.

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `write_file` | `path`, `content`, `dry_run` (bool) | Yes, unless `dry_run` | Creates/overwrites a text file (parent dirs created) |
| `edit_file` | `path`, `old_text`, `new_text`, `dry_run` (bool) | Yes, unless `dry_run` | Replaces the first occurrence of `old_text`; errors if not found |
| `delete_file` | `path`, `dry_run` (bool) | Yes, unless `dry_run` | Permanently deletes a file (files only, not directories) |
| `create_dir` | `path`, `dry_run` (bool) | Yes, unless `dry_run` | Creates a directory including parents (no-op if it exists) |
| `copy_file` | `src`, `dst`, `dry_run` (bool) | Yes, unless `dry_run` | Copies a file, metadata preserved (destination parents created) |
| `move_file` | `src`, `dst`, `dry_run` (bool) | Yes, unless `dry_run` | Moves/renames a file (destination parents created) |
| `file_info` | `path` | No | Size and created/modified timestamps for a file or directory |

`dry_run=True` computes the full plan — including a unified diff for
`write_file`/`edit_file` overwrites — with no approval dialog and zero
side effects. The compensating action for each tool is documented in its
risk profile (`tool_profiles.py`).

### Task state & arbitration (no approval — local bookkeeping)

| Tool | Args | What it does |
|---|---|---|
| `task_create` | `goal` (string), `plan` (string list) | Creates a signed task record; returns `task_id` |
| `task_checkpoint` | `task_id`, `step` (int), `result` (string), `done` (bool) | Appends a signed checkpoint to the task |
| `task_status` | `task_id` (optional) | Verifies the hash chain and returns status; with no id, lists all tasks |
| `arbitrate` | `trajectories` (list of tool-name lists) | Scores/ranks candidate tool sequences by risk: `score`, tier (`auto`/`confirm`/`explicit_intent`), one-line rationale |

Task records are append-only and HMAC-signed with a machine-local key;
each checkpoint hash-chains to the previous one, so tampering is
detected on read. `arbitrate` uses the weighted formula
`0.25·(1−blast) + 0.35·recoverability + 0.15·(1−side_effects) +
0.25·optionality − 0.10·human_cost`; thresholds: >0.8 auto, 0.5–0.8
confirm, <0.5 explicit intent.

## Browser tools — drive a live Edge tab via CDP

Edge must be launched with remote debugging enabled. Proven working flags
(verified 2026-10-04 against Edge 154):

```bat
msedge.exe --remote-debugging-port=9222 --user-data-dir=C:\edge-cdp --remote-allow-origins=*
```

Notes from live testing — `--remote-debugging-port` alone is not enough:

- Without a **separate `--user-data-dir`**, a relaunch reuses the existing
  browser process and the flag is silently ignored (nothing listens on 9222).
- Without **`--remote-allow-origins=*`**, Edge 154 rejects the bridge's
  WebSocket with `403 Forbidden`.
- Requires `pip install websocket-client` on the PC.

The bridge attaches to a live tab and sees the DOM that UI Automation
cannot reach. `browser_snapshot`, `browser_click`, and `browser_fill` all
recurse through **open shadow roots** and **same-origin iframes**
(recursively); snapshot tags such elements `[shadow]` / `[iframe]`, and
reports `cross_origin_iframes` (count of iframes that could not be pierced —
cross-origin DOM is inaccessible from the page context). Verified live
2026-10-04: injected shadow-DOM and `srcdoc`-iframe buttons were listed and
clickable, clicks confirmed via page title changes.

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `browser_snapshot` | `url_contains` (optional filter) | No | Lists interactive elements of the tab: tag + label |
| `browser_navigate` | `url`, `url_contains` | Yes | Navigates the tab (`url` must start with `http(s)://`) |
| `browser_click` | `text`, `url_contains` | Yes | Clicks the element containing `text` |
| `browser_fill` | `label`, `text`, `url_contains` | Yes | Fills the field matching `label` (React-aware) |
| `browser_eval` | `js`, `url_contains` | Yes | Runs JavaScript, returns the value |

`url_contains` picks which tab when several are open (e.g. `"seclayer"`).

Live-verified 2026-10-04 (Edge 154, fresh `--user-data-dir` profile):

- `browser_snapshot` returned real element lists for two pages
  (Tampermonkey welcome page, `seclayer.app` landing — 27 and 39 controls).
- `browser_navigate` moved the tab to `https://seclayer.app`; it rejects
  non-`http(s)` URLs (`data:`) with `ValueError`.
- `browser_fill` filled `seclayer.app`'s URL field; value confirmed via
  `browser_eval`. Note: when no field matches `label`, it falls back to the
  first text-like input instead of returning `NOT-FOUND`.
- `browser_click` clicked a FAQ accordion (`CLICKED:What does Seclayer…`).
- `browser_eval` evaluated expressions and DOM queries (`1+1` → `2`).
- Fixed live: `browser_snapshot`'s JS was `() => {}()` (unwrapped arrow
  IIFE — a SyntaxError; CDP reports it as protocol error `"Uncaught"`).
  Wrapping it (`(() => {})()`) fixed it — commit `8f954f1`.

Not yet verified: closed shadow roots (inaccessible by design), and the
Google account chooser as a separate CDP target (untestable on the fresh
test profile).

## Examples (via `bridge/pc_bridge.py`)

```bash
export PC_BRIDGE_URL=https://<random>.trycloudflare.com
export PC_BRIDGE_TOKEN=<session token from pairing>

python3 bridge/pc_bridge.py tools
python3 bridge/pc_bridge.py call system_info '{}'
python3 bridge/pc_bridge.py call list_dir '{"path": "C:\\Users\\celos\\Downloads"}'
python3 bridge/pc_bridge.py call list_windows '{}'
python3 bridge/pc_bridge.py screenshot --out /tmp/shot.png
# write tools — expect a Yes/No dialog on the PC:
python3 bridge/pc_bridge.py call shell_exec '{"command": "whoami"}'
```

Tool errors come back as `{"error": "..."}` payloads (including
operator-denied approvals) — they never crash the server.
