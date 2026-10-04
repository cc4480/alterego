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
| `type_text` | `text` (string) | Types text into the focused window |
| `shell_exec` | `command` (string), `timeout_s` (int, default 60) | Runs a command in `cmd.exe`, returns stdout/stderr/exit code |

## Browser tools — drive the live Edge tab via CDP

Edge must run with `--remote-debugging-port=9222` (add to the Edge shortcut
target). The bridge attaches to the user's real tab — sessions and logins
intact — and sees the full DOM: buttons, inputs, iframes, and popups that
UI Automation cannot reach. Requires `pip install websocket-client`.

| Tool | Args | Approval | What it does |
|---|---|---|---|
| `browser_snapshot` | `url_contains` (optional filter) | No | Lists interactive elements of the tab: tag + label |
| `browser_navigate` | `url`, `url_contains` | Yes | Navigates the tab |
| `browser_click` | `text`, `url_contains` | Yes | Clicks the element containing `text` |
| `browser_fill` | `label`, `text`, `url_contains` | Yes | Fills the field matching `label` (React-aware) |
| `browser_eval` | `js`, `url_contains` | Yes | Runs JavaScript, returns the value |

`url_contains` picks which tab when several are open (e.g. `"seclayer"`).

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
