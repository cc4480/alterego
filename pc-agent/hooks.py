"""Hook lifecycle: PreToolUse / PostToolUse extensibility.

Hooks are Python callables registered per tool name (or "*" for all tools).

PreToolUse hooks run BEFORE the tool executes. Signature: fn(tool_name, args).
Return one of:
  - None or "allow"        -> proceed with (possibly modified) args
  - ("deny", reason)       -> block the tool; reason surfaced to the operator
  - ("modify", new_args)    -> run the tool with new_args instead
If ANY PreToolUse hook denies, the tool does not run.

PostToolUse hooks run AFTER the tool executes. Signature: fn(tool_name, args, result).
Return None to keep the result, or a replacement result dict.

Every hook has a configurable timeout (default 5s). Timeouts fail safe:
  PreToolUse timeout  -> deny ("hook '<name>' timed out")
  PostToolUse timeout -> logged, original result kept.

API:
  register_hook(event, tool_pattern, fn, timeout_s=5.0, name=None)
  run_pre_hooks(tool_name, args)  -> (allowed: bool, args_or_reason)
  run_post_hooks(tool_name, args, result) -> result

tool_pattern is an exact tool name or "*" for all tools.

Built-in examples (defined here, NOT registered by default — see HOOKS.md):
  deny_delete_on_drive, log_shell_commands
"""
import threading
from audit import log_event

PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"

DEFAULT_TIMEOUT_S = 5.0

_hooks: dict[str, list[dict]] = {PRE_TOOL_USE: [], POST_TOOL_USE: []}


def register_hook(event: str, tool_pattern: str, fn,
                  timeout_s: float = DEFAULT_TIMEOUT_S,
                  name: str | None = None) -> None:
    """Register a hook callable.

    event: "PreToolUse" or "PostToolUse"
    tool_pattern: exact tool name, or "*" for all tools
    fn: PreToolUse -> fn(tool_name, args); PostToolUse -> fn(tool_name, args, result)
    timeout_s: max seconds the hook may run before failing safe
    name: human-readable label for logs (defaults to fn.__name__)
    """
    if event not in _hooks:
        raise ValueError(f"unknown hook event: {event!r}")
    _hooks[event].append({
        "pattern": tool_pattern,
        "fn": fn,
        "timeout": timeout_s,
        "name": name or getattr(fn, "__name__", "hook"),
    })


def _matches(pattern: str, tool_name: str) -> bool:
    return pattern == "*" or pattern == tool_name


def _run_with_timeout(fn, args: tuple, timeout_s: float):
    """Run fn(*args) in a thread; return (completed, result_or_exception)."""
    outcome: dict = {}

    def _target():
        try:
            outcome["result"] = fn(*args)
        except Exception as exc:  # noqa: BLE001 - hook errors fail safe
            outcome["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return False, TimeoutError(f"hook timed out after {timeout_s}s")
    if "error" in outcome:
        return True, outcome["error"]
    return True, outcome.get("result")


def run_pre_hooks(tool_name: str, args: dict) -> tuple[bool, object]:
    """Run all matching PreToolUse hooks.

    Returns (True, final_args) if allowed, (False, reason) if denied.
    """
    current_args = dict(args)
    for h in _hooks[PRE_TOOL_USE]:
        if not _matches(h["pattern"], tool_name):
            continue
        completed, out = _run_with_timeout(
            h["fn"], (tool_name, current_args), h["timeout"])
        if not completed:
            reason = f"PreToolUse hook '{h['name']}' timed out"
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"deny: {reason}")
            return False, reason
        if isinstance(out, Exception):
            reason = f"PreToolUse hook '{h['name']}' errored: {out}"
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"deny: {reason}")
            return False, reason
        if out is None or out == "allow":
            continue
        if isinstance(out, tuple) and len(out) == 2:
            action, payload = out
            if action == "deny":
                reason = str(payload)
                log_event("hook", {"tool": tool_name, "hook": h["name"]},
                          f"deny: {reason}")
                return False, reason
            if action == "modify" and isinstance(payload, dict):
                current_args = payload
                continue
        # Unrecognized return: fail safe (deny)
        reason = (f"PreToolUse hook '{h['name']}' returned unrecognized "
                  f"value: {out!r}")
        log_event("hook", {"tool": tool_name, "hook": h["name"]},
                  f"deny: {reason}")
        return False, reason
    return True, current_args


def run_post_hooks(tool_name: str, args: dict, result: dict) -> dict:
    """Run all matching PostToolUse hooks. Timeouts/errors are logged;
    the original result is kept."""
    current = result
    for h in _hooks[POST_TOOL_USE]:
        if not _matches(h["pattern"], tool_name):
            continue
        completed, out = _run_with_timeout(
            h["fn"], (tool_name, args, current), h["timeout"])
        if not completed:
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      "warning: PostToolUse hook timed out, result kept")
            continue
        if isinstance(out, Exception):
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"warning: PostToolUse hook errored ({out}), result kept")
            continue
        if isinstance(out, dict):
            current = out  # hook replaced the result
    return current


def list_hooks() -> dict:
    """Registered hooks per event (for inspection, not execution)."""
    return {
        event: [{"pattern": h["pattern"], "name": h["name"],
                 "timeout_s": h["timeout"]} for h in lst]
        for event, lst in _hooks.items()
    }


# ---- built-in examples (NOT registered by default) -------------------------

def deny_delete_on_drive(tool_name: str, args: dict):
    """Example PreToolUse hook: deny delete_file when the path is on D:.

    Register with:
        register_hook("PreToolUse", "delete_file", deny_delete_on_drive)
    """
    path = str(args.get("path", ""))
    if path[:3].upper().replace("/", "\\") == "D:\\" or \
            path.upper().startswith("D:"):
        return "deny", f"deletes on D: are blocked by policy (path: {path})"
    return "allow"


def log_shell_commands(tool_name: str, args: dict, result: dict):
    """Example PostToolUse hook: append every shell command to a log file.

    Register with:
        register_hook("PostToolUse", "shell_exec", log_shell_commands)
        register_hook("PostToolUse", "shell_pwsh", log_shell_commands)
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import os
    cmd = args.get("command", args.get("script", "<no command>"))
    log_file = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) \
        / "pc-mcp-bridge" / "shell_commands.log"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} "
                    f"[{tool_name}] {cmd}\n")
    except OSError:
        pass
    return None
