"""Service Definitions for browser tools (group: browser).

Driven via the Chrome DevTools Protocol; snapshot is a read, everything
else needs approval.
"""
from seams.definitions.base import ToolDef

_URL_CONTAINS = {"url_contains": {"type": "str", "default": "",
                                  "required": False}}

BROWSER_SNAPSHOT = ToolDef(
    name="browser_snapshot",
    group="browser",
    doc="List interactive elements of the live tab (no approval).",
    args=dict(_URL_CONTAINS),
    result_keys=["page_url", "url", "title", "elements",
                 "cross_origin_iframes"],
    approval_tier="silent",
    side_effects="none",
)

BROWSER_NAVIGATE = ToolDef(
    name="browser_navigate",
    group="browser",
    doc="Navigate the tab to a URL. Requires on-PC approval.",
    args={"url": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result"],
    approval_tier="routine",
    side_effects="session",
)

BROWSER_CLICK = ToolDef(
    name="browser_click",
    group="browser",
    doc="Click the element containing text in the tab. Requires approval.",
    args={"text": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result"],
    approval_tier="ask",
    side_effects="session",
)

BROWSER_FILL = ToolDef(
    name="browser_fill",
    group="browser",
    doc="Fill the field matching label in the tab. Requires approval.",
    args={"label": {"type": "str", "required": True},
          "text": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result"],
    approval_tier="ask",
    side_effects="session",
)

BROWSER_EVAL = ToolDef(
    name="browser_eval",
    group="browser",
    doc="Run JavaScript in the tab. Requires on-PC approval.",
    args={"js": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "type", "value"],
    approval_tier="always_ask",
    side_effects="session",
)

ALL_BROWSER_DEFS = [BROWSER_SNAPSHOT, BROWSER_NAVIGATE, BROWSER_CLICK,
                    BROWSER_FILL, BROWSER_EVAL]
