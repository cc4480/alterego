"""Thin re-export shim: implementation lives in
seams.providers.windows.read. Kept so existing importers keep working.
"""
from seams.providers.windows.read import (
    MAX_READ_BYTES,
    _check_path,
    _system_root,
    _user_profile,
    clipboard_get,
    list_dir,
    list_windows,
    read_file,
    screenshot,
    system_info,
    uia_find,
)
