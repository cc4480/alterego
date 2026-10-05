"""Audit-logged tool invocation, shared by server.py and tool_wrappers.py."""
from audit import log_event
import approval
import hooks
from tool_profiles import approval_tier, get_profile
from seams import registry

# File tools that support dry_run=True (used by plan permission mode).
_DRY_RUN_TOOLS = {"write_file", "edit_file", "delete_file",
                  "create_dir", "copy_file", "move_file"}


def _risk_snapshot(name):
    p = get_profile(name)
    return {"tier": approval_tier(name),
            "blast_radius": p["blast_radius"],
            "recoverability": p["recoverability"]}


def _plan_mode_result(name, args):
    """plan posture: no side effects. File tools run with dry_run=True;
    everything else returns a would-execute stub."""
    if name in _DRY_RUN_TOOLS:
        return None  # caller proceeds with dry_run injected
    return {"plan_mode": True, "would_execute": name, "args": args,
            "note": "plan posture: tool not executed (no side effects)"}


def call(name, args, write=False, rationale=None):
    """Run a tool with audit logging; errors become {error} payloads.

    The implementation function is resolved from the active provider via
    seams.registry (selected by PC_BRIDGE_PROVIDER) — callers name the
    tool, never the provider.

    rationale: optional agent-supplied reason, recorded verbatim.
    Every entry also carries the tool's risk snapshot (tier, blast radius,
    recoverability) — the decision rationale for the approval gate.

    Pipeline: resolve -> PreToolUse hooks -> [plan-mode short-circuit] ->
    execute -> PostToolUse hooks -> audit log.
    """
    fn = registry.resolve(name)  # THE SEAM: was passed in by the caller.
    risk = _risk_snapshot(name)

    # 1. PreToolUse hooks (may deny or rewrite args).
    try:
        allowed, args_or_reason = hooks.run_pre_hooks(name, dict(args))
    except Exception as e:  # noqa: BLE001 - hook infra must fail safe
        log_event(name, args, f"denied: hook infra error: {e}",
                  approved=False, rationale=rationale, risk=risk)
        return {"error": f"denied by hook infrastructure: {e}"}
    if not allowed:
        log_event(name, args, f"denied: {args_or_reason}",
                  approved=False, rationale=rationale, risk=risk)
        return {"error": f"denied by hook: {args_or_reason}"}
    args = args_or_reason

    # 2. plan posture: force dry-run, never execute side effects.
    plan_stub = None
    if write and approval.get_permission_mode() == "plan":
        plan_stub = _plan_mode_result(name, args)
        if plan_stub is None:
            args = dict(args, dry_run=True)

    try:
        result = plan_stub if plan_stub is not None else fn(**args)
        status = "ok"
        if write and approval.is_auto_approve():
            status = "ok (AUTO-APPROVED, no dialog shown)"
        if write and approval.get_permission_mode() == "plan":
            status = "ok (PLAN MODE, no side effects)"
        # 3. PostToolUse hooks (may log, verify, or replace result).
        try:
            result = hooks.run_post_hooks(name, args, result)
        except Exception as e:  # noqa: BLE001 - never lose the result
            log_event(name, args, f"warning: post-hook infra error: {e}",
                      rationale=rationale, risk=risk)
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
