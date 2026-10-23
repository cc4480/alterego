#!/usr/bin/env python3
"""Run a PowerShell script file through the pc-mcp-bridge shell_pwsh tool.
Usage: PC_BRIDGE_URL=... python3 ~/workspace/alterego/pwsh_call.py <script.ps1>
Prints the tool result text; full result is stashed in /tmp/op-full.json by op_call.py.
"""
import json, os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
script_path = sys.argv[1]
with open(script_path, encoding="utf-8") as f:
    script = f.read()
args = json.dumps({"script": script})
p = subprocess.run(
    [sys.executable, os.path.join(here, "bridge", "op_call.py"),
     "call", "shell_pwsh", args],
    capture_output=True, text=True, cwd=here)
print(p.stdout)
if p.stderr:
    print("STDERR:", p.stderr[-2000:], file=sys.stderr)
sys.exit(p.returncode)
