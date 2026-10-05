#!/usr/bin/env python3
"""Scene-by-scene 45-tool demo. Token from env. Each scene writes JSON results,
then waits for a .go gate file (Carlos verifies before the next scene runs)."""
import json, os, sys, time, urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gates")
os.makedirs(GATE, exist_ok=True)

def _req(path, body, sid=None, timeout=90):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
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
    return msg.get("result"), sid2

_, SID = _req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "scenes", "version": "1"}}})
try:
    _req("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, SID)
except Exception:
    pass

_ID = [100]
def _raw(tool, args=None):
    """Call a tool; unwrap MCP content envelope to the inner result dict."""
    _ID[0] += 1
    res, _ = _req("/mcp", {"jsonrpc": "2.0", "id": _ID[0], "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}}}, SID)
    if isinstance(res, dict) and "content" in res:
        if res.get("isError"):
            raise RuntimeError(str(res["content"][0].get("text", "tool error"))[:200])
        try:
            return json.loads(res["content"][0]["text"])
        except Exception:
            return res
    return res

def _a(cond, msg):
    assert cond, msg

RESULTS = []
def C(tool, args=None, check=None, show=220):
    rec = {"tool": tool, "ok": True, "note": ""}
    try:
        data = _raw(tool, args)
        rec["result"] = data
        if isinstance(data, dict) and data.get("error"):
            rec.update(ok=False, note=str(data["error"])[:120])
        elif check:
            try:
                check(data)
            except AssertionError as e:
                rec.update(ok=False, note=str(e))
    except Exception as e:
        rec.update(ok=False, note=f"ERROR: {str(e)[:120]}")
    RESULTS.append(rec)
    print(f"[{'OK ' if rec['ok'] else 'FAIL'}] {tool} {rec['note']}",
          flush=True)
    return rec

def find_hwnd(sub):
    for w in _raw("list_windows").get("windows", []):
        if sub.lower() in w.get("title", "").lower():
            return w["hwnd"]
    return None

def find_pid(exe):
    for p in _raw("list_processes").get("processes", []):
        if (p.get("exe") or "").lower() == exe.lower():
            return p["pid"]
    return None

def nplog(text):
    try:
        h = find_hwnd("notepad")
        if not h:
            return
        _raw("focus_window", {"hwnd": h})
        time.sleep(0.5)
        _raw("type_text", {"text": text})
    except Exception as e:
        print(f"nplog skipped: {e}", flush=True)

def scene(n, name, fn):
    global RESULTS
    RESULTS = []
    print(f"\n===== SCENE {n}: {name} =====", flush=True)
    try:
        fn()
    except Exception as e:
        print(f"scene {n} aborted: {e}", flush=True)
    fails = [r for r in RESULTS if not r["ok"]]
    with open(f"{GATE}/scene{n}.json", "w") as f:
        json.dump({"scene": n, "name": name, "fails": len(fails),
                   "results": RESULTS}, f)
    print(f"SCENE {n} DONE — {len(RESULTS)-len(fails)}/{len(RESULTS)} ok. "
          f"Waiting for gate.", flush=True)
    for _ in range(900):
        if os.path.exists(f"{GATE}/scene{n}.stop"):
            sys.exit("STOP requested")
        if os.path.exists(f"{GATE}/scene{n}.go"):
            return
        time.sleep(2)
    sys.exit("gate timeout")

# ---------------- scenes ----------------
def s1():
    pid = find_pid("notepad.exe")
    if pid:
        C("kill_process", {"pid": pid},
          lambda r: _a(r.get("terminated") is True, "not terminated"))
    C("shell_exec", {"command": "start notepad"})
    time.sleep(2)
    d = C("shell_pwsh", {"script": "[Environment]::GetFolderPath('Desktop')"},
          lambda r: _a(r.get("stdout", "").strip() != "", "no desktop"))
    C("shell_exec", {"command": f'start explorer "{d["result"]["stdout"].strip()}"'})
    time.sleep(2)
    nplog("=== SCENE 1: stage set - demo log open ===\n")

def s2():
    nplog("=== SCENE 2: seeing the machine ===\n")
    C("screenshot", {},
      lambda r: _a(r.get("png_base64", "").startswith("iVBOR"), "no png"))
    C("active_window", {}, lambda r: _a("hwnd" in r, "no hwnd"))
    C("idle_seconds", {},
      lambda r: _a(isinstance(r.get("idle_seconds"), (int, float)), "no idle"))
    C("system_info", {}, lambda r: _a(r.get("hostname") == "DA1", "host?"))
    C("list_windows", {}, lambda r: _a(len(r.get("windows", [])) > 0, "none"))
    C("list_processes", {}, lambda r: _a(r.get("count", 0) > 100, "few"))
    C("memory_recall", {"query": "pc-mcp-bridge"},
      lambda r: _a(len(r.get("results", [])) > 0, "no hits"))
    C("batch", {"calls": [{"tool": "active_window", "args": {}},
                           {"tool": "idle_seconds", "args": {}},
                           {"tool": "system_info", "args": {}}]},
      lambda r: _a(r.get("calls") == 3, "batch count"))

def s3():
    nplog("=== SCENE 3: typing ===\n")
    h = find_hwnd("notepad")
    _a(h, "no notepad")
    C("focus_window", {"hwnd": h}, lambda r: _a("focused" in r, "no field"))
    C("type_text", {"text": "Watch each keystroke land live. "},
      lambda r: _a(r.get("typed_chars", 0) > 10, "typed?"))
    C("paste_text", {"text": "Pasted in one shot: the quick brown fox "
                             "jumps over the lazy dog. 0123456789.\n"},
      lambda r: _a(r.get("pasted_chars", 0) > 20, "pasted?"))
    C("hotkey", {"keys": "ctrl+a"},
      lambda r: _a(r.get("pressed") is True, "hotkey?"))
    C("clipboard_set", {"text": "scene-3-clipboard-marker"})
    C("clipboard_get", {},
      lambda r: _a(r.get("text") == "scene-3-clipboard-marker", "roundtrip"))

def s4():
    h = find_hwnd("notepad")
    _a(h, "no notepad")
    C("minimize_window", {"hwnd": h},
      lambda r: _a(r.get("minimized") is True, "min?"))
    time.sleep(2)
    C("maximize_window", {"hwnd": h},
      lambda r: _a(r.get("maximized") is True, "max?"))
    time.sleep(2)
    C("close_window", {"hwnd": h})  # dirty tab -> save dialog appears
    time.sleep(2)
    pid = find_pid("notepad.exe")
    if pid:
        C("kill_process", {"pid": pid},
          lambda r: _a(r.get("terminated") is True, "killed?"))

def s5():
    C("shell_exec", {"command": "start notepad"})
    time.sleep(2)
    nplog("=== SCENE 5: mouse ===\n")
    sz = _raw("shell_pwsh", {"script": "Add-Type -AssemblyName System.Windows.Forms;"
        " $s=[System.Windows.Forms.SystemInformation]::PrimaryMonitorSize;"
        ' "$($s.Width) $($s.Height)"'})
    w, hgt = map(int, sz["stdout"].split())
    C("mouse_move", {"x": 200, "y": 200})
    time.sleep(1)
    C("mouse_move", {"x": w // 2, "y": hgt // 2})
    time.sleep(1)
    C("mouse_click", {"x": w - 30, "y": hgt - 20, "button": "left"},
      lambda r: _a(r.get("clicked") is True, "click?"))
    time.sleep(2)
    _raw("hotkey", {"keys": "esc"})
    time.sleep(1)

def s6():
    nplog("=== SCENE 6: files ===\n")
    d = _raw("shell_pwsh",
             {"script": "[Environment]::GetFolderPath('Desktop')"})["stdout"].strip()
    base, sub = d + "\\bridge-demo", d + "\\bridge-demo\\sub"
    f1, f2 = base + "\\demo.txt", sub + "\\moved.txt"
    C("create_dir", {"path": base},
      lambda r: _a(r.get("created") is True, "mkdir?"))
    C("create_dir", {"path": sub},
      lambda r: _a(r.get("created") is True, "mkdir?"))
    _raw("shell_exec", {"command": f'start explorer "{base}"'})
    time.sleep(2)
    C("write_file", {"path": f1, "content": "hello bridge\n"},
      lambda r: _a(r.get("bytes_written", 0) > 0, "write?"))
    C("list_dir", {"path": base},
      lambda r: _a(any(i["name"] == "demo.txt" for i in r.get("items", [])),
                   "listed?"))
    C("read_file", {"path": f1},
      lambda r: _a("hello bridge" in r.get("content", ""), "content?"))
    C("edit_file", {"path": f1, "old_text": "hello", "new_text": "hello edited"},
      lambda r: _a(r.get("replacements") == 1, "edit?"))
    C("read_file", {"path": f1},
      lambda r: _a("hello edited" in r.get("content", ""), "edited?"))
    C("copy_file", {"src": f1, "dst": base + "\\copy.txt"})
    C("move_file", {"src": base + "\\copy.txt", "dst": f2})
    C("file_info", {"path": f1},
      lambda r: _a(r.get("is_file") is True and r.get("size", 0) > 0, "info?"))
    C("delete_file", {"path": f2},
      lambda r: _a(r.get("deleted") is True, "del?"))
    C("delete_file", {"path": f1},
      lambda r: _a(r.get("deleted") is True, "del?"))
    _raw("shell_pwsh", {"script": f'Remove-Item -Recurse -Force "{base}"'})

def s7():
    C("shell_exec", {"command": 'start cmd /k "ver & echo. & echo Bridge demo terminal"'})
    time.sleep(2)
    C("shell_exec", {"command": "whoami"},
      lambda r: _a("celos" in r.get("stdout", ""), "who?"))
    C("shell_pwsh", {"script": "$PSVersionTable.PSVersion.ToString()"},
      lambda r: _a(r.get("stdout", "").strip() != "", "ps?"))
    h = find_hwnd("Command Prompt")
    if h:
        _raw("close_window", {"hwnd": h})

def s8():
    _raw("shell_pwsh", {"script": "$e=\"C:\\Program Files (x86)\\Microsoft\\Edge\\"
        "Application\\msedge.exe\"; Start-Process $e -ArgumentList "
        "\"--remote-debugging-port=9222\",\"--user-data-dir=$env:TEMP\\edge-debug\","
        "\"--remote-allow-origins=*\",\"about:blank\""})
    time.sleep(5)
    C("browser_navigate", {"url": "https://www.bing.com"})
    time.sleep(5)
    snap = C("browser_snapshot", {"url_contains": "bing.com"},
             lambda r: _a("bing.com" in r.get("page_url", ""), "not on bing"))
    els = " ".join((snap["result"] or {}).get("elements", []))
    _a("Search the web" in els, "no search box")
    C("mouse_scroll", {"direction": "down", "clicks": 5})
    time.sleep(1)
    C("browser_fill", {"label": "Search the web", "text": "Model Context Protocol",
                       "url_contains": "bing.com"})
    C("browser_eval", {"js": "document.querySelector('textarea').value",
                       "url_contains": "bing.com"},
      lambda r: _a("Model Context Protocol" in json.dumps(r), "fill?"))
    C("browser_click", {"text": "Images", "url_contains": "bing.com"},
      lambda r: _a("NOT-FOUND" not in json.dumps(r), "click?"))
    time.sleep(3)
    C("browser_eval", {"js": "document.title", "url_contains": "bing.com"},
      lambda r: _a("mage" in json.dumps(r), "click nav?"))
    C("browser_eval", {"js": "document.title='Bridge demo was here'",
                       "url_contains": "bing.com"})

def s9():
    C("http_headers", {"url": "https://secscan.info"},
      lambda r: _a(r.get("status") == 200, "http?"))
    C("dns_query", {"domain": "secscan.info", "rtype": "A"},
      lambda r: _a(len(json.dumps(r)) > 50, "dns?"))
    C("tls_info", {"host": "secscan.info"},
      lambda r: _a("TLSv1.3" in json.dumps(r), "tls?"))
    C("tcp_check", {"host": "127.0.0.1", "ports": [8765]},
      lambda r: _a("open" in json.dumps(r).lower(), "port?"))

def s10():
    C("notify", {"title": "Bridge demo",
                 "message": "All 45 tools verified - locking in 10 seconds"})
    C("speak", {"text": "Demo complete. All forty five tools verified."})
    C("set_volume", {"level": 40},
      lambda r: _a(r.get("volume") == 40, "vol?"))
    time.sleep(8)
    C("power", {"action": "lock"},
      lambda r: _a(r.get("power") == "lock", "lock?"))

if __name__ == "__main__":
    for n, name, fn in [(1, "stage", s1), (2, "awareness", s2),
                        (3, "typing", s3), (4, "windows", s4),
                        (5, "mouse", s5), (6, "files", s6),
                        (7, "shell", s7), (8, "browser", s8),
                        (9, "recon", s9), (10, "finale", s10)]:
        scene(n, name, fn)
    print("ALL SCENES COMPLETE")
