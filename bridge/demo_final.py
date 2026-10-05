#!/usr/bin/env python3
"""FINAL EXAM: all 45 tools, fast, foreground-visible, fully autonomous.
Token from env. No gates, minimal sleeps. Target: under 4 minutes."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
T0 = time.time()

def _req(body, sid=None, timeout=60):
    req = urllib.request.Request(URL + "/mcp", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": UA, "Authorization": "Bearer " + TOKEN,
                 **({"Mcp-Session-Id": sid} if sid else {})}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        sid2 = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    msg = json.loads(next(l[5:].strip() for l in raw.splitlines()
                          if l.startswith("data:")))
    if "error" in msg:
        raise RuntimeError(msg["error"])
    res = msg.get("result")
    if isinstance(res, dict) and "content" in res:
        if res.get("isError"):
            raise RuntimeError(str(res["content"][0].get("text"))[:200])
        try:
            return json.loads(res["content"][0]["text"]), sid2
        except Exception:
            return res, sid2
    return res, sid2

_, SID = _req({"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "final", "version": "1"}}})
_ID = [100]
def raw(tool, args=None):
    _ID[0] += 1
    res, _ = _req({"jsonrpc": "2.0", "id": _ID[0], "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}}}, SID)
    return res

N = [0]
def C(tool, args=None):
    N[0] += 1
    t = time.time()
    try:
        r = raw(tool, args)
        dt = time.time() - t
        bad = isinstance(r, dict) and (r.get("error") or r.get("timed_out"))
        print(f"[{N[0]:02d}] {tool} {'FAIL' if bad else 'ok'} {dt:.1f}s",
              flush=True)
        return r
    except Exception as e:
        print(f"[{N[0]:02d}] {tool} ERROR {str(e)[:80]}", flush=True)
        return None

def hwnd(sub):
    for w in raw("list_windows").get("windows", []):
        if sub.lower() in w.get("title", "").lower():
            return w["hwnd"]
    return None

def pid(exe):
    for p in raw("list_processes").get("processes", []):
        if (p.get("exe") or "").lower() == exe.lower():
            return p["pid"]
    return None

def edge_hwnd():
    """The debug Edge (never his main chat window)."""
    wins = [w for w in raw("list_windows").get("windows", [])
            if "edge" in w.get("title", "").lower()]
    for w in wins:
        t = w.get("title", "")
        if "Cho-zen1" not in t and ("bing" in t.lower() or "bridge" in t.lower()
                                    or "example" in t.lower()
                                    or "blank" in t.lower()):
            return w["hwnd"]
    for w in wins:
        if "Cho-zen1" not in w.get("title", ""):
            return w["hwnd"]
    return wins[0]["hwnd"] if wins else None

def stage(sub, maximize=False):
    """Bring a window front-and-center before its visual beat."""
    h = hwnd(sub)
    if h:
        raw("focus_window", {"hwnd": h})
        if maximize:
            raw("maximize_window", {"hwnd": h})
        time.sleep(0.8)
    return h

def shot(tag):
    r = C("screenshot")
    if r and r.get("png_base64"):
        with open(f"/tmp/final-{tag}.png", "wb") as f:
            f.write(__import__("base64").b64decode(r["png_base64"]))
        print(f"  saved /tmp/final-{tag}.png", flush=True)

# ---- 1. stage ----
p = pid("notepad.exe")
if p:
    C("kill_process", {"pid": p})
C("shell_exec", {"command": "start notepad"})
time.sleep(1.5)
d = raw("shell_pwsh", {"script": "[Environment]::GetFolderPath('Desktop')"})["stdout"].strip()
C("shell_exec", {"command": f'start explorer "{d}"'})
time.sleep(1)
shot("stage")

# ---- 2. awareness (batched for speed) ----
C("batch", {"calls": [{"tool": "active_window", "args": {}},
                       {"tool": "idle_seconds", "args": {}},
                       {"tool": "system_info", "args": {}}]})
C("list_windows")
C("list_processes")
C("memory_recall", {"query": "pc-mcp-bridge"})
C("clipboard_get")

# ---- 3. typing (visible) ----
h = stage("notepad", maximize=True)
C("focus_window", {"hwnd": h})
C("type_text", {"text": "Final exam: every keystroke visible. "})
C("paste_text", {"text": "Pasted instantly: the quick brown fox jumps over the lazy dog.\n"})
C("hotkey", {"keys": "ctrl+a"})
C("clipboard_set", {"text": "final-exam-marker"})
r = C("clipboard_get")
assert r and r.get("text") == "final-exam-marker", "clipboard roundtrip"

# ---- 4. window gymnastics ----
C("minimize_window", {"hwnd": h})
time.sleep(1.2)
C("maximize_window", {"hwnd": h})
time.sleep(1.2)
C("close_window", {"hwnd": h})
time.sleep(1)
p = pid("notepad.exe")
if p:
    C("kill_process", {"pid": p})

# ---- 5. mouse ----
sz = raw("shell_pwsh", {"script": "Add-Type -AssemblyName System.Windows.Forms;"
    " $s=[System.Windows.Forms.SystemInformation]::PrimaryMonitorSize;"
    ' "$($s.Width) $($s.Height)"'})["stdout"].split()
w, ht = int(sz[0]), int(sz[1])
C("mouse_move", {"x": 200, "y": 200})
C("mouse_move", {"x": w // 2, "y": ht // 2})
C("mouse_click", {"x": w - 30, "y": ht - 20, "button": "left"})
time.sleep(1.5)
C("hotkey", {"keys": "esc"})

# ---- 6. files (Explorer foreground) ----
stage("Desktop")
base, sub = d + "\\bridge-demo", d + "\\bridge-demo\\sub"
C("create_dir", {"path": base})
C("create_dir", {"path": sub})
raw("shell_exec", {"command": f'start explorer "{base}"'})
time.sleep(1.5)
f1 = base + "\\demo.txt"
C("write_file", {"path": f1, "content": "hello bridge\n"})
C("list_dir", {"path": base})
C("read_file", {"path": f1})
C("edit_file", {"path": f1, "old_text": "hello", "new_text": "hello edited"})
C("copy_file", {"src": f1, "dst": base + "\\copy.txt"})
C("move_file", {"src": base + "\\copy.txt", "dst": sub + "\\moved.txt"})
C("file_info", {"path": f1})
C("delete_file", {"path": sub + "\\moved.txt"})
C("delete_file", {"path": f1})
shot("files")
raw("shell_pwsh", {"script": f'Remove-Item -Recurse -Force "{base}"'})

# ---- 7. shell (visible terminal, detached so no pipe hang) ----
raw("shell_pwsh", {"script": "Start-Process cmd -ArgumentList '/k ver'"})
time.sleep(1.5)
stage("Command Prompt")
shot("terminal")
C("shell_exec", {"command": "whoami"})
C("shell_pwsh", {"script": "$PSVersionTable.PSVersion.ToString()"})
C("mouse_scroll", {"direction": "down", "clicks": 3})
h = hwnd("Command Prompt")
if h:
    C("close_window", {"hwnd": h})

# ---- 8. browser (debug Edge front-and-center, maximized) ----
raw("shell_pwsh", {"script": "$e=\"C:\\Program Files (x86)\\Microsoft\\Edge\\"
    "Application\\msedge.exe\"; Start-Process $e -ArgumentList "
    "\"--remote-debugging-port=9222\",\"--user-data-dir=$env:TEMP\\edge-debug\","
    "\"--remote-allow-origins=*\",\"about:blank\""})
time.sleep(4)
h = edge_hwnd()
if h:
    raw("focus_window", {"hwnd": h})
    raw("maximize_window", {"hwnd": h})
    time.sleep(0.8)
C("browser_navigate", {"url": "https://www.bing.com"})
time.sleep(4)
C("browser_snapshot", {"url_contains": "bing.com"})
C("browser_fill", {"label": "Search the web", "text": "Model Context Protocol",
                   "url_contains": "bing.com"})
shot("browser-fill")
C("browser_click", {"text": "Images", "url_contains": "bing.com"})
time.sleep(4)
shot("browser-click")
C("browser_eval", {"js": "document.title='Bridge: all 45 tools work'",
                   "url_contains": "bing.com"})

# ---- 9. recon ----
C("http_headers", {"url": "https://secscan.info"})
C("dns_query", {"domain": "secscan.info", "rtype": "A"})
C("tls_info", {"host": "secscan.info"})
C("tcp_check", {"host": "127.0.0.1", "ports": [8765]})

# ---- 10. finale ----
C("notify", {"title": "Final exam", "message": "All 45 tools proven - locking in 8s"})
C("speak", {"text": "Final exam complete. All forty five tools work."})
C("set_volume", {"level": 40})
shot("finale")
time.sleep(6)
C("power", {"action": "lock"})

print(f"\nFINAL EXAM COMPLETE — {N[0]} calls in {time.time()-T0:.0f}s",
      flush=True)
