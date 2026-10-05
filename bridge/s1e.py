#!/usr/bin/env python3
"""Scenario 1e: fix the $env:TEMP quoting bug. Kill strays, wipe both the
real and the literal-named profile dirs, relaunch with a hardcoded path."""
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
               "clientInfo": {"name": "s1e", "version": "1"}}})
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

PROFILE = "C:\\Users\\celos\\AppData\\Local\\Temp\\edge-debug"

CLEAN_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
    "Where-Object { $_.CommandLine -like '*edge-debug*' } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
    "Start-Sleep 2; "
    f"Remove-Item -Recurse -Force '{PROFILE}' -ErrorAction SilentlyContinue; "
    "Get-ChildItem C:\\Users\\celos -Directory -Filter '*`$env:TEMP*' "
    "-ErrorAction SilentlyContinue | "
    "ForEach-Object { Remove-Item -Recurse -Force $_.FullName }; "
    "'cleaned'"
)
r = C("shell_pwsh", {"script": CLEAN_PS})
print("CLEAN:", str(r)[:150], flush=True)

LAUNCH_PS = (
    "Start-Process 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' "
    "-ArgumentList '--remote-debugging-port=9222',"
    f"'--user-data-dir={PROFILE}',"
    "'--remote-allow-origins=*','about:blank'"
)
r = C("shell_pwsh", {"script": LAUNCH_PS})
print("LAUNCH:", str(r)[:150], flush=True)

port_open = False
for i in range(8):
    time.sleep(2)
    r = raw("tcp_check", {"host": "127.0.0.1", "ports": [9222]})
    st = (r.get("ports") or {}).get("9222")
    print(f"poll9222 try{i}: {st}", flush=True)
    if st == "open":
        port_open = True
        break

print("PORT9222_OPEN" if port_open else "PORT9222_STILL_CLOSED", flush=True)

# verify the flag actually reached the process
r = C("shell_pwsh", {"script":
    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
    "Where-Object { $_.CommandLine -like '*remote-debugging-port*' } | "
    "ForEach-Object { $_.CommandLine }"})
print("FLAGCHECK:", str(r)[:400], flush=True)

if port_open:
    C("browser_navigate", {"url": "https://www.bing.com"})
    time.sleep(4)
    snap = C("browser_snapshot", {"url_contains": "bing.com"})
    print(f"CONTROLS: {len((snap or {}).get('elements', []))}", flush=True)
    r = C("browser_click", {"text": "Copilot", "url_contains": "bing.com"})
    print("COPILOT_CLICK:", str(r)[:200], flush=True)
    time.sleep(6)
    snap = C("browser_snapshot", {})
    print("===COPILOT_CONTROLS===", flush=True)
    for c in (snap or {}).get("elements", [])[:60]:
        print(f'{c.get("role")}|{c.get("name")}|{(c.get("text") or "")[:80]}',
              flush=True)
    print("===END===", flush=True)
