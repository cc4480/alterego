"""Browser tools via CDP. Snapshot is a read; everything else needs approval.

browser_* drives Edge (browser_cdp; Edge must run with
--remote-debugging-port=9222). cdp_* is the Chrome session hand
(chrome_cdp): a persistent, agent-owned presentation profile for
deterministic, readback-verified control. Requires websocket-client.
"""
from approval import request_approval
import browser_cdp
import chrome_cdp


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def browser_snapshot(url_contains: str = "") -> dict:
    """Accessible element list of the live Edge tab (no approval)."""
    return browser_cdp.snapshot(url_contains or None)


def browser_navigate(url: str, url_contains: str = "") -> dict:
    if not url.startswith(("https://", "http://")):
        raise ValueError("url must start with http(s)://")
    _approved("browser_navigate", f"Navigate Edge tab to:\n{url}")
    return browser_cdp.navigate(url, url_contains or None)


def browser_click(text: str, url_contains: str = "") -> dict:
    if not text:
        raise ValueError("text must be non-empty")
    _approved("browser_click",
              f"Click element containing text in Edge:\n\"{text}\"")
    return browser_cdp.click_text(text, url_contains or None)


def browser_fill(label: str, text: str, url_contains: str = "") -> dict:
    if not label or not text:
        raise ValueError("label and text must be non-empty")
    preview = text if len(text) <= 200 else text[:200] + "..."
    _approved("browser_fill",
              f"Fill field matching \"{label}\" in Edge with:\n{preview}")
    return browser_cdp.fill_field(label, text, url_contains or None)


def browser_eval(js: str, url_contains: str = "") -> dict:
    if not js or len(js) > 4000:
        raise ValueError("js must be 1-4000 chars")
    _approved("browser_eval", f"Run JavaScript in Edge tab:\n{js[:500]}")
    return browser_cdp.eval_js(js, url_contains or None)


# --- Chrome CDP session hand (cdp_*) -------------------------------------
# One persistent session (chrome_cdp module state). Every action is
# addressed by DOM identity and returns its readback, per the
# 2026-10-28 browser-execution proof.

def cdp_launch(profile_dir: str = "", debug_port: int = 0,
               start_url: str = "") -> dict:
    if start_url and not start_url.startswith(("https://", "http://",
                                                "about:")):
        raise ValueError("start_url must start with http(s):// or about:")
    _approved("cdp_launch",
              "Launch Chrome on the presentation profile:\n"
              f"profile: {profile_dir or '(agent default)'}"
              f"\nport: {debug_port or chrome_cdp.DEFAULT_DEBUG_PORT}"
              f"\nstart: {start_url or 'about:blank'}")
    r = chrome_cdp.launch(profile_dir, debug_port, start_url)
    return {"cdp_port": r["cdp_port"], "pid": r["pid"],
            "profile_dir": r["profile_dir"], "url": r["url"],
            "title": r["title"]}


def cdp_connect(debug_port: int = 0) -> dict:
    _approved("cdp_connect",
              "Attach the CDP session to the Chrome already serving "
              f"port {debug_port or chrome_cdp.DEFAULT_DEBUG_PORT}")
    r = chrome_cdp.connect(debug_port)
    return {"cdp_port": r["cdp_port"], "browser": r["browser"],
            "url": r["url"], "title": r["title"]}


def cdp_targets(target_id: str = "") -> dict:
    r = chrome_cdp.targets(target_id)
    return {"cdp_port": r["cdp_port"], "targets": r["targets"],
            "active_target": r["active_target"]}


def cdp_navigate(url: str, timeout_s: int = 30) -> dict:
    if not url.startswith(("https://", "http://")):
        raise ValueError("url must start with http(s)://")
    _approved("cdp_navigate", f"Navigate CDP tab to:\n{url}")
    r = chrome_cdp.navigate(url, timeout_s)
    return {"url": r["url"], "title": r["title"]}


def cdp_snapshot() -> dict:
    r = chrome_cdp.snapshot()
    return {"page_url": r["page_url"], "url": r["url"],
            "title": r["title"], "elements": r["elements"],
            "cross_origin_iframes": r["cross_origin_iframes"]}


def cdp_click(selector: str = "", text: str = "") -> dict:
    if not selector and not text:
        raise ValueError("selector or text is required")
    _approved("cdp_click",
              "Click element in CDP tab:\n"
              f"{selector or ('text: ' + text)}")
    r = chrome_cdp.click(selector, text)
    return {"clicked": r["clicked"], "url": r["url"],
            "title": r["title"], "changed": r["changed"]}


def cdp_type(text: str, selector: str = "", label: str = "",
             clear_first: bool = True) -> dict:
    if not text:
        raise ValueError("text must be non-empty")
    if not selector and not label:
        raise ValueError("selector or label is required")
    preview = text if len(text) <= 200 else text[:200] + "..."
    _approved("cdp_type",
              f"Type into field ({selector or label}) via CDP:\n{preview}")
    r = chrome_cdp.fill(text, selector, label, clear_first)
    return {"value": r["value"], "field": r["field"],
            "verified": r["verified"]}


def cdp_evaluate(js: str) -> dict:
    if not js or len(js) > 4000:
        raise ValueError("js must be 1-4000 chars")
    _approved("cdp_evaluate", f"Run JavaScript in CDP tab:\n{js[:500]}")
    r = chrome_cdp.eval_js(js)
    return {"page_url": r["page_url"], "type": r["type"],
            "value": r["value"]}


def cdp_readback() -> dict:
    r = chrome_cdp.readback()
    return {"page_url": r["page_url"], "url": r["url"],
            "title": r["title"], "ready_state": r["ready_state"],
            "active_element": r["active_element"]}


def cdp_close(delete_profile: bool = False) -> dict:
    _approved("cdp_close",
              "Close the CDP session (launched browser closes "
              "gracefully)" + ("\nDELETE the presentation profile"
                               if delete_profile else ""))
    r = chrome_cdp.close(delete_profile)
    return {"closed": r["closed"], "forced": r["forced"],
            "profile_deleted": r["profile_deleted"],
            "profile_dir": r["profile_dir"]}
