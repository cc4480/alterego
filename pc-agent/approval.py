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


def is_auto_approve() -> bool:
    return AUTO_APPROVE


def request_approval(summary: str, timeout_s: int = 30) -> bool:
    """Show the dialog; return True only on an explicit Yes click.

    In auto-approve mode the dialog is skipped and this returns True.
    """
    if AUTO_APPROVE:
        return True
    import ctypes  # Windows-only; imported lazily

    user32 = ctypes.windll.user32
    text = (
        "A remote operator is requesting this action on your PC:\n\n"
        + summary
        + f"\n\nAllow it? (auto-denies in {timeout_s} seconds)"
    )
    outcome: dict = {}

    def _show():
        try:
            rc = user32.MessageBoxW(
                None, text, TITLE, MB_YESNO | MB_ICONWARNING | MB_SYSTEMMODAL
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
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t.join(5)
    if "error" in outcome:
        return False  # fail closed
    return outcome.get("rc") == IDYES
