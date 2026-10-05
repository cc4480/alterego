"""Append-only local audit log. Every tool call is recorded here."""
import json
from datetime import datetime, timezone

from auth import app_dir

AUDIT_FILE = "audit.log"
_REDACT_KEYS = ("token", "secret", "password", "passwd", "pwd", "key")


def _redact(args):
    if not isinstance(args, dict):
        return args
    return {
        k: ("<redacted>" if any(s in k.lower() for s in _REDACT_KEYS) else v)
        for k, v in args.items()
    }


def log_event(tool, args, result, approved=None, rationale=None, risk=None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": _redact(args),
        "approved": approved,  # True/False for write tools, None for reads
        "result": result,
    }
    if risk is not None:
        entry["risk"] = risk  # why the approval tier was what it was
    if rationale:
        entry["rationale"] = rationale  # agent's stated reason, if given
    p = app_dir() / AUDIT_FILE
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
