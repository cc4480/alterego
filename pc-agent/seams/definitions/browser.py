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
    doc="List interactive elements of the live tab (no approval).",
    args=dict(_URL_CONTAINS),
    result_keys=["page_url", "url", "title", "elements",
                 "cross_origin_iframes"],
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
    doc="Click the element containing text in the tab. Requires approval.",
    args={"text": {"type": "str", "required": True}, **_URL_CONTAINS},
    result_keys=["page_url", "result"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP_NOTE,
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

# --- Chrome CDP session hand (browser2 family, implemented as cdp_*) ---
#
# The deterministic presentation hand proven 2026-10-28
# (browser-execution-proof): raw CDP over a per-target WebSocket with a
# background reader, a prepared presentation profile owned by the agent
# (never the user's real Chrome profile), actions addressed by DOM
# identity and verified by readback in the same call, and a graceful
# Browser.close. Distinct from the browser_* seat above, which attaches
# opportunistically to a headless Edge for one-shot DOM access; this
# family owns a visible Chrome session across calls (launch -> act ->
# close), which is what live presentation needs.

_CDP2_NOTE = ("Driven over the Chrome DevTools Protocol against a "
              "persistent session on an agent-owned presentation "
              "profile; per-target WebSocket + background reader "
              "(never flatten-mode attach). Chrome/Chromium only; "
              "the profile is prepared under the agent data area and "
              "the user's real browser profile is refused.")

CDP_LAUNCH = ToolDef(
    name="cdp_launch",
    group="browser",
    doc="Launch Chrome on the prepared presentation profile with a "
        "CDP debug port and attach the session. Requires approval.",
    args={"profile_dir": {"type": "str", "default": "",
                          "required": False},
          "debug_port": {"type": "int", "default": 0, "required": False},
          "start_url": {"type": "str", "default": "",
                        "required": False}},
    result_keys=["cdp_port", "pid", "profile_dir", "url", "title"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_CONNECT = ToolDef(
    name="cdp_connect",
    group="browser",
    doc="Attach the CDP session to a Chrome already serving a debug "
        "port (no process ownership). Requires approval.",
    args={"debug_port": {"type": "int", "default": 0, "required": False}},
    result_keys=["cdp_port", "browser", "url", "title"],
    approval_tier="routine",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_TARGETS = ToolDef(
    name="cdp_targets",
    group="browser",
    doc="List the session browser's page targets; switches the active "
        "target when target_id is given (no approval).",
    args={"target_id": {"type": "str", "default": "", "required": False}},
    result_keys=["cdp_port", "targets", "active_target"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_CDP2_NOTE,
)

CDP_NAVIGATE = ToolDef(
    name="cdp_navigate",
    group="browser",
    doc="Navigate the CDP session tab to a URL and verify the landing "
        "(url + title readback; hard timeout, never a silent hang). "
        "Requires approval.",
    args={"url": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 30, "required": False}},
    result_keys=["url", "title"],
    approval_tier="routine",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_SNAPSHOT = ToolDef(
    name="cdp_snapshot",
    group="browser",
    doc="Compact index of the page's interactive elements with stable "
        "CSS selectors — the addressing/verification primitive "
        "(no approval).",
    args={},
    result_keys=["page_url", "url", "title", "elements",
                 "cross_origin_iframes"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_CDP2_NOTE,
)

CDP_CLICK = ToolDef(
    name="cdp_click",
    group="browser",
    doc="Click an element by CSS selector or visible text (never "
        "coordinates); returns the landing readback. Requires approval.",
    args={"selector": {"type": "str", "default": "", "required": False},
          "text": {"type": "str", "default": "", "required": False}},
    result_keys=["clicked", "url", "title", "changed"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_TYPE = ToolDef(
    name="cdp_type",
    group="browser",
    doc="Type into a field addressed by selector or label via CDP "
        "Input.insertText; success requires value readback equality. "
        "Requires approval.",
    args={"text": {"type": "str", "required": True},
          "selector": {"type": "str", "default": "", "required": False},
          "label": {"type": "str", "default": "", "required": False},
          "clear_first": {"type": "bool", "default": True,
                          "required": False}},
    result_keys=["value", "field", "verified"],
    approval_tier="ask",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_EVALUATE = ToolDef(
    name="cdp_evaluate",
    group="browser",
    doc="Run JavaScript in the CDP session tab (escape hatch; same "
        "approval tier as browser_eval). Requires on-PC approval.",
    args={"js": {"type": "str", "required": True}},
    result_keys=["page_url", "type", "value"],
    approval_tier="always_ask",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

CDP_READBACK = ToolDef(
    name="cdp_readback",
    group="browser",
    doc="Read back the session page's url, title, ready_state and "
        "active element — verify a landing without acting "
        "(no approval).",
    args={},
    result_keys=["page_url", "url", "title", "ready_state",
                 "active_element"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_CDP2_NOTE,
)

CDP_CLOSE = ToolDef(
    name="cdp_close",
    group="browser",
    doc="End the CDP session: a launched browser is closed gracefully "
        "via Browser.close (never force-killed unless it ignores the "
        "graceful close); an attached browser is only detached. "
        "Optionally deletes the presentation profile. Requires "
        "approval.",
    args={"delete_profile": {"type": "bool", "default": False,
                             "required": False}},
    result_keys=["closed", "forced", "profile_deleted", "profile_dir"],
    approval_tier="routine",
    side_effects="session",
    platform_notes=_CDP2_NOTE,
)

ALL_BROWSER_DEFS = [BROWSER_SNAPSHOT, BROWSER_NAVIGATE, BROWSER_CLICK,
                    BROWSER_FILL, BROWSER_EVAL,
                    CDP_LAUNCH, CDP_CONNECT, CDP_TARGETS, CDP_NAVIGATE,
                    CDP_SNAPSHOT, CDP_CLICK, CDP_TYPE, CDP_EVALUATE,
                    CDP_READBACK, CDP_CLOSE]
