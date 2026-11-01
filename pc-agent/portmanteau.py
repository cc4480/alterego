"""Portmanteau tool grouping (Batch 6): one tool per family, dispatched to
a single member tool.

Each portmanteau tool takes (operation, args): the operation is a short
verb naming one member of the family, and args is that member's argument
object. Dispatch drops None-valued args, filters args to the member
definition's args, and calls the member through toolcall.call — so the
member's own provider-side approval gate fires exactly once at the member
tier, and hooks and events fire for both the portmanteau call and the
member call. The portmanteau provider functions define NO _approved and
perform NO muting: approval is provider-side, never centralized here.

The result is a uniform envelope {"operation", "tool", "result"} so the
result-keys contract holds for every operation.

Forbidden dispatch targets (no nesting, no recursion): "batch", every
portmanteau tool name, and every "macro_*" name. The portmanteau profile
tier is the most restrictive member tier (risk snapshot / annotations
only — runtime approval always uses the member's own tier).

Plan mode: portmanteau names are NOT in toolcall._DRY_RUN_TOOLS, so the
wrapper passes a per-operation write flag (see write_flag): write
operations stub in plan mode, read operations execute.
"""

# operation verb -> member tool name, per family.
OP_MAPS = {
    "mouse": {
        "move": "mouse_move",
        "click": "mouse_click",
        "scroll": "mouse_scroll",
    },
    "keyboard": {
        "type": "type_text",
        "hotkey": "hotkey",
        "paste": "paste_text",
    },
    "window": {
        "list": "list_windows",
        "active": "active_window",
        "focus": "focus_window",
        "close": "close_window",
        "minimize": "minimize_window",
        "maximize": "maximize_window",
    },
    "uia": {
        "find": "uia_find",
        "click": "uia_click",
        "set_text": "uia_set_text",
    },
    "file": {
        "read": "read_file",
        "read_range": "read_file_range",
        "write": "write_file",
        "edit": "edit_file",
        "delete": "delete_file",
        "list": "list_dir",
        "copy": "copy_file",
        "move": "move_file",
        "create_dir": "create_dir",
        "info": "file_info",
        "search": "search_files",
        "search_names": "search_filenames",
    },
    "process": {
        "list": "list_processes",
        "kill": "kill_process",
    },
    "browser": {
        "snapshot": "browser_snapshot",
        "navigate": "browser_navigate",
        "click": "browser_click",
        "fill": "browser_fill",
        "eval": "browser_eval",
    },
    "cdp": {
        "launch": "cdp_launch",
        "connect": "cdp_connect",
        "targets": "cdp_targets",
        "navigate": "cdp_navigate",
        "snapshot": "cdp_snapshot",
        "click": "cdp_click",
        "type": "cdp_type",
        "evaluate": "cdp_evaluate",
        "readback": "cdp_readback",
        "close": "cdp_close",
    },
    "clipboard": {
        "get": "clipboard_get",
        "set": "clipboard_set",
    },
    "exec": {
        "shell": "shell_exec",
        "pwsh": "shell_pwsh",
        "background": "exec_background",
        "status": "exec_status",
        "cancel": "exec_cancel",
    },
}

# Member tool -> wrapper write flag, mirroring the member wrappers:
# True = write=True, False = no write flag (read), "dry_run" = write is
# honored per call via args["dry_run"] (file tools pass write=not dry_run).
_WRITE = {
    "mouse_move": True, "mouse_click": True, "mouse_scroll": True,
    "type_text": True, "hotkey": True, "paste_text": True,
    "list_windows": False, "active_window": False,
    "focus_window": True, "close_window": True,
    "minimize_window": True, "maximize_window": True,
    "uia_find": False, "uia_click": True, "uia_set_text": True,
    "read_file": False, "read_file_range": False,
    "search_files": False, "search_filenames": False,
    "list_dir": False, "file_info": False,
    "write_file": "dry_run", "edit_file": "dry_run",
    "delete_file": "dry_run", "create_dir": "dry_run",
    "copy_file": "dry_run", "move_file": "dry_run",
    "list_processes": False, "kill_process": True,
    "browser_snapshot": False, "browser_navigate": True,
    "browser_click": True, "browser_fill": True, "browser_eval": True,
    "cdp_launch": True, "cdp_connect": True, "cdp_targets": False,
    "cdp_navigate": True, "cdp_snapshot": False, "cdp_click": True,
    "cdp_type": True, "cdp_evaluate": True, "cdp_readback": False,
    "cdp_close": True,
    "clipboard_get": False, "clipboard_set": True,
    "shell_exec": True, "shell_pwsh": True, "exec_background": True,
    "exec_status": False, "exec_cancel": True,
}


def operations(tool: str) -> list:
    """Operation verbs for a portmanteau tool. ValueError on unknown tool."""
    try:
        return sorted(OP_MAPS[tool])
    except KeyError:
        raise ValueError(
            f"unknown portmanteau tool: {tool!r}; "
            f"expected one of {sorted(OP_MAPS)}") from None


def _is_forbidden(name: str) -> bool:
    """Targets dispatch must never reach: batch, portmanteau tools, macros."""
    return (name == "batch" or name in OP_MAPS
            or name.startswith("macro_"))


def resolve_member(tool: str, operation: str) -> str:
    """Member tool name for (tool, operation). Fail loud on anything
    unknown or forbidden, listing the valid operations."""
    if not isinstance(operation, str) or not operation:
        raise ValueError(
            f"operation must be a non-empty string for {tool!r}")
    ops = OP_MAPS.get(tool)
    if ops is None:
        raise ValueError(
            f"unknown portmanteau tool: {tool!r}; "
            f"expected one of {sorted(OP_MAPS)}")
    member = ops.get(operation)
    if member is None:
        raise ValueError(
            f"unknown operation {operation!r} for {tool!r}; "
            f"expected one of {sorted(ops)}")
    if _is_forbidden(member):
        raise ValueError(f"forbidden dispatch target: {member!r}")
    return member


def write_flag(tool: str, operation: str, args=None) -> bool:
    """Per-operation wrapper write flag, mirroring the member wrapper.

    Unknown operations fail closed (True): the outer call stubs in plan
    mode and the provider raises the informative error in normal mode.
    File operations honor args["dry_run"]: write=not dry_run."""
    try:
        member = resolve_member(tool, operation)
    except ValueError:
        return True
    flag = _WRITE[member]
    if flag == "dry_run":
        return not bool((args or {}).get("dry_run", False))
    return bool(flag)


def dispatch(tool: str, operation: str, args=None) -> dict:
    """Run one member call; return the uniform envelope.

    args must be a dict (or None for no args). None-valued args are
    dropped so member defaults apply; keys outside the member
    definition's args are dropped. Unknown operations, non-dict args,
    and forbidden targets raise ValueError (the pipeline converts
    provider exceptions to {"error": ...} envelopes)."""
    member = resolve_member(tool, operation)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError(
            f"args must be an object for {tool!r}/{operation!r}")
    from seams import definitions  # local: definitions import first is cheap
    defn = definitions.by_name(member)
    if defn is None:
        raise RuntimeError(f"no definition for member tool {member!r}")
    filtered = {k: v for k, v in args.items()
                if k in defn.args and v is not None}
    from toolcall import call  # lazy: avoids an import cycle at module load
    result = call(member, filtered, write=write_flag(tool, operation, args))
    return {"operation": operation, "tool": member, "result": result}
