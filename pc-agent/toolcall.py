"""Tool invocation pipeline, shared by server.py and tool_wrappers.py.

The typed event-sourced operation log (events.py) is the PRIMARY record:
every pipeline stage appends an event and nothing else is written. The
event write can never break the tool call — _emit() swallows all errors
(stderr + continue) and events.append() never raises.

Pipeline: resolve -> ToolCalled -> PreToolUse hooks -> HookEvaluated
(per hook) -> [plan-mode short-circuit] -> [ApprovalRequested] ->
execute -> PostToolUse hooks -> HookEvaluated (per hook) ->
ToolCompleted | ToolDenied | ToolFailed.

Causality: every stage event carries caused_by = the ToolCalled event_id.
call() accepts an optional caused_by (used by the batch tool so each
sub-call's chain points at the BatchStarted event).

Emergency rollback: EVENT_LOG_ENABLED=0 skips all event writes.

Honest Phase-2 note (carried over): ApprovalRequested's
dialog_shown/skipped_reason is a prediction from approval.request_approval's
skip logic, and dialog_result is inferred from the outcome (the tool ran =>
"yes"; PermissionError => "no" or "timeout"). The provider shows the actual
dialog.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import approval
import blob_store
import events
from events import _redact
import hooks
from tool_profiles import approval_tier, get_profile
from seams import registry

# File tools that support dry_run=True (used by plan permission mode).
_DRY_RUN_TOOLS = {"write_file", "edit_file", "delete_file",
                  "create_dir", "copy_file", "move_file"}

# The server's persisted session token (same file server.py uses). The
# token itself is never logged — only its fingerprint becomes session_id.
_SESSION_TOKEN_FILE = (
    Path(os.environ.get("APPDATA") or Path.home())
    / "pc-mcp-bridge" / "session_token")


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


def _session_id():
    """Operator session id: 'sess_' + sha256(token)[:12]. Never the token
    itself. None when no operator is paired (or the token file is gone)."""
    try:
        token = _SESSION_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not token:
        return None
    return "sess_" + hashlib.sha256(token.encode()).hexdigest()[:12]


def _emit(type, data, caused_by=None):
    """Append a typed event. NEVER raises: on failure logs to stderr and
    returns None so the tool pipeline continues.

    EVENT_LOG_ENABLED=0 skips event writes entirely (emergency rollback).
    """
    if os.environ.get("EVENT_LOG_ENABLED", "1") == "0":
        return None
    try:
        return events.append(type, data, caused_by=caused_by,
                             session_id=_session_id())
    except Exception as e:  # noqa: BLE001 - event write must never break calls
        print(f"[events] emit failed ({type}): {e}", file=sys.stderr)
        return None


def _event_value(obj):
    """Redact secrets, then blob-store values over the size threshold."""
    if isinstance(obj, dict):
        red = _redact(obj)
        return {k: blob_store.store_if_large(v) for k, v in red.items()}
    return blob_store.store_if_large(obj)


def _content_hash(obj):
    """sha256:<hex> of the canonical JSON of obj (full values, redacted)."""
    return ("sha256:" + hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest())


def _approval_preview(tool):
    """Predict the approval gate outcome WITHOUT showing anything.

    Mirrors approval.request_approval's skip logic (best-effort):
    returns (dialog_shown, skipped_reason)."""
    tier = approval_tier(tool)
    if approval.is_full_access():
        return False, "dontAsk"
    if (approval.get_permission_mode() == "acceptedits"
            and tool in approval.ACCEPT_EDITS_AUTO and tier != "always_ask"):
        return False, "acceptEdits"
    if tier == "always_ask":
        return True, None
    if approval.is_auto_approve():
        return False, "auto_approve"
    return True, None


def _emit_approval_requested(name, risk, dialog_result, caused_by):
    dialog_shown, skipped = _approval_preview(name)
    return _emit("ApprovalRequested", {
        "tier": risk["tier"],
        "dialog_shown": dialog_shown,
        "dialog_result": dialog_result,
        "skipped_reason": skipped,
        # The provider builds the dialog text; toolcall never sees it.
        "summary_shown": None,
    }, caused_by=caused_by)


def call(name, args, write=False, rationale=None, caused_by=None):
    """Run a tool; errors become {error} payloads. The event log is the
    primary record — ToolCalled/ToolCompleted/ToolDenied/ToolFailed (plus
    HookEvaluated and ApprovalRequested stage events) are the whole story.

    The implementation function is resolved from the active provider via
    seams.registry (selected by PC_BRIDGE_PROVIDER) — callers name the
    tool, never the provider.

    rationale: optional agent-supplied reason, recorded verbatim.
    Every entry also carries the tool's risk snapshot (tier, blast radius,
    recoverability) — the decision rationale for the approval gate.

    caused_by: optional parent event_id for composite calls (the batch
    tool passes its BatchStarted event_id so each sub-call's chain links
    up). Root calls leave it None.

    Pipeline: resolve -> PreToolUse hooks -> [plan-mode short-circuit] ->
    execute -> PostToolUse hooks -> typed events.
    """
    fn = registry.resolve(name)  # THE SEAM: was passed in by the caller.
    risk = _risk_snapshot(name)
    t_call = time.perf_counter()
    in_plan_mode = write and approval.get_permission_mode() == "plan"

    called_id = _emit("ToolCalled", {
        "tool": name,
        "args": _event_value(args if isinstance(args, dict) else {}),
        "args_hash": _content_hash(
            _redact(args if isinstance(args, dict) else {})),
        "risk": risk,
        "permission_mode": approval.get_permission_mode(),
        "rationale": rationale,
    }, caused_by=caused_by)

    # 1. PreToolUse hooks (may deny or rewrite args). Each evaluated
    # hook appends its own HookEvaluated event (hooks.py); toolcall only
    # records the phase outcome here.
    hook_error = None
    try:
        allowed, args_or_reason = hooks.run_pre_hooks(
            name, dict(args), caused_by=called_id)
    except Exception as e:  # noqa: BLE001 - hook infra must fail safe
        allowed, args_or_reason, hook_error = False, None, e
    if hook_error is not None:
        _emit("ToolDenied", {
            "tool": name, "denied_by": "hook_infra",
            "reason": "hook infra error: %s" % hook_error}, caused_by=called_id)
        return {"error": "denied by hook infrastructure: %s" % hook_error}
    if not allowed:
        reason = str(args_or_reason)
        _emit("ToolDenied", {
            "tool": name, "denied_by": "hook", "reason": reason},
            caused_by=called_id)
        return {"error": "denied by hook: %s" % reason}
    args = args_or_reason

    # 2. plan posture: force dry-run, never execute side effects.
    plan_stub = None
    if in_plan_mode:
        plan_stub = _plan_mode_result(name, args)
        if plan_stub is None:
            args = dict(args, dry_run=True)

    try:
        t_fn = time.perf_counter()
        result = plan_stub if plan_stub is not None else fn(**args)
        fn_ms = int((time.perf_counter() - t_fn) * 1000)
        if write and not in_plan_mode:
            # The provider ran the approval gate and the tool executed,
            # so approval was granted (dialog Yes, or posture-skipped).
            _emit_approval_requested(name, risk, "yes", called_id)
        # 3. PostToolUse hooks (may log, verify, or replace result).
        # Per-hook HookEvaluated events are appended by hooks.py.
        try:
            result = hooks.run_post_hooks(name, args, result,
                                          caused_by=called_id)
        except Exception as e:  # noqa: BLE001 - never lose the result
            print(f"[events] post-hook infra error ({name}): {e}",
                  file=sys.stderr)
        _emit("ToolCompleted", {
            "tool": name,
            "result_summary": str(result)[:200],
            "result_hash": _content_hash(
                _redact(result) if isinstance(result, dict) else result),
            "latency_ms": fn_ms,
            "plan_mode": in_plan_mode,
        }, caused_by=called_id)
        if name == "doctor" and isinstance(result, dict) \
                and "checks" in result and "summary" in result:
            # DoctorRun: the health-check outcome as a typed event,
            # caused_by the doctor ToolCalled event. Emitted here (not in
            # the provider) so it carries the session and the causal link.
            _emit("DoctorRun", {
                "checks": result["checks"],
                "summary": result["summary"],
            }, caused_by=called_id)
        return result
    except PermissionError as e:
        msg = str(e)
        # Providers raise PermissionError("denied by local approval ...")
        # when the on-PC dialog is denied or times out; anything else is a
        # best-effort classification (the reason string carries the truth).
        # Note: the provider's generic message ends "(or timed out)" — that
        # parenthetical alone is not evidence of an actual timeout.
        denied_by, dialog_result = "approval_dialog", "no"
        low = msg.lower()
        if "timed out" in low and "(or timed out)" not in low:
            denied_by, dialog_result = "approval_timeout", "timeout"
        if write and not in_plan_mode:
            _emit_approval_requested(name, risk, dialog_result, called_id)
        _emit("ToolDenied", {
            "tool": name, "denied_by": denied_by, "reason": msg,
            "hook_event_id": None}, caused_by=called_id)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - surface as tool error, never crash
        _emit("ToolFailed", {
            "tool": name,
            "error_type": type(e).__name__,
            "error_message": str(e)[:500],
            "latency_ms": int((time.perf_counter() - t_fn) * 1000),
        }, caused_by=called_id)
        return {"error": "%s: %s" % (type(e).__name__, e)}
