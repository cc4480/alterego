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
    cdp_click,
    cdp_close,
    cdp_connect,
    cdp_evaluate,
    cdp_launch,
    cdp_navigate,
    cdp_readback,
    cdp_snapshot,
    cdp_targets,
    cdp_type,
)
