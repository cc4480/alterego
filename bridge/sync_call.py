#!/usr/bin/env python3
"""Run a PowerShell script file on the PC via the bridge shell_pwsh tool."""
import json, os, subprocess, sys

ps1_path = sys.argv[1]
with open(ps1_path, encoding="utf-8") as f:
    script = f.read()

bridge_dir = os.path.expanduser("~/workspace/alterego/bridge")
env = dict(os.environ, PC_BRIDGE_URL="https://pc.secscan.info")
args_json = json.dumps({"script": script})
p = subprocess.run(
    [sys.executable, "op_call.py", "call", "shell_pwsh", args_json],
    cwd=bridge_dir, env=env, capture_output=True, text=True, timeout=180)
sys.stdout.write(p.stdout)
sys.stderr.write(p.stderr)
sys.exit(p.returncode)
