# Tool reference

All tools are exposed over MCP (Streamable HTTP) at `POST {tunnel-url}/mcp`,
authenticated with the session bearer token from `/pair`.

## Read tools — no approval needed

| Tool | Args | Returns |
|---|---|---|
| `screenshot` | — | `{"png_base64": ..., "width": N, "height": N}` — primary monitor as PNG |
| `list_windows` | — | visible windows: handle (`hwnd`), pid, title |
| `system_info` | — | OS, hostname, username, CPU / memory |
| `list_dir` | `path` (default: profile root) | `{"path": ..., "items": [{"name", "dir", "size"}]}` |
| `read_file` | `path` (required) | UTF-8 text file, max 1 MB |

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
| `type_text` | `text` (string) | Types text into the focused window |
| `shell_exec` | `command` (string), `timeout_s` (int, default 60) | Runs a command in `cmd.exe`, returns stdout/stderr/exit code |

## File tools — full read/write access to the machine

Paths are absolute Windows paths anywhere on the machine (the read tools
are restricted to the user profile; the write tools below are not — the
on-PC approval dialog showing the full path is the guardrail). Text
files only (UTF-8, 1 MB cap, mirroring `read_file`); writes are
byte-exact, no newline translation, so write → read roundtrips cleanly.

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `write_file` | `path`, `content` | Yes | Creates/overwrites a text file (parent dirs created) |
| `edit_file` | `path`, `old_text`, `new_text` | Yes | Replaces the first occurrence of `old_text`; errors if not found |
| `delete_file` | `path` | Yes | Permanently deletes a file (files only, not directories) |
| `create_dir` | `path` | Yes | Creates a directory including parents (no-op if it exists) |

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
