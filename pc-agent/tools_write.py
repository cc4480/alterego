"""Thin re-export shim: implementation lives in
seams.providers.windows.write. Kept so existing importers keep working.
"""
from seams.providers.windows.write import (
    MAX_OUTPUT,
    _approved,
    _key_event,
    _mouse_event,
    _parse_hotkey,
    _send_unicode,
    _tap_vk,
    clipboard_set,
    close_window,
    focus_window,
    hotkey,
    kill_process,
    maximize_window,
    minimize_window,
    mouse_click,
    mouse_move,
    mouse_scroll,
    paste_text,
    shell_exec,
    type_text,
    uia_click,
    uia_set_text,
)
