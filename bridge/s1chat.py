#!/usr/bin/env python3
"""s1chat: send one message to the Copilot chat and print its reply.
MESSAGE comes from the env var COPILOT_MSG."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
MSG = os.environ["COPILOT_MSG"]
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
               "clientInfo": {"name": "s1chat", "version": "1"}}})
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

def edge_hwnd2():
    wins = [w for w in raw("list_windows").get("windows", [])
            if "edge" in w.get("title", "").lower()]
    for w in wins:
        if "Cho-zen1" not in w.get("title", ""):
            return w["hwnd"]
    return None

h = edge_hwnd2()
if h:
    raw("focus_window", {"hwnd": h})
    raw("maximize_window", {"hwnd": h})
    time.sleep(0.8)

C("browser_navigate",
  {"url": "https://copilot.microsoft.com/chats/jNy2kP2xzv3yjqt4H4j23"})
time.sleep(6)
d = C("browser_eval", {"js": """(() => {
  const vis = e => e.offsetParent !== null;
  const inputs = Array.from(document.querySelectorAll(
    'textarea, input[type=text], input:not([type]), [contenteditable="true"]'))
    .filter(vis).map(e => e.getAttribute('aria-label') || e.placeholder || '');
  return {inputs: inputs.slice(0,8)};
})()"""})
try:
    d = json.loads(d.get("value") or "{}")
except Exception:
    d = {}
chatbox = next((i for i in d.get("inputs", []) if i), None)
print("CHATBOX:", chatbox, flush=True)
if not chatbox:
    sys.exit(2)
C("browser_fill", {"label": chatbox, "text": MSG})
time.sleep(1)
C("hotkey", {"keys": "enter"})
print("SENT", flush=True)
time.sleep(20)
r = C("screenshot")
if r and r.get("png_base64"):
    import base64
    open("/tmp/s1chat.png", "wb").write(base64.b64decode(r["png_base64"]))
    print("saved /tmp/s1chat.png", flush=True)
resp = C("browser_eval", {"js":
    "(() => { const m = document.querySelector('main'); "
    "return (m ? m.innerText : document.body.innerText).slice(-3000); })()"})
try:
    resp = json.loads(resp.get("value") or '""')
except Exception:
    pass
print("===COPILOT_REPLY===", flush=True)
print(str(resp)[:3000], flush=True)
print("===END===", flush=True)
