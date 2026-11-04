"""Native Windows Yes/No approval dialog for write tools.

Every write-tool call blocks on this. Explicit Yes (IDYES) is the only path
to approval: timeout, dialog error, or a non-interactive session all fail
closed (deny).

AUTO-APPROVE MODE: if the environment variable PC_BRIDGE_AUTO_APPROVE=1 is
set when the server starts, the dialog is skipped and every write is
allowed. This exists for the PC owner's own testing only — it removes the
human gate entirely. Pairing/bearer authentication is NOT affected; the
operator still needs a valid session token. The server prints a loud
warning banner in this mode and the audit log marks every auto-approved
call. Restart without the env var to restore dialogs.

TIERS (from tool_profiles.approval_tier):
- silent: read-only tools; no dialog, callers skip approval entirely.
- routine / ask: dialog shown; skipped in auto-approve mode.
- always_ask: dialog ALWAYS shown, even in auto-approve mode, with a
  DESTRUCTIVE title. Fail closed on timeout/error as usual.

FULL-ACCESS MODE: if PC_BRIDGE_FULL_ACCESS=1 is set, every dialog is
skipped unconditionally — including always_ask/DESTRUCTIVE. The audit
log still records every call with its tier. Restart without the env var
to restore dialogs.

NAMED PERMISSION POSTURES (PC_BRIDGE_PERMISSION_MODE):
- default     : reads silent, writes show dialog, destructive always dialog.
- plan        : every write/destructive tool runs in dry-run mode (no
                dialogs, no side effects). Handled in toolcall.call.
- acceptEdits : file write tools auto-approved; shell/destructive tools
                still show the approval dialog.
- dontAsk     : no dialogs at all (same as PC_BRIDGE_FULL_ACCESS=1).

Backward compat: PC_BRIDGE_FULL_ACCESS=1 with no explicit mode is
treated as dontAsk.

STACK SAFETY (2026-11-02): approval dialogs are serialized through one
lock — at most one approval window exists at any moment, however many
tool calls arrive concurrently. The window is shown with
MessageBoxTimeoutW, which closes ITSELF when the timeout expires, so a
timed-out dialog can never be orphaned on screen. (The previous design
ran each MessageBoxW on its own thread and, on timeout, tried to close
"the" dialog by title lookup; every destructive dialog shares one
title, so with several queued the close hit the wrong window or none,
and unanswered dialogs stacked a dozen deep on the desktop.) A request
identical to one that already timed out unanswered within the last
120 seconds is denied immediately without a new window, so a retrying
caller cannot re-spam the desktop for a decision nobody made.
"""
import contextvars
import os
import threading
import time

TITLE = "pc-mcp-bridge: approve remote action"
MB_YESNO = 0x04
MB_ICONWARNING = 0x30
MB_SYSTEMMODAL = 0x1000
IDYES = 6
WM_CLOSE = 0x0010

AUTO_APPROVE = os.environ.get("PC_BRIDGE_AUTO_APPROVE") == "1"
FULL_ACCESS = os.environ.get("PC_BRIDGE_FULL_ACCESS") == "1"

# File tools that acceptEdits mode auto-approves. delete_file stays on
# dialog (tagged destructive, recoverability 2).
ACCEPT_EDITS_AUTO = {"write_file", "edit_file", "create_dir",
                     "copy_file", "move_file"}


def _resolve_permission_mode() -> str:
    mode = os.environ.get("PC_BRIDGE_PERMISSION_MODE", "").strip().lower()
    if mode in ("plan", "acceptedits", "dontask", "default"):
        return mode
    if FULL_ACCESS:  # backward compat
        return "dontask"
    return "default"


PERMISSION_MODE = _resolve_permission_mode()


def override_permission_mode(mode: str) -> None:
    """Override the permission mode at runtime (e.g., after a declined
    dontask confirmation falls back to default)."""
    global PERMISSION_MODE, FULL_ACCESS
    if mode in ("plan", "acceptedits", "dontask", "default"):
        PERMISSION_MODE = mode
        if mode == "default":
            # is_full_access() also ORs the frozen FULL_ACCESS global,
            # so setting the mode alone could never revoke dontAsk
            # granted via PC_BRIDGE_FULL_ACCESS=1.
            FULL_ACCESS = False


def get_permission_mode() -> str:
    """One of: plan | acceptEdits | dontAsk | default."""
    return PERMISSION_MODE


def is_auto_approve() -> bool:
    return AUTO_APPROVE


def is_full_access() -> bool:
    # Full access now means the dontAsk posture (or the legacy env var).
    return FULL_ACCESS or PERMISSION_MODE == "dontask"


# Last gate decision, recorded by request_approval on EVERY path (shown
# and skipped alike) so the audit log reports facts, never predictions.
# Consumed once per tool call by the pipeline (pop_gate_record); a call
# whose provider never consults the gate finds None and logs no
# ApprovalRequested at all. Never holds token values.
_GATE_RECORD: contextvars.ContextVar = contextvars.ContextVar(
    "pc_bridge_gate_record", default=None)


def _record_gate(shown, result, skipped, tier, tool) -> None:
    _GATE_RECORD.set({"shown": shown, "result": result,
                      "skipped": skipped, "tier": tier, "tool": tool})


