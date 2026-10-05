#!/usr/bin/env python3
"""Scenario 1f: talk to Copilot. Eval-based page discovery (snapshot is
returning empty), then fill the chatbox and send via the real tools."""
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
               "clientInfo": {"name": "s1f", "version": "1"}}})
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

DISCOVER_JS = """(() => {
  const inputs = Array.from(document.querySelectorAll(
    'textarea, input[type=text], input:not([type]), [contenteditable="true"]'))
    .filter(e => e.offsetParent !== null)
    .map(e => ({tag: e.tagName,
                ph: e.placeholder || '',
                aria: e.getAttribute('aria-label') || '',
                id: e.id || '', name: e.name || ''}));
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(e => e.offsetParent !== null)
    .map(e => (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0,40))
    .filter(t => t);
  return {url: location.href, title: document.title,
          inputs: inputs.slice(0,10), buttons: btns.slice(0,30)};
})()"""

r = C("browser_eval", {"js": DISCOVER_JS})
print("===DISCOVERY===", flush=True)
print(json.dumps(r, indent=1)[:3000], flush=True)
print("===END===", flush=True)
