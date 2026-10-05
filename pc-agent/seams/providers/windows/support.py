"""Support tools for faster task execution.

- shell_pwsh: run PowerShell directly, without cmd.exe wrapping and its
  quoting pitfalls.
- batch: run up to 20 tool calls in a single roundtrip. One approval
  dialog covers every write action in the batch; per-tool dialogs are
  muted for the batch duration (restored afterwards).
"""
import subprocess

import events
import subproc
import toolcall
from seams.providers.windows import browser as _browser_mod
from seams.providers.windows import files as _files_mod
from seams.providers.windows import write as _write_mod
from seams.providers.windows.read import _resolve_cwd
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
    "shell_pwsh", "batch", "exec_background", "exec_cancel",
}

# Modules whose _approved gate is muted for the duration of a batch
# (the batch's own single approval covers every write). Patched on the
# provider modules so the mute lands in the functions' own globals.
_GATED_MODULES = (_write_mod, _files_mod, _browser_mod)


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def shell_pwsh(script: str, timeout_s: int = 60,
               cwd: str | None = None) -> dict:
    """Run a PowerShell script directly (no cmd.exe). Requires approval.

    cwd: optional working directory, must be inside the user profile.
    """
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be a non-empty string")
    if len(script) > 8000:
        raise ValueError("script must be 1-8000 chars")
    timeout_s = max(1, min(int(timeout_s), 300))
    run_cwd = _resolve_cwd(cwd)
    where = f" in {run_cwd}" if run_cwd else ""
    _approved("shell_pwsh",
              f"Run PowerShell (timeout {timeout_s}s){where}:\n{script[:500]}")
    try:
        returncode, stdout, stderr, timed_out = subproc.run_noinherit(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout_s, cwd=run_cwd,
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
    batch_id = events.append("BatchStarted", {
        "tool_count": len(plan),
        "tools": [n for n, _ in plan],
    }, session_id=toolcall._session_id())
    completed = denied = failed = 0
    try:
        for m in _GATED_MODULES:
            m._approved = lambda tool, summary: None
        results = []
        for name, args in plan:
            if name not in registry:
                results.append({"tool": name, "error": f"unknown tool: {name}"})
                failed += 1
                continue
            r = toolcall.call(name, args, write=False, caused_by=batch_id)
            err = r.get("error") if isinstance(r, dict) else None
            if err is None:
                completed += 1
            elif "denied" in str(err).lower():
                denied += 1
            else:
                failed += 1
            results.append({"tool": name, "result": r})
    finally:
        for m, fn in originals.items():
            m._approved = fn
    events.append("BatchCompleted", {
        "completed": completed,
        "denied": denied,
        "failed": failed,
    }, caused_by=batch_id, session_id=toolcall._session_id())
    return {"calls": len(plan), "results": results}


# ---- background command execution -----------------------------------------
import tempfile
import threading
import time
import uuid

_JOBS: dict = {}  # job_id -> {proc, output_path, started, timeout_s, done}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_S = 3600  # completed jobs kept for 1 hour


def _cleanup_jobs():
    """Remove completed jobs older than _JOB_TTL_S."""
    now = time.time()
    with _JOBS_LOCK:
        for jid in [k for k, v in _JOBS.items()
                    if v.get("done") and now - v["done"] > _JOB_TTL_S]:
            try:
                import os as _os
                _os.unlink(_JOBS[jid]["output_path"])
            except OSError:
                pass
            del _JOBS[jid]


def exec_background(command: str, timeout_s: int = 300,
                    cwd: str | None = None) -> dict:
    """Start a shell command in the background. Requires approval.

    cwd: optional working directory, must be inside the user profile.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    timeout_s = max(1, min(int(timeout_s), 3600))
    run_cwd = _resolve_cwd(cwd)
    where = f" in {run_cwd}" if run_cwd else ""
    _approved("exec_background",
              f"Run in background (timeout {timeout_s}s){where}:\n{command[:500]}")
    _cleanup_jobs()
    job_id = uuid.uuid4().hex[:12]
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".log",
                                      delete=False, encoding="utf-8",
                                      errors="replace")
    tmp_path = tmp.name
    tmp.close()
    out = open(tmp_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        command, shell=True, cwd=run_cwd,
        stdout=out, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "proc": proc, "output_path": tmp_path,
            "started": time.time(), "timeout_s": timeout_s,
            "done": None, "returncode": None, "cancelled": False,
            "cwd": run_cwd,
        }
    events.append("BackgroundJobStarted", {
        "job_id": job_id, "pid": proc.pid,
        "command": command[:200], "timeout_s": timeout_s,
        "cwd": run_cwd,
    }, session_id=toolcall._session_id())
    return {"job_id": job_id, "status": "started", "pid": proc.pid}


def _job_status(job: dict) -> tuple[str, int | None]:
    """Return (status, returncode). Updates job dict on completion."""
    if job.get("cancelled"):
        return "cancelled", job["returncode"]
    proc = job["proc"]
    rc = proc.poll()
    if rc is None:
        if time.time() - job["started"] > job["timeout_s"]:
            try:
                proc.kill()
            except OSError:
                pass
            job["done"] = time.time()
            job["returncode"] = -1
            return "timeout", -1
        return "running", None
    if job["done"] is None:
        job["done"] = time.time()
        job["returncode"] = rc
    return ("completed" if rc == 0 else "failed"), rc


def exec_status(job_id: str, offset: int = 0) -> dict:
    """Check on a background job. offset (bytes, default 0) enables
    incremental polling: output_from_offset holds bytes from offset
    onward, output_size the total — pass it back as the next offset."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise ValueError(f"unknown job_id: {job_id}")
    status, rc = _job_status(job)
    offset = max(0, int(offset))
    try:
        with open(job["output_path"], encoding="utf-8",
                   errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2000))
            tail = f.read()
            f.seek(min(offset, size))
            from_offset = f.read()
    except OSError:
        tail, from_offset, size = "", "", 0
    return {"job_id": job_id, "status": status, "returncode": rc,
            "output_tail": tail, "output_full_path": job["output_path"],
            "output_from_offset": from_offset, "output_size": size}


def _kill_tree(proc) -> None:
    """Kill a process and its whole child tree. On Windows, Popen.kill
    only kills the cmd.exe wrapper, leaving orphans; taskkill /T kills
    the tree. Falls back to Popen.kill when taskkill fails."""
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()  # ensure the direct child is gone regardless
    except OSError:
        pass


def exec_cancel(job_id: str) -> dict:
    """Kill a running background job and its whole process tree."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise ValueError(f"unknown job_id: {job_id}")
    proc = job["proc"]
    if proc.poll() is None:
        _kill_tree(proc)
        proc.wait()
        job["done"] = time.time()
        job["returncode"] = -9
        job["cancelled"] = True
    events.append("BackgroundJobCancelled", {"job_id": job_id},
                  session_id=toolcall._session_id())
    return {"job_id": job_id, "status": "cancelled"}
