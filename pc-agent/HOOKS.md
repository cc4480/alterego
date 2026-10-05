# Bridge Hooks — PreToolUse / PostToolUse

Hooks let you inject custom policy into every tool call without modifying
tool code. Inspired by Claude Code's hook lifecycle.

## Concepts

- **PreToolUse** hooks run *before* a tool executes. They receive
  `(tool_name, args)` and return:
  - `None` or `"allow"` — proceed
  - `("deny", reason)` — block the tool; the reason goes to the operator
    and the audit log
  - `("modify", new_args)` — run the tool with `new_args` instead
- **PostToolUse** hooks run *after* a tool executes. They receive
  `(tool_name, args, result)` and return `None` (keep result) or a
  replacement result dict.
- Register per tool name, or `"*"` for all tools.
- Every hook has a timeout (default 5s). Timeouts fail safe:
  PreToolUse timeout → **deny**; PostToolUse timeout → logged, original
  result kept.

## Registering hooks

Hooks are registered in Python. The natural place is at the bottom of
`pc-agent/hooks.py`, or in a separate `pc-agent/hooks_local.py` that you
import from `hooks.py` (keeps your policy out of version control):

```python
from hooks import register_hook, PRE_TOOL_USE, POST_TOOL_USE
from hooks import deny_delete_on_drive, log_shell_commands

# Block deletes on D: (the old-HDD fallback drive)
register_hook(PRE_TOOL_USE, "delete_file", deny_delete_on_drive)

# Log every shell command to %APPDATA%/pc-mcp-bridge/shell_commands.log
register_hook(POST_TOOL_USE, "shell_exec", log_shell_commands)
register_hook(POST_TOOL_USE, "shell_pwsh", log_shell_commands)
```

## Writing your own

```python
from hooks import register_hook, PRE_TOOL_USE

def no_network_after_midnight(tool_name, args):
    from datetime import datetime
    hour = datetime.now().hour
    if tool_name in ("http_headers", "dns_query", "tls_info", "tcp_check"):
        if hour >= 0 and hour < 6:
            return "deny", "network recon blocked 00:00–06:00"
    return "allow"

register_hook(PRE_TOOL_USE, "*", no_network_after_midnight, timeout_s=2.0)
```

```python
from hooks import register_hook, POST_TOOL_USE

def redact_secrets_from_results(tool_name, args, result):
    # Example: strip anything that looks like a token from read results
    import json
    text = json.dumps(result)
    if "token" in text.lower():
        return {"warning": "result contained a token-like string; redacted",
                "tool": tool_name}
    return None  # keep original

register_hook(POST_TOOL_USE, "read_file", redact_secrets_from_results)
```

## Built-in examples

Defined in `hooks.py`, **not registered by default**:

| Hook | Event | Effect |
|---|---|---|
| `deny_delete_on_drive` | PreToolUse | Denies `delete_file` when path is on `D:\` |
| `log_shell_commands` | PostToolUse | Appends every `shell_exec`/`shell_pwsh` command to `shell_commands.log` |

## Notes

- Hooks run inside `toolcall.call`, so they apply to **every** tool,
  including `batch` sub-calls (each sub-call goes through `toolcall.call`).
- A hook that raises, or returns an unrecognized value, fails safe
  (PreToolUse → deny).
- Hook activity is written to the audit log with tool name `"hook"`.
- `list_hooks()` (in `hooks.py`) returns the registered hooks for
  inspection.
