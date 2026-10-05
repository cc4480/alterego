"""Background command execution tool wrappers.

Split from tool_wrappers.py to keep files under 300 lines.
Each wrapper delegates to toolcall.call for audit logging.
"""
import toolcall


def exec_background(command: str, timeout_s: int = 300) -> dict:
    """Start a long-running shell command in the background. Returns a
    job_id for polling with exec_status or killing with exec_cancel.
    Requires on-PC approval."""
    return toolcall.call("exec_background",
                         {"command": command, "timeout_s": timeout_s},
                         write=True)


def exec_status(job_id: str) -> dict:
    """Check on a background job started with exec_background."""
    return toolcall.call("exec_status", {"job_id": job_id})


def exec_cancel(job_id: str) -> dict:
    """Kill a running background job started with exec_background."""
    return toolcall.call("exec_cancel", {"job_id": job_id}, write=True)
