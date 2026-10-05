#!/usr/bin/env python3
"""Scenario 1a: stage + checklist + Edge front + Bing + fill + snapshot.
Prints the snapshot controls so the operator can pick the Search button."""
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
               "clientInfo": {"name": "s1b", "version": "1"}}})
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

def hwnd(sub):
    for w in raw("list_windows").get("windows", []):
        if sub.lower() in w.get("title", "").lower():
            return w["hwnd"]
    return None

def edge_hwnd():
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


# s1b: pivot to Copilot — click the Copilot nav link on Bing
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
    for w in wins:
        if "Cho-zen1" not in w.get("title", ""):
            return w["hwnd"]
    return wins[0]["hwnd"] if wins else None

h = edge_hwnd2()
if h:
    raw("focus_window", {"hwnd": h})
    raw("maximize_window", {"hwnd": h})
    time.sleep(0.8)
    print("EDGE_FRONT_OK", flush=True)

r = C("browser_click", {"text": "Copilot", "url_contains": "bing.com"})
print("CLICK_RESULT:", str(r)[:200], flush=True)
time.sleep(5)
snap = C("browser_snapshot", {})
print("===SNAPSHOT_CONTROLS===", flush=True)
for c in (snap or {}).get("controls", [])[:50]:
    print(f'{c.get("role")}|{c.get("name")}|{(c.get("text") or "")[:80]}', flush=True)
print("===END===", flush=True)
