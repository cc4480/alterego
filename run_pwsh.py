#!/usr/bin/env python3
"""Run a .ps1 file through the bridge shell_pwsh tool and print full output.
Usage: PC_BRIDGE_URL=... python3 run_pwsh.py script.ps1
Prints the tool's full text (not the 4000-char cap of op_call.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge"))
import op_call  # noqa: E402

script_path = sys.argv[1]
with open(script_path, encoding="utf-8") as f:
    command = f.read()

# initialize session
res, sid = op_call.post("/mcp", {"jsonrpc": "2.0", "id": 1,
    "method": "initialize", "params": {"protocolVersion": "2025-06-18",
    "capabilities": {}, "clientInfo": {"name": "run-pwsh", "version": "0.1"}}})
try:
    op_call.post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
except Exception:
    pass

res, _ = op_call.post("/mcp", {"jsonrpc": "2.0", "id": 2,
    "method": "tools/call",
    "params": {"name": "shell_pwsh", "arguments": {"script": command}}}, sid)
content = res.get("content", [])
text = content[0].get("text", "") if content else ""
print(text)
