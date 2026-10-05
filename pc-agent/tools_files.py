"""Thin re-export shim: implementation lives in
seams.providers.windows.files. Kept so existing importers keep working.
"""
from seams.providers.windows.files import (
    MAX_WRITE_BYTES,
    _abs,
    _approved,
    _diff,
    copy_file,
    create_dir,
    delete_file,
    edit_file,
    file_info,
    move_file,
    write_file,
)
