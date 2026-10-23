#!/usr/bin/env python3
"""Call PC bridge shell_pwsh. Usage: pc.py <ps1-file> [timeout_s]"""
import json, subprocess, sys, os

script = open(sys.argv[1], encoding="utf-8").read()
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 55
args = json.dumps({"script": script, "timeout_s": timeout})
env = dict(os.environ, PC_BRIDGE_URL="https://pc.secscan.info")
r = subprocess.run(
    ["python3", "bridge/op_call.py", "call", "shell_pwsh", args],
    capture_output=True, text=True, env=env,
    cwd=os.path.expanduser("~/workspace/alterego"),
    timeout=timeout + 150)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
sys.exit(r.returncode)
