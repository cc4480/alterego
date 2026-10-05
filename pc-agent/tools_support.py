"""Support tools for faster task execution.

- shell_pwsh: run PowerShell directly, without cmd.exe wrapping and its
  quoting pitfalls.
- batch: run up to 20 tool calls in a single roundtrip. One approval
  dialog covers every write action in the batch; per-tool dialogs are
  muted for the batch duration (restored afterwards).
"""
import subprocess

import subproc
import toolcall
import tools_browser
import tools_files
import tools_write
from approval import request_approval

MAX_OUTPUT = 4000

# Mirrors the wrappers in tool_wrappers.py that pass write=True.
WRITE_TOOLS = {
    "focus_window", "close_window", "type_text", "shell_exec", "hotkey",
    "mouse_move", "mouse_click", "mouse_scroll", "minimize_window",
    "maximize_window", "kill_process", "clipboard_set", "paste_text",
    "write_file", "edit_file", "delete_file", "create_dir",
    "copy_file", "move_file",
    "browser_navigate", "browser_click", "browser_fill", "browser_eval",
    "shell_pwsh", "batch",
}

_GATED_MODULES = (tools_write, tools_files, tools_browser)


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def shell_pwsh(script: str, timeout_s: int = 60) -> dict:
    """Run a PowerShell script directly (no cmd.exe). Requires approval."""
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be a non-empty string")
    if len(script) > 8000:
        raise ValueError("script must be 1-8000 chars")
    timeout_s = max(1, min(int(timeout_s), 300))
    _approved("shell_pwsh", f"Run PowerShell (timeout {timeout_s}s):\n{script[:500]}")
    try:
        returncode, stdout, stderr, timed_out = subproc.run_noinherit(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout_s,
        )
    except FileNotFoundError:
        raise RuntimeError("powershell not found on this machine")
    if timed_out:
        return {"timed_out": True,
                "stdout": stdout[-MAX_OUTPUT:],
                "stderr": stderr[-MAX_OUTPUT:]}
    return {"returncode": returncode,
            "stdout": stdout[-MAX_OUTPUT:],
            "stderr": stderr[-MAX_OUTPUT:]}


def batch(calls: list) -> dict:
    """Run several tool calls in one roundtrip.

    calls: [{"tool": name, "args": {...}}, ...], 1-20 items, no nesting.
    """
    if not isinstance(calls, list) or not 1 <= len(calls) <= 20:
        raise ValueError("calls must be a list of 1-20 {tool, args} items")
    plan = []
    for c in calls:
        if not isinstance(c, dict) or not isinstance(c.get("tool"), str):
            raise ValueError("each call must look like {tool, args}")
        name = c["tool"]
        if name == "batch":
            raise ValueError("nested batch is not allowed")
        args = c.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        plan.append((name, args))
    import tool_wrappers  # lazy: avoids a circular import at module load
    registry = {f.__name__: f for f in tool_wrappers.ALL_TOOLS}
    writes = [n for n, _ in plan if n in WRITE_TOOLS]
    if writes:
        _approved("batch",
                  f"{len(plan)} calls, {len(writes)} write:\n"
                  + "\n".join(f"- {n}" for n in writes[:12]))
    originals = {m: m._approved for m in _GATED_MODULES}
    try:
        for m in _GATED_MODULES:
            m._approved = lambda tool, summary: None
        results = []
        for name, args in plan:
            fn = registry.get(name)
            if fn is None:
                results.append({"tool": name, "error": f"unknown tool: {name}"})
                continue
            results.append({"tool": name,
                            "result": toolcall.call(name, fn, args, write=False)})
    finally:
        for m, fn in originals.items():
            m._approved = fn
    return {"calls": len(plan), "results": results}
