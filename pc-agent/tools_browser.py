"""Thin re-export shim: implementation lives in
seams.providers.windows.browser. Kept so existing importers keep working.
"""
from seams.providers.windows.browser import (
    _approved,
    browser_click,
    browser_eval,
    browser_fill,
    browser_navigate,
    browser_snapshot,
)
