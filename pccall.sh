#!/bin/bash
# Usage: pccall.sh <pwsh-script>
# Runs a PowerShell script on Carlos's PC via the AlterEgo bridge.
set -euo pipefail
cd "$(dirname "$0")"
export PC_BRIDGE_URL="https://pc.secscan.info"
SCRIPT="$1"
python3 bridge/op_call.py call shell_pwsh "$(python3 -c 'import json,sys; print(json.dumps({"script": sys.argv[1]}))' "$SCRIPT")"
