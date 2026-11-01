"""Mock support tools: shell_pwsh (canned) and batch (in-process fan-out).

batch() executes sub-tools by resolving the active provider's function
directly (registry.resolve) — NOT through toolcall.call — so a batch does
not nest pipeline events. Sub-tool exceptions become {"tool","error"}
results, mirroring the Windows provider's behavior. The composite
BatchStarted/BatchCompleted events ARE emitted (they describe the batch
itself, not the sub-calls).
"""
import events
from seams.providers.mock import record


def _session_id():
    from toolcall import _session_id as sid  # lazy: avoids import cycle
    return sid()


def shell_pwsh(script: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Mock PowerShell: never executes anything. `echo <text>` returns the
    text; anything else returns canned output."""
    record("shell_pwsh", {"script": script, "timeout_s": timeout_s,
                          "cwd": cwd})
    stripped = script.strip()
    if stripped == "echo":
        stdout = ""
    elif stripped.startswith("echo "):
        stdout = stripped[5:]
    else:
        stdout = "mock pwsh output"
    return {"timed_out": False, "stdout": stdout, "stderr": "",
            "returncode": 0}


def batch(calls: list) -> dict:
    if not isinstance(calls, list) or not 1 <= len(calls) <= 20:
        raise ValueError("calls must be a list of 1-20 {tool, args} items")
    from seams import registry  # lazy: avoids import cycle
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
    record("batch", {"calls": [n for n, _ in plan]})
    batch_id = events.append("BatchStarted", {
        "tool_count": len(plan),
        "tools": [n for n, _ in plan],
    }, session_id=_session_id())
    completed = denied = failed = 0
    results = []
    for name, args in plan:
        # Unknown tool -> per-call error envelope (mirrors the Windows
        # provider); only malformed plans raise.
        try:
            fn = registry.resolve(name)
        except Exception as e:  # noqa: BLE001 - unknown tool -> error result
            results.append({"tool": name,
                            "error": f"unknown tool: {name}"})
            failed += 1
            continue
        try:
            r = fn(**args)
            completed += 1
            results.append({"tool": name, "result": r})
        except Exception as e:  # noqa: BLE001 - per-call error envelope
            err = f"{type(e).__name__}: {e}"
            if "denied" in err.lower():
                denied += 1
            else:
                failed += 1
            results.append({"tool": name, "error": err})
    events.append("BatchCompleted", {
        "completed": completed,
        "denied": denied,
        "failed": failed,
    }, caused_by=batch_id, session_id=_session_id())
    return {"calls": len(plan), "results": results}


_MOCK_OUTPUT = "mock output line 1\nmock output line 2\n"


def exec_background(command: str, timeout_s: int = 300,
                    cwd: str | None = None) -> dict:
    """Mock: pretend to start a background job."""
    record("exec_background", {"command": command, "timeout_s": timeout_s,
                               "cwd": cwd})
    return {"job_id": "mock-job-001", "status": "started", "pid": 4242}


def exec_status(job_id: str, offset: int = 0) -> dict:
    """Mock: pretend the job completed; honors offset for parity."""
    record("exec_status", {"job_id": job_id, "offset": offset})
    size = len(_MOCK_OUTPUT.encode("utf-8"))
    return {"job_id": job_id, "status": "completed", "returncode": 0,
            "output_tail": _MOCK_OUTPUT, "output_full_path": "/mock/job.log",
            "output_from_offset": _MOCK_OUTPUT[max(0, int(offset)):],
            "output_size": size}


def exec_cancel(job_id: str) -> dict:
    """Mock: pretend to cancel the job."""
    record("exec_cancel", {"job_id": job_id})
    return {"job_id": job_id, "status": "cancelled"}


def server_reload() -> dict:
    record("server_reload", {})
    return {"reloading": True, "supervisor_running": True,
            "reason": "mock", "error": None, "fix": None}


def supervisor_status() -> dict:
    record("supervisor_status", {})
    return {"running": True, "present": True, "supervisor_pid": 4242,
            "child_pid": 4243, "restarts": 0, "reloads": 0,
            "last_exit_code": None, "last_exit_ts": None,
            "storm_breaker": False, "heartbeat_age_s": 1.0,
            "updated_ts": 0, "port": 8765, "detail": "mock"}


def hot_reload(action: str) -> dict:
    record("hot_reload", {"action": action})
    if action not in ("status", "enable", "disable", "trigger"):
        raise ValueError("action must be status | enable | disable | trigger")
    return {"action": action, "enabled": action in ("enable", "trigger"),
            "watching": True, "last_change": None,
            "reloading": action == "trigger", "error": None, "fix": None}

# ---- Batch 6 (portmanteau slice) ----------------------------------------


def exec(operation: str, args: dict | None = None) -> dict:
    """Shell execution as one tool: shell | pwsh | background | status |
    cancel."""
    import portmanteau
    return portmanteau.dispatch("exec", operation, args)
