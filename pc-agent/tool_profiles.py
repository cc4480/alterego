"""Machine-readable risk profiles for the 45 bridge tools.

Per the agent action-layer standard: every tool declares its blast radius,
recoverability, side effects, compensating action, and sensitivity tags so an
arbitration layer can score trajectories before executing them.

blast_radius: none | session | user_data | system | external
recoverability: 0-10 (10 = no-op or fully reversible)
"""

def _p(blast, rec, effects=(), undo=None, tags=()):
    return {"blast_radius": blast, "recoverability": rec,
            "side_effects": list(effects), "compensating_action": undo,
            "sensitivity_tags": list(tags)}


PROFILES = {
    # ---- read-only: no blast, fully recoverable ----
    "screenshot": _p("none", 10),
    "list_windows": _p("none", 10),
    "system_info": _p("none", 10),
    "list_dir": _p("none", 10),
    "read_file": _p("none", 10, tags=("user_data",)),
    "clipboard_get": _p("none", 10, tags=("user_data",)),
    "memory_recall": _p("none", 10),
    "file_info": _p("none", 10),
    "task_create": _p("none", 10, ("task record created",)),
    "task_checkpoint": _p("none", 10, ("checkpoint appended",)),
    "task_status": _p("none", 10),
    "arbitrate": _p("none", 10, ("trajectories scored",)),
    "browser_snapshot": _p("none", 10),
    "active_window": _p("none", 10),
    "idle_seconds": _p("none", 10),
    "list_processes": _p("none", 10),
    # ---- recon: read-only but touches the network ----
    "http_headers": _p("external", 10, ("HTTP request to target URL",), tags=("network",)),
    "dns_query": _p("external", 10, ("DNS query issued",), tags=("network",)),
    "tls_info": _p("external", 10, ("TLS handshake performed",), tags=("network",)),
    "tcp_check": _p("external", 10, ("TCP connection attempt",), tags=("network",)),
    # ---- window / input: session scope ----
    "focus_window": _p("session", 10, ("window z-order changes",), "refocus previous window"),
    "minimize_window": _p("session", 10, ("window minimized",), "restore window"),
    "maximize_window": _p("session", 10, ("window maximized",), "restore window"),
    "close_window": _p("session", 7, ("window closes; unsaved work prompts",),
                       "reopen app (unsaved work may be lost)"),
    "mouse_move": _p("none", 10, ("cursor moves",)),
    "mouse_scroll": _p("session", 10, ("view scrolls",), "scroll back"),
    "mouse_click": _p("session", 8, ("click lands at coordinates",), "depends on target"),
    "hotkey": _p("session", 8, ("key combo sent to focused app",), "depends on combo"),
    "type_text": _p("user_data", 6, ("text entered into focused app",),
                    "manually delete typed text", ("user_data",)),
    "paste_text": _p("user_data", 6, ("text pasted into focused app",),
                     "manually delete pasted text", ("user_data",)),
    "clipboard_set": _p("session", 9, ("clipboard content replaced",),
                        "restore previous clipboard", ("user_data",)),
    "browser_navigate": _p("session", 9, ("tab navigates",), "navigate back"),
    "browser_click": _p("session", 7, ("page element activated",), "depends on element"),
    "browser_fill": _p("session", 7, ("form field filled",), "clear field", ("user_data",)),
    "notify": _p("session", 10, ("toast notification shown",), "dismiss notification"),
    "speak": _p("session", 10, ("text spoken aloud",)),
    "set_volume": _p("session", 10, ("volume changed",), "restore previous volume"),
    # ---- file writes: user-data scope ----
    "create_dir": _p("user_data", 9, ("directory created",), "remove directory"),
    "copy_file": _p("user_data", 9, ("file copied",), "delete the copy", ("user_data",)),
    "move_file": _p("user_data", 7, ("file moved",), "move it back", ("user_data",)),
    "write_file": _p("user_data", 5, ("file created or overwritten",),
                      "restore from backup or delete", ("user_data", "destructive")),
    "edit_file": _p("user_data", 5, ("file modified",),
                     "revert the edit", ("user_data",)),
    "delete_file": _p("user_data", 2, ("file deleted",),
                      "restore from Recycle Bin", ("user_data", "destructive")),
    # ---- arbitrary code / process / power: system scope ----
    "shell_exec": _p("system", 3, ("arbitrary cmd.exe execution",),
                     "depends on command", ("system", "destructive")),
    "shell_pwsh": _p("system", 3, ("arbitrary PowerShell execution",),
                      "depends on script", ("system", "destructive")),
    "browser_eval": _p("session", 4, ("arbitrary JavaScript in page",),
                       "depends on script", ("destructive",)),
    "kill_process": _p("system", 2, ("process terminated; unsaved work lost",),
                       "restart process (work lost)", ("system", "destructive")),
    "power": _p("system", 1, ("lock / sleep / restart / shutdown",),
                "lock: sign back in; sleep: wake; restart/shutdown: reboot",
                ("system", "destructive")),
    # ---- batch: inherits the riskiest sub-call ----
    "batch": _p("system", 3, ("executes multiple tool calls",),
                "undo each sub-call", ("destructive",)),
}


def get_profile(tool_name: str) -> dict:
    """Risk profile for a tool; unknown tools get the most restrictive default."""
    return PROFILES.get(tool_name, _p("system", 0, ("unknown tool",),
                                      "manual review required",
                                      ("system", "destructive", "unknown")))


def approval_tier(tool_name: str) -> str:
    """Suggested approval tier from the risk profile."""
    p = get_profile(tool_name)
    if p["blast_radius"] == "none":
        return "silent"
    if p["blast_radius"] == "session" and p["recoverability"] >= 8:
        return "routine"
    if "destructive" in p["sensitivity_tags"] or p["recoverability"] <= 3:
        return "always_ask"
    return "ask"
