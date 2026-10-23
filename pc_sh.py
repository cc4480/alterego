#!/usr/bin/env python3
"""Run a shell_pwsh command on the PC via the AlterEgo bridge. Reads the
command from a file, emits the raw result JSON."""
import json, os, subprocess, sys

CMD_FILE = sys.argv[1]
with open(CMD_FILE) as f:
    command = f.read()

args = json.dumps({"script": command})
env = dict(os.environ, PC_BRIDGE_URL="https://pc.secscan.info")
out = subprocess.run(
    ["python3", "bridge/op_call.py", "call", "shell_pwsh", args],
    capture_output=True, text=True, cwd=os.path.expanduser("~/workspace/alterego"),
    env=env, timeout=600)
print("STDOUT:", out.stdout)
print("STDERR:", out.stderr)
print("RC:", out.returncode)
