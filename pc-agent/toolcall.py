"""Audit-logged tool invocation, shared by server.py and tool_wrappers.py."""
from audit import log_event
import approval
from tool_profiles import approval_tier, get_profile


def _risk_snapshot(name):
    p = get_profile(name)
    return {"tier": approval_tier(name),
            "blast_radius": p["blast_radius"],
            "recoverability": p["recoverability"]}


def call(name, fn, args, write=False, rationale=None):
    """Run a tool with audit logging; errors become {error} payloads.

    rationale: optional agent-supplied reason, recorded verbatim.
    Every entry also carries the tool's risk snapshot (tier, blast radius,
    recoverability) — the decision rationale for the approval gate.
    """
    risk = _risk_snapshot(name)
    try:
        result = fn(**args)
        status = "ok"
        if write and approval.is_auto_approve():
            status = "ok (AUTO-APPROVED, no dialog shown)"
        log_event(name, args, status, approved=True if write else None,
                  rationale=rationale, risk=risk)
        return result
    except PermissionError as e:
        log_event(name, args, f"denied: {e}", approved=False,
                  rationale=rationale, risk=risk)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface as tool error, never crash
        log_event(name, args, f"error: {type(e).__name__}: {e}",
                  rationale=rationale, risk=risk)
        return {"error": f"{type(e).__name__}: {e}"}