def pop_gate_record():
    """Take the recorded gate decision (consume-once). None when the
    provider never consulted approval for this call."""
    rec = _GATE_RECORD.get()
    _GATE_RECORD.set(None)
    return rec


def request_approval(summary: str, timeout_s: int = 30,
                     tier: str = "routine",
                     tool_name: str | None = None) -> bool:
    """Show the dialog; return True only on an explicit Yes click.

    In auto-approve mode the dialog is skipped and this returns True —
    unless tier is "always_ask" (destructive tools), which always shows
    the dialog with a DESTRUCTIVE title.

    In dontAsk mode (PC_BRIDGE_FULL_ACCESS=1 or mode=dontAsk) ALL dialogs
    are skipped, including always_ask. The audit log still records
    every call.

    In acceptEdits mode, file write tools (write_file, edit_file, ...)
    are auto-approved; shell/destructive tools still show the dialog.
    """
    if is_full_access():
        _record_gate(False, None, "dontAsk", tier, tool_name)
        return True
    if (PERMISSION_MODE == "acceptedits" and tool_name in ACCEPT_EDITS_AUTO
            and tier != "always_ask"):
        _record_gate(False, None, "acceptEdits", tier, tool_name)
        return True
    if tier == "always_ask":
        approved, timed_out, shown = _show_dialog(summary, timeout_s,
                                                 destructive=True)
        result = ("yes" if approved else "timeout" if timed_out else "no") \
            if shown else "no"
        _record_gate(shown, result, None, tier, tool_name)
        return approved
    elif AUTO_APPROVE:
        _record_gate(False, None, "auto_approve", tier, tool_name)
        return True
    else:
        approved, timed_out, shown = _show_dialog(summary, timeout_s,
                                                 destructive=False)
        result = ("yes" if approved else "timeout" if timed_out else "no") \
            if shown else "no"
        _record_gate(shown, result, None, tier, tool_name)
        return approved


MB_TIMEDOUT = 32000  # MessageBoxTimeoutW return value on timeout

# One approval window at a time, ever. Callers queue on this lock; a
# queued caller whose request is identical to one that just timed out
# unanswered is denied from the cache below instead of re-showing.
_DIALOG_LOCK = threading.Lock()

# summary -> monotonic timestamp of its last unanswered timeout.
_RECENT_TIMEOUTS: dict = {}
_TIMEOUT_DEDUPE_S = 120


def _show_dialog(summary: str, timeout_s: int,
                 destructive: bool) -> tuple:
    """Show the dialog; returns (approved, timed_out, shown). The
    timeout-dedupe cache denies without showing (shown False)."""
    with _DIALOG_LOCK:
        now = time.monotonic()
        # Prune stale entries so the cache stays bounded.
        for key in [k for k, ts in _RECENT_TIMEOUTS.items()
                    if now - ts >= _TIMEOUT_DEDUPE_S]:
            del _RECENT_TIMEOUTS[key]
        if summary in _RECENT_TIMEOUTS:
            return False, False, False  # identical timeout, no dialog shown
        approved, timed_out = _show_dialog_once(summary, timeout_s,
                                                destructive)
        if timed_out:
            _RECENT_TIMEOUTS[summary] = time.monotonic()
        return approved, timed_out, True


def _show_dialog_once(summary: str, timeout_s: int,
                      destructive: bool):
    """Show exactly one dialog. Returns (approved, timed_out).

    Prefers MessageBoxTimeoutW: it dismisses its own window when the
    timeout expires, so nothing can be left orphaned on the desktop.
    Falls back to a threaded MessageBoxW whose timeout close is safe
    only because _DIALOG_LOCK guarantees this is the sole approval
    window on screen, making the title lookup unambiguous.
    """
    import ctypes  # Windows-only; imported lazily

    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return False, False  # no Win32 dialog available: fail closed
    title = ("pc-mcp-bridge: DESTRUCTIVE ACTION - approve?"
             if destructive else TITLE)
    text = (
        "A remote operator is requesting this action on your PC:\n\n"
        + summary
        + f"\n\nAllow it? (auto-denies in {timeout_s} seconds)"
    )
    flags = MB_YESNO | MB_ICONWARNING | MB_SYSTEMMODAL

    timeout_fn = getattr(user32, "MessageBoxTimeoutW", None)
    if timeout_fn is not None:
        timeout_fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                               ctypes.c_wchar_p, ctypes.c_uint,
                               ctypes.c_ushort, ctypes.c_uint]
        timeout_fn.restype = ctypes.c_int
        try:
            rc = timeout_fn(None, text, title, flags, 0,
                            int(timeout_s * 1000))
        except Exception:
            return False, False  # fail closed
        if rc == MB_TIMEDOUT:
            return False, True
        return rc == IDYES, False

    outcome: dict = {}

    def _show():
        try:
            rc = user32.MessageBoxW(None, text, title, flags)
        except Exception as exc:  # no interactive desktop, etc.
            outcome["error"] = str(exc)
        else:
            outcome["rc"] = rc

    t = threading.Thread(target=_show, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        # Timed out: close the dialog, then treat as denied. This is
        # the only approval window (see _DIALOG_LOCK), so the title
        # lookup cannot hit a different request's dialog.
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t.join(5)
        return False, "error" not in outcome
    if "error" in outcome:
        return False, False  # fail closed
    return outcome.get("rc") == IDYES, False
