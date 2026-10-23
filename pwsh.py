#!/usr/bin/env python3
"""Run a PowerShell script file on the PC via the AlterEgo bridge (shell_pwsh).
Usage: pwsh.py <script.ps1> [timeout_ms]
Reads script from file, posts to bridge, prints stdout. Exit = bridge rc.
"""
import json, os, subprocess, sys

script_path = sys.argv[1]
with open(script_path, "r", encoding="utf-8") as f:
    script = f.read()

url = os.environ["PC_BRIDGE_URL"]
repo = os.path.dirname(os.path.abspath(__file__))
payload = json.dumps({"script": script})
r = subprocess.run(
    [sys.executable, os.path.join(repo, "bridge", "op_call.py"), "call", "shell_pwsh", payload],
    capture_output=True, text=True, timeout=(int(sys.argv[2]) // 1000) + 120 if len(sys.argv) > 2 else 300,
)
print(r.stdout, end="")
if r.stderr.strip():
    print(r.stderr, file=sys.stderr, end="")
try:
    out = json.loads(r.stdout)
    sys.exit(out.get("returncode", 99) if isinstance(out, dict) else 99)
except Exception:
    sys.exit(99)
