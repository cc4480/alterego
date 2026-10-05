"""Command-level allow/deny rules for shell tools.

Rules live in %APPDATA%/pc-mcp-bridge/command_rules.json:
    {"allow": ["git *", "npm test"], "deny": ["rm -rf *", "format *"]}

Deny patterns are checked first (deny wins). Then allow patterns.
If no rules file exists, everything is allowed (backward compat).
Patterns use fnmatch (* wildcards), matched case-insensitively against
the normalized command string.

Enforced via a PreToolUse hook on shell_exec and shell_pwsh — see
register() below, auto-registered from hooks.py.
"""
import fnmatch
import json
import os
from pathlib import Path


def rules_path() -> Path:
    base = Path(os.environ.get("APPDATA", os.path.expanduser("~")))
    return base / "pc-mcp-bridge" / "command_rules.json"


def load_rules() -> dict:
    """Read the rules file. Returns {"allow": [...], "deny": [...]}.

    Missing file or invalid JSON -> empty rules (allow everything).
    """
    try:
        raw = rules_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"allow": [], "deny": []}
        allow = data.get("allow", []) or []
        deny = data.get("deny", []) or []
        return {"allow": [str(p) for p in allow],
                "deny": [str(p) for p in deny]}
    except (OSError, ValueError):
        return {"allow": [], "deny": []}


def _normalize(cmd: str) -> str:
    """Collapse whitespace and lowercase for matching."""
    return " ".join(str(cmd).split()).lower()


def check_command(cmd: str) -> tuple[bool, str]:
    """Check a command against the rules.

    Returns (allowed, reason). Deny patterns win over allow patterns.
    No rules file -> (True, "no rules configured").
    """
    rules = load_rules()
    if not rules["allow"] and not rules["deny"]:
        return True, "no rules configured"
    norm = _normalize(cmd)
    for pattern in rules["deny"]:
        if fnmatch.fnmatch(norm, _normalize(pattern)):
            return False, (f"command denied by rule: {pattern!r} "
                           f"matched {cmd[:80]!r}")
    if rules["allow"]:
        for pattern in rules["allow"]:
            if fnmatch.fnmatch(norm, _normalize(pattern)):
                return True, f"command allowed by rule: {pattern!r}"
        return False, (f"command not in allow list: {cmd[:80]!r} "
                       "(allow list is non-empty, default deny)")
    return True, "no deny rule matched"


def _pre_tool_use_hook(tool_name: str, args: dict):
    """PreToolUse hook: deny shell commands matching deny patterns."""
    cmd = args.get("command", args.get("script", ""))
    if not cmd:
        return "allow"
    allowed, reason = check_command(str(cmd))
    if not allowed:
        return "deny", reason
    return "allow"


def register() -> None:
    """Register the command-rules PreToolUse hook for shell tools."""
    import hooks
    hooks.register_hook("PreToolUse", "shell_exec", _pre_tool_use_hook,
                        name="command_rules")
    hooks.register_hook("PreToolUse", "shell_pwsh", _pre_tool_use_hook,
                        name="command_rules")
