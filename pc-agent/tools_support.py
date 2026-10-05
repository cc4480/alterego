"""Thin re-export shim: implementation lives in
seams.providers.windows.support. Kept so existing importers keep working.
"""
from seams.providers.windows.support import (
    MAX_OUTPUT,
    WRITE_TOOLS,
    _approved,
    batch,
    shell_pwsh,
)
