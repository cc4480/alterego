"""Service Definitions for browser tools (group: browser).

Driven via the Chrome DevTools Protocol; snapshot is a read, everything
else needs approval.

CDP is a protocol, not a Windows API: these definitions are
browser-agnostic. Any platform running a Chromium-based browser with
--remote-debugging-port works; only the browser binary differs (Edge on
Windows, Chromium/Chrome on Linux/macOS) plus the --user-data-dir launch
pattern. The snapshot/click/fill/eval logic has zero OS coupling.
"""
from seams.definitions.base import ToolDef

_CDP_NOTE = ("Browser-agnostic: driven over the Chrome DevTools Protocol, "
             "not a Windows API. Ports to any OS with a Chromium-based "
             "browser on --remote-debugging-port; only the browser binary "
             "and launch flags differ.")

_URL_CONTAINS = {"url_contains": {"type": "str", "default": "",
                                  "required": False}}

BROWSER_SNAPSHOT = ToolDef(
    name="browser_snapshot",
    group="browser",
    doc="List interactive elements of the live tab (no approval). "
        "status/detail form the clean receipt: status is 'ok', "
        "'blocked_dialog' (tab is on an internal/dialog page such as "
        "edge://sync-confirmation-dialog/; detail names the URL and no "
        "elements are reported for that surface), or 'error' (detail "
        "carries the underlying CDP message).",
    args=dict(_URL_CONTAINS),
    result_keys=["page_url", "url", "title", "elements",
                 "cross_origin_iframes", "status", "detail"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_CDP_NOTE,
)

BROWSER_NAVIGATE = ToolDef(
    name="browser_navigate",
    group="browser",
    doc="Navigate the tab to a URL. Requires on-PC approval.",
    args={"url": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result"],
    approval_tier="routine",
    side_effects="session",
    platform_notes=_CDP_NOTE,
)

BROWSER_CLICK = ToolDef(
    name="browser_click",
    group="browser",
    doc="Click the element containing text in the tab. Requires approval. "
        "status distinguishes 'ok' from 'not_found' (result NOT-FOUND), "
        "'blocked_dialog' (tab intercepted by an internal/dialog page; "
        "detail names its URL, nothing was clicked) and 'error' (detail "
        "carries the underlying CDP message; a raw CDPError is never "
        "the surfaced error).",
    args={"text": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result", "status", "detail"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP_NOTE,
)

BROWSER_FILL = ToolDef(
    name="browser_fill",
    group="browser",
    doc="Fill the field matching label in the tab. Requires approval. "
        "Same status/detail receipt as browser_click: 'not_found' for "
        "a missing field, 'blocked_dialog' for an internal/dialog tab, "
        "'error' with the CDP message in detail.",
    args={"label": {"type": "str", "required": True},
          "text": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result", "status", "detail"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP_NOTE,
)

BROWSER_EVAL = ToolDef(
    name="browser_eval",
    group="browser",
    doc="Run JavaScript in the tab. Requires on-PC approval.",
    args={"js": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "type", "value"],
    approval_tier="always_ask",
    side_effects="session",
    platform_notes=_CDP_NOTE,
)

ALL_BROWSER_DEFS = [BROWSER_SNAPSHOT, BROWSER_NAVIGATE, BROWSER_CLICK,
                    BROWSER_FILL, BROWSER_EVAL]
