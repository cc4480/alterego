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

Every evaluated hook appends one HookEvaluated event (events.py) with its
own name, decision, and latency — one event per hook, not per phase.
caused_by links the event to the tool call that triggered it.

API:
  register_hook(event, tool_pattern, fn, timeout_s=5.0, name=None)
  run_pre_hooks(tool_name, args, caused_by=None)  -> (allowed, args_or_reason)
  run_post_hooks(tool_name, args, result, caused_by=None) -> result

tool_pattern is an exact tool name or "*" for all tools.

Built-in examples (defined here, NOT registered by default — see HOOKS.md):
  deny_delete_on_drive, log_shell_commands
"""
import threading
import time
from audit import log_event
import events

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


def _emit_hook(phase: str, hook_name: str, decision: str, reason,
               latency_ms: int, timed_out: bool, caused_by=None) -> None:
    """Append one HookEvaluated event for a single hook evaluation.

    Never raises: events.append is write-only-safe, so a broken event log
    can never break a tool call (dual-write guarantee)."""
    events.append("HookEvaluated", {
        "phase": phase,
        "hook_name": hook_name,
        "decision": decision,
        "reason": reason,
        "latency_ms": latency_ms,
        "timed_out": timed_out,
    }, caused_by=caused_by)


def run_pre_hooks(tool_name: str, args: dict,
                  caused_by=None) -> tuple[bool, object]:
    """Run all matching PreToolUse hooks.

    Returns (True, final_args) if allowed, (False, reason) if denied.
    One HookEvaluated event is appended per hook evaluated.
    """
    current_args = dict(args)
    for h in _hooks[PRE_TOOL_USE]:
        if not _matches(h["pattern"], tool_name):
            continue
        t0 = time.perf_counter()
        completed, out = _run_with_timeout(
            h["fn"], (tool_name, current_args), h["timeout"])
        ms = int((time.perf_counter() - t0) * 1000)
        if not completed:
            reason = f"PreToolUse hook '{h['name']}' timed out"
            _emit_hook(PRE_TOOL_USE, h["name"], "deny", reason, ms,
                       True, caused_by)
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"deny: {reason}")
            return False, reason
        if isinstance(out, Exception):
            reason = f"PreToolUse hook '{h['name']}' errored: {out}"
            _emit_hook(PRE_TOOL_USE, h["name"], "deny", reason, ms,
                       False, caused_by)
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"deny: {reason}")
            return False, reason
        if out is None or out == "allow":
            _emit_hook(PRE_TOOL_USE, h["name"], "allow", None, ms,
                       False, caused_by)
            continue
        if isinstance(out, tuple) and len(out) == 2:
            action, payload = out
            if action == "deny":
                reason = str(payload)
                _emit_hook(PRE_TOOL_USE, h["name"], "deny", reason, ms,
                           False, caused_by)
                log_event("hook", {"tool": tool_name, "hook": h["name"]},
                          f"deny: {reason}")
                return False, reason
            if action == "modify" and isinstance(payload, dict):
                _emit_hook(PRE_TOOL_USE, h["name"], "modify", None, ms,
                           False, caused_by)
                current_args = payload
                continue
        # Unrecognized return: fail safe (deny)
        reason = (f"PreToolUse hook '{h['name']}' returned unrecognized "
                  f"value: {out!r}")
        _emit_hook(PRE_TOOL_USE, h["name"], "deny", reason, ms,
                   False, caused_by)
        log_event("hook", {"tool": tool_name, "hook": h["name"]},
                  f"deny: {reason}")
        return False, reason
    return True, current_args


def run_post_hooks(tool_name: str, args: dict, result: dict,
                   caused_by=None) -> dict:
    """Run all matching PostToolUse hooks. Timeouts/errors are logged;
    the original result is kept. One HookEvaluated event per hook."""
    current = result
    for h in _hooks[POST_TOOL_USE]:
        if not _matches(h["pattern"], tool_name):
            continue
        t0 = time.perf_counter()
        completed, out = _run_with_timeout(
            h["fn"], (tool_name, args, current), h["timeout"])
        ms = int((time.perf_counter() - t0) * 1000)
        if not completed:
            reason = (f"PostToolUse hook '{h['name']}' timed out, "
                      "result kept")
            _emit_hook(POST_TOOL_USE, h["name"], "allow", reason, ms,
                       True, caused_by)
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      "warning: PostToolUse hook timed out, result kept")
            continue
        if isinstance(out, Exception):
            reason = (f"PostToolUse hook '{h['name']}' errored ({out}), "
                      "result kept")
            _emit_hook(POST_TOOL_USE, h["name"], "allow", reason, ms,
                       False, caused_by)
            log_event("hook", {"tool": tool_name, "hook": h["name"]},
                      f"warning: PostToolUse hook errored ({out}), result kept")
            continue
        if isinstance(out, dict):
            _emit_hook(POST_TOOL_USE, h["name"], "modify",
                       "hook replaced the result", ms, False, caused_by)
            current = out  # hook replaced the result
        else:
            _emit_hook(POST_TOOL_USE, h["name"], "allow", None, ms,
                       False, caused_by)
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
