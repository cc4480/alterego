#!/usr/bin/env python3
"""Scenario 1g: all-in-one Copilot demo. Ensures the debug Edge (relaunch if
dead), navigates to Copilot, discovers the chatbox via eval, fills a question
with browser_fill, sends it, screenshots the reply."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def _req(body, sid=None, timeout=90):
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
               "clientInfo": {"name": "s1g", "version": "1"}}})
_ID = [100]
def raw(tool, args=None):
    _ID[0] += 1
    res, _ = _req({"jsonrpc": "2.0", "id": _ID[0], "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}}}, SID)
    return res

def C(tool, args=None):
    try:
        r = raw(tool, args)
        bad = isinstance(r, dict) and (r.get("error") or r.get("timed_out"))
        print(f"{tool} {'FAIL' if bad else 'ok'}", flush=True)
        return r
    except Exception as e:
        print(f"{tool} ERROR {str(e)[:100]}", flush=True)
        return None

PROFILE = "C:\\Users\\celos\\AppData\\Local\\edge-debug"
KILL_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
    "Where-Object { $_.CommandLine -like '*edge-debug*' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
    "Start-Sleep 2; 'killed'"
)
WIPE_PS = KILL_PS.replace("'killed'",
    f"Remove-Item -Recurse -Force '{PROFILE}' -ErrorAction SilentlyContinue; 'wiped'")
LAUNCH_PS = (
    "Start-Process 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' "
    "-ArgumentList '--remote-debugging-port=9222',"
    f"'--user-data-dir={PROFILE}',"
    "'--remote-allow-origins=*','--no-first-run','--no-default-browser-check',"
    "'about:blank'"
)

def port_open():
    r = raw("tcp_check", {"host": "127.0.0.1", "ports": [9222]})
    return (r.get("ports") or {}).get("9222") == "open"

def cdp_alive():
    try:
        raw("browser_eval", {"js": "1+1"})
        return True
    except Exception:
        return False

def edge_hwnd2():
    wins = [w for w in raw("list_windows").get("windows", [])
            if "edge" in w.get("title", "").lower()]
    for w in wins:
        t = w.get("title", "")
        if "Cho-zen1" not in t and ("bing" in t.lower() or "bridge" in t.lower()
                                    or "copilot" in t.lower()
                                    or "example" in t.lower()
                                    or "blank" in t.lower()):
            return w["hwnd"]
    return None

def wait_cdp(timeout_s=30):
    for i in range(timeout_s // 2):
        time.sleep(2)
        if port_open() and cdp_alive():
            return True
    return False

print("ensuring debug Edge...", flush=True)
if port_open() and cdp_alive():
    print("CDP already alive", flush=True)
else:
    print("launching (profile kept, no first-run dialogs)", flush=True)
    C("shell_pwsh", {"script": KILL_PS})
    C("shell_pwsh", {"script": LAUNCH_PS})
    if not wait_cdp():
        print("still dead - wiping profile and relaunching", flush=True)
        C("shell_pwsh", {"script": WIPE_PS})
        C("shell_pwsh", {"script": LAUNCH_PS})
        if not wait_cdp():
            print("CDP_FAILED_TO_START", flush=True)
            sys.exit(1)
    print("CDP up", flush=True)

h = edge_hwnd2()
if h:
    raw("focus_window", {"hwnd": h})
    raw("maximize_window", {"hwnd": h})
    time.sleep(0.8)
raw("browser_eval", {"js": "document.title='BRIDGE BROWSER - DO NOT CLOSE'"})

C("browser_navigate", {"url": "https://www.bing.com"})
time.sleep(4)
r = C("browser_click", {"text": "Copilot", "url_contains": "bing.com"})
print("COPILOT_CLICK:", str(r)[:150], flush=True)
time.sleep(8)

DISCOVER_JS = """(() => {
  const vis = e => e.offsetParent !== null;
  const inputs = Array.from(document.querySelectorAll(
    'textarea, input[type=text], input:not([type]), [contenteditable="true"]'))
    .filter(vis).map(e => ({tag: e.tagName, ph: e.placeholder || '',
      aria: e.getAttribute('aria-label') || '', id: e.id || ''}));
  const btns = Array.from(document.querySelectorAll('button')).filter(vis)
    .map(e => (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0,40))
    .filter(t => t);
  return {url: location.href, title: document.title,
          inputs: inputs.slice(0,8), buttons: btns.slice(0,25)};
})()"""
d = C("browser_eval", {"js": DISCOVER_JS})
try:
    d = json.loads(d.get("value") or "{}")
except Exception:
    d = {}
print("PAGE:", str(d.get("url"))[:80], "|", str(d.get("title"))[:60], flush=True)
chatbox = None
for inp in d.get("inputs", []):
    lbl = inp.get("aria") or inp.get("ph")
    if lbl:
        chatbox = lbl
        break
print("CHATBOX:", chatbox, flush=True)
print("BUTTONS:", [b for b in d.get("buttons", [])][:12], flush=True)

if not chatbox:
    print("NO_CHATBOX_FOUND", flush=True)
    sys.exit(2)

QUESTION = "What is the Model Context Protocol? Answer in two sentences."
C("browser_fill", {"label": chatbox, "text": QUESTION})
time.sleep(1)
# send: click a send/submit button, else Enter
sent = False
for b in d.get("buttons", []):
    if any(k in b.lower() for k in ("send", "submit")):
        r = C("browser_click", {"text": b})
        print("SEND_CLICK:", str(r)[:120], flush=True)
        sent = True
        break
if not sent:
    C("hotkey", {"keys": "enter"})
    print("SENT_VIA_ENTER", flush=True)

print("waiting for Copilot reply...", flush=True)
time.sleep(15)
r = C("screenshot")
if r and r.get("png_base64"):
    import base64
    open("/tmp/s1g-copilot.png", "wb").write(
        base64.b64decode(r["png_base64"]))
    print("saved /tmp/s1g-copilot.png", flush=True)
resp = C("browser_eval", {"js":
    "(() => { const m = document.querySelector('main'); "
    "return (m ? m.innerText : document.body.innerText).slice(-2500); })()"})
try:
    resp = json.loads(resp.get("value") or '""')
except Exception:
    pass
print("===COPILOT_REPLY===", flush=True)
print(str(resp)[:2500], flush=True)
print("===END===", flush=True)
