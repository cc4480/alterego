"""Browser tools via Edge CDP. Snapshot is a read; everything else needs approval.

Edge must run with --remote-debugging-port=9222. Requires websocket-client.
"""
from approval import request_approval
import browser_cdp


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
