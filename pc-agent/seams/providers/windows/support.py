"""Support tools for faster task execution.

- shell_pwsh: run PowerShell directly, without cmd.exe wrapping and its
  quoting pitfalls.
- batch: run up to 20 tool calls in a single roundtrip. One approval
  dialog covers every write action in the batch; per-tool dialogs are
  muted for the batch duration (restored afterwards).
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _partition_batch(plan, resource_map):
    """Group plan indices into chains that must run sequentially.

    Calls sharing a RESOURCE_MAP resource serialize in plan order;
    unlisted calls each get their own chain and run concurrently.
    Returns a list of chains (lists of plan indices)."""
    chains = []
    by_resource = {}
    for i, (name, _args) in enumerate(plan):
        resource = resource_map.get(name)
        if resource is None:
            chains.append([i])
        else:
            chain = by_resource.get(resource)
            if chain is None:
                chain = by_resource[resource] = []
                chains.append(chain)
            chain.append(i)
    return chains


def _run_batch_call(name, args, registry, batch_id):
    """One sub-call, exactly as the sequential batch ran it.

    Returns (entry, kind) where kind is completed/denied/failed."""
    if name not in registry:
        return {"tool": name, "error": f"unknown tool: {name}"}, "failed"
    r = toolcall.call(name, args, write=False, caused_by=batch_id)
    err = r.get("error") if isinstance(r, dict) else None
    if err is None:
        kind = "completed"
    elif "denied" in str(err).lower():
        kind = "denied"
    else:
        kind = "failed"
    return {"tool": name, "result": r}, kind


def _run_batch_chain(chain, plan, registry, batch_id):
    """Run one chain sequentially; returns [(index, entry, kind)]."""
    out = []
    for i in chain:
        name, args = plan[i]
        entry, kind = _run_batch_call(name, args, registry, batch_id)
        out.append((i, entry, kind))
    return out


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
    from jobs import RESOURCE_MAP  # lazy: jobs never imports this module
    completed = denied = failed = 0
    results = [None] * len(plan)
    try:
        for m in _GATED_MODULES:
            m._approved = lambda tool, summary: None
        chains = _partition_batch(plan, RESOURCE_MAP)
        with ThreadPoolExecutor(max_workers=min(len(chains), 8)) as ex:
            futs = [ex.submit(_run_batch_chain, chain, plan, registry,
                              batch_id)
                    for chain in chains]
            for fut in as_completed(futs):
                for i, entry, kind in fut.result():
                    results[i] = entry
                    if kind == "completed":
                        completed += 1
                    elif kind == "denied":
                        denied += 1
                    else:
                        failed += 1
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


# ---- supervisor / hot reload (Batch 1) ---------------------------------
def _supervisor_data_dir():
    try:
        from auth import app_dir
        return app_dir()
    except Exception:
        import os
        from pathlib import Path
        return Path(os.environ.get("APPDATA") or Path.home()) / "pc-mcp-bridge"


def server_reload() -> dict:
    """Restart the bridge server via the supervisor. Refuses when no
    supervisor is running, so the bridge is never darkened with nobody
    to bring it back."""
    import supervisor as _sup
    data = _supervisor_data_dir()
    st = _sup.supervisor_status(data)
    if not st.get("running"):
        return {"reloading": False, "supervisor_running": False,
                "reason": None,
                "error": "no supervisor running",
                "fix": "Start supervised mode: .\\bridge-admin.ps1 supervise"}
    _approved("server_reload",
              "Restart the bridge server via the supervisor "
              "(fresh code, same tunnel and tokens).")
    _sup.request_reload(data, reason="server_reload tool")
    return {"reloading": True, "supervisor_running": True,
            "reason": "server_reload tool", "error": None, "fix": None}


def supervisor_status() -> dict:
    """Supervisor health. Read-only, no approval."""
    import supervisor as _sup
    return _sup.supervisor_status(_supervisor_data_dir())


def hot_reload(action: str) -> dict:
    """Hot reload: status | enable | disable | trigger."""
    import hot_reload as _hr
    import supervisor as _sup
    data = _supervisor_data_dir()
    if action == "status":
        last_change = None
        try:
            import json
            last_change = json.loads(
                _sup.state_path(data).read_text(
                    encoding="utf-8")).get("last_change")
        except Exception:
            last_change = None
        return {"action": "status",
                "enabled": _hr.hot_reload_enabled(data),
                "watching": _sup.supervisor_status(data).get("running",
                                                            False),
                "last_change": last_change,
                "reloading": False, "error": None, "fix": None}
    if action not in ("enable", "disable", "trigger"):
        raise ValueError("action must be status | enable | disable | trigger")
    _approved("hot_reload", f"Hot reload: {action}")
    if action == "enable":
        _hr.set_hot_reload(data, True)
        return {"action": "enable", "enabled": True, "watching": True,
                "last_change": None, "reloading": False,
                "error": None, "fix": None}
    if action == "disable":
        _hr.set_hot_reload(data, False)
        return {"action": "disable", "enabled": False, "watching": False,
                "last_change": None, "reloading": False,
                "error": None, "fix": None}
    st = _sup.supervisor_status(data)
    if not st.get("running"):
        return {"action": "trigger", "enabled":
                _hr.hot_reload_enabled(data), "watching": False,
                "last_change": None, "reloading": False,
                "error": "no supervisor running",
                "fix": "Start supervised mode: .\\bridge-admin.ps1 supervise"}
    _sup.request_reload(data, reason="hot_reload trigger")
    return {"action": "trigger",
            "enabled": _hr.hot_reload_enabled(data), "watching": True,
            "last_change": None, "reloading": True,
            "error": None, "fix": None}

# ---- Batch 6 (portmanteau slice) ----------------------------------------
# No _approved here and no muting: dispatch goes through toolcall.call so
# the member's own provider-side gate fires once at the member tier.


def exec(operation: str, args: dict | None = None) -> dict:
    """Shell execution as one tool: shell | pwsh | background | status |
    cancel."""
    import portmanteau
    return portmanteau.dispatch("exec", operation, args)
