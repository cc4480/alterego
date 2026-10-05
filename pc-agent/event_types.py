"""Schema validators for the event-sourced operation log (design doc §3).

Each validator takes an event's `data` dict and returns a list of error
strings (empty = valid). Checks are intentionally shallow: required
fields plus key enums. Deep semantic validation is a later-phase concern.
"""


def _req(data, *fields):
    return [f"missing required field: {f!r}" for f in fields if f not in data]


def _enum(data, field, allowed):
    v = data.get(field)
    if v is not None and v not in allowed:
        return [f"field {field!r} must be one of {sorted(allowed)}; "
                f"got {v!r}"]
    return []


def _v_session_paired(d):
    return _req(d, "token_fingerprint", "pairing_method")


def _v_session_revoked(d):
    return (_req(d, "reason")
            + _enum(d, "reason", {"logout", "token_deleted",
                                  "server_restart"}))


def _v_pairing_denied(d):
    return _req(d, "reason")


def _v_tool_called(d):
    return _req(d, "tool", "args", "args_hash", "risk", "permission_mode")


def _v_hook_evaluated(d):
    # One event per hook evaluated (emitted by hooks.py); hook_name is
    # always set on current events. Null hook_name survives in the schema
    # only for legacy (pre-Phase-2) aggregate per-phase events.
    return (_req(d, "phase", "decision", "latency_ms", "timed_out")
            + _enum(d, "phase", {"PreToolUse", "PostToolUse"})
            + _enum(d, "decision", {"allow", "deny", "modify"}))


def _v_approval_requested(d):
    # skipped_reason gains "plan": no dialog is shown under the plan
    # posture (extension of the design-doc enum).
    return (_req(d, "tier", "dialog_shown", "dialog_result")
            + _enum(d, "dialog_result", {"yes", "no", "timeout", "error"})
            + _enum(d, "skipped_reason",
                    {"dontAsk", "acceptEdits", "auto_approve", "plan"}))


def _v_tool_completed(d):
    return _req(d, "tool", "result_summary", "result_hash", "latency_ms")


def _v_tool_denied(d):
    return (_req(d, "tool", "denied_by", "reason")
            + _enum(d, "denied_by", {"hook", "approval_dialog",
                                     "approval_timeout", "permission_mode",
                                     "hook_infra"}))


def _v_tool_failed(d):
    return _req(d, "tool", "error_type", "error_message", "latency_ms")


def _v_batch_started(d):
    return _req(d, "tool_count", "tools")


def _v_batch_completed(d):
    return _req(d, "completed", "denied", "failed")


def _v_server_started(d):
    return _req(d, "version", "permission_mode", "python",
                "hook_count", "event_log_version")


def _v_server_stopping(d):
    return (_req(d, "reason", "uptime_s")
            + _enum(d, "reason", {"restart", "shutdown", "crash_detected"}))


def _v_doctor_run(d):
    return _req(d, "checks", "summary")


VALIDATORS = {
    "SessionPaired": _v_session_paired,
    "SessionRevoked": _v_session_revoked,
    "PairingDenied": _v_pairing_denied,
    "ToolCalled": _v_tool_called,
    "HookEvaluated": _v_hook_evaluated,
    "ApprovalRequested": _v_approval_requested,
    "ToolCompleted": _v_tool_completed,
    "ToolDenied": _v_tool_denied,
    "ToolFailed": _v_tool_failed,
    "BatchStarted": _v_batch_started,
    "BatchCompleted": _v_batch_completed,
    "ServerStarted": _v_server_started,
    "ServerStopping": _v_server_stopping,
    "DoctorRun": _v_doctor_run,
}

VALID_EVENT_TYPES = frozenset(VALIDATORS)


def validate(type, data):
    """Return a list of schema errors for a (type, data) pair."""
    validator = VALIDATORS.get(type)
    if validator is None:
        return [f"unknown event type: {type!r}"]
    if not isinstance(data, dict):
        return ["data must be an object"]
    return validator(data)
