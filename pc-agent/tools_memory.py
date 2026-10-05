"""Thin re-export shim: implementation lives in
seams.providers.windows.misc. Kept so existing importers keep working.
"""
from seams.providers.windows.misc import (
    _memory_dir,
    _score,
    memory_recall,
)
