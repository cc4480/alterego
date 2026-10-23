#!/usr/bin/env python3
"""Run a .ps1 file on Carlos's PC via the AlterEgo bridge shell_pwsh tool.
Usage: ps_call.py <script.ps1> [--timeout-ms N]
Prints stdout/stderr/rc of the remote call.
"""
import json, os, sys, time, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge"))
import token_store
from op_call import URL, TOKEN, post

def call_shell_pwsh(command, timeout_ms=55000):
    res, sid = post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "ps-call", "version": "0.1.0"}}})
    try:
        post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except Exception:
        pass
    res, _ = post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "shell_pwsh",
                   "arguments": {"script": command, "timeout_ms": timeout_ms}}}, sid)
    return res["content"][0]["text"]

def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: ps_call.py <script.ps1>")
    with open(sys.argv[1], encoding="utf-8") as f:
        command = f.read()
    timeout_ms = 55000
    if "--timeout-ms" in sys.argv:
        timeout_ms = int(sys.argv[sys.argv.index("--timeout-ms") + 1])
    text = call_shell_pwsh(command, timeout_ms)
    with open("/tmp/ps-full.json", "w", encoding="utf-8") as f:
        f.write(text)
    try:
        data = json.loads(text)
    except Exception:
        print(text[:6000])
        return
    print("rc:", data.get("exit_code", data.get("rc")))
    out = data.get("stdout", "")
    err = data.get("stderr", "")
    if out:
        print("--- stdout ---")
        print(out[:6000])
    if err:
        print("--- stderr ---")
        print(err[:4000])

if __name__ == "__main__":
    main()
