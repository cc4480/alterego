#!/usr/bin/env python3
"""Scenario 1d: diagnose 9222, wipe stale debug profile, relaunch, Copilot."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

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
               "clientInfo": {"name": "s1d", "version": "1"}}})
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
        t = w.get("title", "")
        if "Cho-zen1" not in t and ("bing" in t.lower() or "bridge" in t.lower()
                                    or "copilot" in t.lower()
                                    or "example" in t.lower()
                                    or "blank" in t.lower()):
            return w["hwnd"]
    return None

# diagnose: what msedge processes exist, is 9222 open?
r = C("shell_pwsh", {"script":
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
    "ForEach-Object { $_.ProcessId, $_.CommandLine -join ' || ' }"})
print("PROCS:", str(r)[:600], flush=True)
r = C("tcp_check", {"host": "127.0.0.1", "ports": [9222]})
print("PORT9222:", str(r)[:200], flush=True)

# wipe the stale debug profile (kills Singleton/DevToolsActivePort ghosts)
r = C("shell_pwsh", {"script":
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
    "Where-Object { $_.CommandLine -like '*edge-debug*' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
    "Start-Sleep 2; "
    "Remove-Item -Recurse -Force \"$env:TEMP\\edge-debug\" -ErrorAction SilentlyContinue; "
    "'wiped'"})
print("WIPE:", str(r)[:120], flush=True)

EDGE_PS = (
    "$e='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'; "
    "Start-Process $e -ArgumentList "
    "'--remote-debugging-port=9222',"
    "'--user-data-dir=$env:TEMP\\edge-debug',"
    "'--remote-allow-origins=*','about:blank'"
)
raw("shell_pwsh", {"script": EDGE_PS})
for i in range(6):
    time.sleep(2)
    r = raw("tcp_check", {"host": "127.0.0.1", "ports": [9222]})
    st = (r.get("results") or [{}])[0].get("open")
    print(f"poll9222 try{i}: open={st}", flush=True)
    if st:
        break

h = edge_hwnd2()
if h:
    raw("focus_window", {"hwnd": h})
    raw("maximize_window", {"hwnd": h})
    time.sleep(0.8)
    print("EDGE_FRONT_OK", flush=True)

C("browser_navigate", {"url": "https://www.bing.com"})
time.sleep(4)
snap = C("browser_snapshot", {"url_contains": "bing.com"})
print(f"CONTROLS: {len((snap or {}).get('controls', []))}", flush=True)

r = C("browser_click", {"text": "Copilot", "url_contains": "bing.com"})
print("COPILOT_CLICK:", str(r)[:200], flush=True)
time.sleep(6)
snap = C("browser_snapshot", {})
print("===COPILOT_CONTROLS===", flush=True)
for c in (snap or {}).get("controls", [])[:60]:
    print(f'{c.get("role")}|{c.get("name")}|{(c.get("text") or "")[:80]}',
          flush=True)
print("===END===", flush=True)
