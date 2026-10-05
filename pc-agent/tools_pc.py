"""Thin re-export shim: implementation lives in
seams.providers.windows.pc. Kept so existing importers keep working.
"""
from seams.providers.windows.pc import (
    _approved,
    _proc_name,
    _ps_quote,
    active_window,
    idle_seconds,
    list_processes,
    notify,
    power,
    set_volume,
    speak,
)
