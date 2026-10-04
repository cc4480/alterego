"""Audit-logged tool invocation, shared by server.py and tool_wrappers.py."""
from audit import log_event
import approval


def call(name, fn, args, write=False):
    """Run a tool with audit logging; errors become {error} payloads."""
    try:
        result = fn(**args)
        status = "ok"
        if write and approval.is_auto_approve():
            status = "ok (AUTO-APPROVED, no dialog shown)"
        log_event(name, args, status, approved=True if write else None)
        return result
    except PermissionError as e:
        log_event(name, args, f"denied: {e}", approved=False)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface as tool error, never crash
        log_event(name, args, f"error: {type(e).__name__}: {e}")
        return {"error": f"{type(e).__name__}: {e}"}
