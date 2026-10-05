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
"""
import os
import threading

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


def get_permission_mode() -> str:
    """One of: plan | acceptEdits | dontAsk | default."""
    return PERMISSION_MODE


def is_auto_approve() -> bool:
    return AUTO_APPROVE


def is_full_access() -> bool:
    # Full access now means the dontAsk posture (or the legacy env var).
    return FULL_ACCESS or PERMISSION_MODE == "dontask"


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
        return True
    if (PERMISSION_MODE == "acceptedits" and tool_name in ACCEPT_EDITS_AUTO
            and tier != "always_ask"):
        return True
    if tier == "always_ask":
        return _show_dialog(summary, timeout_s, destructive=True)
    if AUTO_APPROVE:
        return True
    return _show_dialog(summary, timeout_s, destructive=False)


def _show_dialog(summary: str, timeout_s: int, destructive: bool) -> bool:
    import ctypes  # Windows-only; imported lazily

    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return False  # no Win32 dialog available: fail closed
    title = ("pc-mcp-bridge: DESTRUCTIVE ACTION - approve?"
             if destructive else TITLE)
    text = (
        "A remote operator is requesting this action on your PC:\n\n"
        + summary
        + f"\n\nAllow it? (auto-denies in {timeout_s} seconds)"
    )
    outcome: dict = {}

    def _show():
        try:
            rc = user32.MessageBoxW(
                None, text, title, MB_YESNO | MB_ICONWARNING | MB_SYSTEMMODAL
            )
        except Exception as exc:  # no interactive desktop, etc.
            outcome["error"] = str(exc)
        else:
            outcome["rc"] = rc

    t = threading.Thread(target=_show, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        # Timed out: close the dialog, then treat as denied.
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t.join(5)
    if "error" in outcome:
        return False  # fail closed
    return outcome.get("rc") == IDYES
