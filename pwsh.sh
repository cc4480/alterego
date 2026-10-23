#!/usr/bin/env bash
# Wrapper: run a PowerShell command on Carlos's PC via the AlterEgo bridge.
# Usage: pwsh.sh '<powershell command>'
# Prints the tool result text.
set -u
export PC_BRIDGE_URL="https://pc.secscan.info"
cd /home/hatch/workspace/alterego
python3 bridge/op_call.py call shell_pwsh "$(python3 -c 'import json,sys; print(json.dumps({"script": sys.argv[1]}))' "$1")"
