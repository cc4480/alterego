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


def shell_pwsh(script: str, timeout_s: int = 60) -> dict:
    """Mock PowerShell: never executes anything. `echo <text>` returns the
    text; anything else returns canned output."""
    record("shell_pwsh", {"script": script, "timeout_s": timeout_s})
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


def exec_background(command: str, timeout_s: int = 300) -> dict:
    """Mock: pretend to start a background job."""
    record("exec_background", {"command": command, "timeout_s": timeout_s})
    return {"job_id": "mock-job-001", "status": "started", "pid": 4242}


def exec_status(job_id: str) -> dict:
    """Mock: pretend the job completed."""
    record("exec_status", {"job_id": job_id})
    return {"job_id": job_id, "status": "completed", "returncode": 0,
            "output_tail": "mock output", "output_full_path": "/mock/job.log"}


def exec_cancel(job_id: str) -> dict:
    """Mock: pretend to cancel the job."""
    record("exec_cancel", {"job_id": job_id})
    return {"job_id": job_id, "status": "cancelled"}
