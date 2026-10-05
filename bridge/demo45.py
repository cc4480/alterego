#!/usr/bin/env python3
"""Run all 45 pc-mcp-bridge tools as a demo. URL/token from env. Never prints the token."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def _req(path, body, sid=None, timeout=90):
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": UA,
                 "Authorization": f"Bearer {TOKEN}",
                 **({"Mcp-Session-Id": sid} if sid else {})},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        sid2 = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    payload = next((l[5:].strip() for l in raw.splitlines()
                    if l.startswith("data:")), "{}")
    msg = json.loads(payload)
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg.get("result"), sid2

_, SID = _req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "demo45", "version": "1"}}})
try:
    _req("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, SID)
except Exception:
    pass

_n = [0]
def call(tool, args=None, show=500):
    _n[0] += 1
    print(f"===== [{_n[0]}] {tool} =====", flush=True)
    try:
        res, _ = _req("/mcp", {"jsonrpc": "2.0", "id": 100 + _n[0],
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}}}, SID)
        text = res["content"][0]["text"]
        try:
            parsed = json.loads(text)
            print(json.dumps(parsed, indent=1)[:show])
            return parsed
        except Exception:
            print(text[:show])
            return text
    except Exception as e:
        print(f"FAILED: {e}")
        return None
    finally:
        print(flush=True); sys.stdout.flush()

def find_window(substr):
    wins = call("list_windows", {}, 200) or {}
    for w in wins.get("windows", []):
        if substr.lower() in w.get("title", "").lower():
            return w["hwnd"]
    return None

def find_pid(exe):
    procs = call("list_processes", {}, 200) or {}
    items = procs.get("processes") or procs.get("items") or []
    for p in items:
        name = p.get("exe") or p.get("name") or ""
        if name.lower() == exe.lower():
            return p.get("pid")
    return None

print("### 1. AWARENESS", flush=True)
call("active_window", {})
call("idle_seconds", {})
call("system_info", {}, 400)
call("screenshot", {}, 150)

print("### 2. SAVE CLIPBOARD", flush=True)
clip0 = call("clipboard_get", {}, 150)
clip0_text = clip0.get("text", "") if isinstance(clip0, dict) else ""

print("### 3. NOTEPAD — input + window control", flush=True)
call("shell_exec", {"command": "start notepad"}, 150); time.sleep(2)
hwnd = find_window("notepad")
if hwnd:
    call("focus_window", {"hwnd": hwnd}, 150); time.sleep(1)
    call("type_text", {"text": "Hello Carlos - typing demo. "}, 150); time.sleep(1)
    call("paste_text", {"text": "Paste demo: the quick brown fox jumps over the lazy dog. 0123456789."}, 150); time.sleep(1)
    call("hotkey", {"keys": "ctrl+a"}, 150); time.sleep(1)
    call("minimize_window", {"hwnd": hwnd}, 150); time.sleep(2)
    call("maximize_window", {"hwnd": hwnd}, 150); time.sleep(2)

print("### 4. MOUSE + CLIPBOARD", flush=True)
sz = call("shell_pwsh", {"script": "Add-Type -AssemblyName System.Windows.Forms; $s=[System.Windows.Forms.SystemInformation]::PrimaryMonitorSize; \"$($s.Width) $($s.Height)\""}, 150)
try:
    w, h = (sz.get("stdout", "") or "").split()
    cx, cy = int(int(w) / 2), int(int(h) / 2)
except Exception:
    cx, cy = 960, 540
call("mouse_move", {"x": 200, "y": 200}, 150); time.sleep(1)
call("mouse_move", {"x": cx, "y": cy}, 150); time.sleep(1)
call("mouse_click", {"x": cx, "y": cy, "button": "left"}, 150); time.sleep(1)
call("mouse_scroll", {"direction": "down", "clicks": 3}, 150); time.sleep(1)
call("clipboard_set", {"text": "clipboard demo"}, 150)
call("clipboard_get", {}, 150)
if hwnd:
    call("close_window", {"hwnd": hwnd}, 150); time.sleep(1)

print("### 5. SHELL + KILL", flush=True)
call("shell_exec", {"command": "whoami"}, 150)
call("shell_pwsh", {"script": "$PSVersionTable.PSVersion.ToString()"}, 150)
call("shell_exec", {"command": "start notepad"}, 150); time.sleep(2)
pid = find_pid("notepad.exe")
if pid:
    call("kill_process", {"pid": pid}, 150); time.sleep(1)

print("### 6. FILE OPS", flush=True)
tmp = (call("shell_pwsh", {"script": "\"$env:TEMP\\bridge-demo\""}, 150) or {}).get("stdout", "").strip()
if tmp:
    call("create_dir", {"path": tmp}, 150)
    call("write_file", {"path": tmp + "\\demo.txt", "content": "line one\nline two\n"}, 150)
    call("read_file", {"path": tmp + "\\demo.txt"}, 200)
    call("edit_file", {"path": tmp + "\\demo.txt", "old_text": "line two", "new_text": "line TWO (edited)"}, 200)
    call("file_info", {"path": tmp + "\\demo.txt"}, 300)
    call("copy_file", {"src": tmp + "\\demo.txt", "dst": tmp + "\\copy.txt"}, 150)
    call("move_file", {"src": tmp + "\\copy.txt", "dst": tmp + "\\moved.txt"}, 150)
    call("list_dir", {"path": tmp}, 300)
    call("delete_file", {"path": tmp + "\\moved.txt"}, 150)
    call("shell_pwsh", {"script": "Remove-Item -Recurse -Force \"$env:TEMP\\bridge-demo\"; \"cleaned\""}, 150)

print("### 7. WEB RECON", flush=True)
call("http_headers", {"url": "https://secscan.info"}, 500)
call("dns_query", {"name": "secscan.info", "type": "A"}, 400)
call("tls_info", {"host": "secscan.info"}, 500)
call("tcp_check", {"host": "127.0.0.1", "port": 8765}, 200)

print("### 8. BROWSER CDP", flush=True)
call("shell_pwsh", {"script": "$e=\"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe\"; Start-Process $e -ArgumentList \"--remote-debugging-port=9222\",\"--user-data-dir=$env:TEMP\\edge-debug\",\"--remote-allow-origins=*\",\"about:blank\"; \"launched\""}, 150)
time.sleep(6)
call("browser_navigate", {"url": "https://example.com"}, 200); time.sleep(3)
call("browser_snapshot", {}, 1200)
call("browser_eval", {"js": "document.title"}, 150)
call("browser_eval", {"js": "var d=document.createElement('div');d.style='position:fixed;top:8px;left:8px;z-index:99999;background:#fff;border:2px solid #0a0;padding:12px;font:14px sans-serif';d.innerHTML='<label>Bridge Demo Field <input id=\"bdin\" type=\"text\"></label> <button id=\"bdbtn\">Bridge Demo Button</button>';document.body.prepend(d);document.getElementById('bdbtn').onclick=function(){document.title='BRIDGE_CLICK_OK'};'injected'"})
time.sleep(1)
call("browser_fill", {"label": "Bridge Demo Field", "text": "filled by bridge demo"}); time.sleep(1)
call("browser_click", {"text": "Bridge Demo Button"}); time.sleep(1)
call("browser_eval", {"js": "document.title+' | field='+document.getElementById('bdin').value"}, 200)
call("browser_navigate", {"url": "https://www.bing.com"}, 200); time.sleep(4)
call("browser_snapshot", {}, 2500)

print("### 9. ATTENTION", flush=True)
call("notify", {"title": "Bridge demo", "message": "All 45 tools running"}, 150)
call("speak", {"text": "P.C. bridge demo. All forty five tools running."}, 150)
call("set_volume", {"level": 40}, 150)

print("### 10. MEMORY + BATCH", flush=True)
call("memory_recall", {"query": "pc-mcp-bridge named tunnel"}, 500)
call("batch", {"calls": [
    {"tool": "active_window", "args": {}},
    {"tool": "idle_seconds", "args": {}},
    {"tool": "clipboard_get", "args": {}}]}, 500)

print("### 11. RESTORE CLIPBOARD", flush=True)
if clip0_text:
    call("clipboard_set", {"text": clip0_text}, 150)

print("### 12. POWER (45th tool) — locks the PC to close the demo", flush=True)
call("notify", {"title": "Bridge demo", "message": "Demo complete — locking the PC in 10 seconds"}, 150)
time.sleep(10)
call("power", {"action": "lock"}, 150)
print("DEMO COMPLETE — 45/45 tools attempted")
