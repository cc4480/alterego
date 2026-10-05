"""Startup confirmation for high-risk permission modes.

When the bridge starts in dontask mode (all approval dialogs off),
the PC owner must explicitly confirm. This prevents accidental
full-access startups and adds a human check even when the env var
is set.
"""
import os
import threading


def confirm_dontask_mode() -> bool:
    """Ask the PC owner to confirm don't-ask mode at startup.

    Returns True if confirmed or if PC_BRIDGE_FULL_ACCESS_CONFIRM=0
    (explicit opt-out for trusted automated startup). Returns False
    on deny or 30s timeout, in which case the server falls back to
    default permission mode.
    """
    if os.environ.get("PC_BRIDGE_FULL_ACCESS_CONFIRM", "") == "0":
        print("dontask: confirmation skipped via "
              "PC_BRIDGE_FULL_ACCESS_CONFIRM=0")
        return True
    try:
        import ctypes
        result = [None]

        def _show_dialog():
            try:
                result[0] = ctypes.windll.user32.MessageBoxW(
                    0,
                    "pc-mcp-bridge is starting in DON'T-ASK mode.\n\n"
                    "Every tool will execute WITHOUT approval dialogs,\n"
                    "including destructive actions (shell, delete, power).\n\n"
                    "The event log still records everything.\n\n"
                    "Click YES to confirm, NO to fall back to normal mode.",
                    "pc-mcp-bridge: Confirm Don't-Ask Mode",
                    0x4 | 0x30 | 0x10000,  # Yes/No, warning icon, topmost
                )
            except Exception:
                result[0] = -1

        t = threading.Thread(target=_show_dialog, daemon=True)
        t.start()
        t.join(timeout=30)
        if result[0] is None:
            print("dontask: confirmation timed out (30s), "
                  "falling back to default mode")
            return False
        return result[0] == 6  # IDYES
    except Exception:
        print("dontask: cannot show confirmation dialog, "
              "falling back to default mode")
        return False
