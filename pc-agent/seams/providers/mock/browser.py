"""Mock browser CDP tools: canned snapshot/navigate results. No real browser."""
from seams.providers.mock import record

_PAGE_URL = "https://mock.example/"


def _page(url_contains: str = "") -> str:
    return _PAGE_URL


def browser_snapshot(url_contains: str = "") -> dict:
    return {"page_url": _page(url_contains), "url": _PAGE_URL,
            "title": "Mock Page",
            "elements": [
                {"ref": "e1", "role": "button", "name": "Mock Button"},
                {"ref": "e2", "role": "textbox", "name": "Mock Input"},
            ],
            "cross_origin_iframes": []}


def browser_navigate(url: str, url_contains: str = "") -> dict:
    record("browser_navigate", {"url": url, "url_contains": url_contains})
    return {"page_url": url, "result": "navigated (mock)"}


def browser_click(text: str, url_contains: str = "") -> dict:
    record("browser_click", {"text": text, "url_contains": url_contains})
    return {"page_url": _page(url_contains),
            "result": f"clicked {text!r} (mock)"}


def browser_fill(label: str, text: str, url_contains: str = "") -> dict:
    record("browser_fill", {"label": label, "text": text,
                            "url_contains": url_contains})
    return {"page_url": _page(url_contains),
            "result": f"filled {label!r} (mock)"}


def browser_eval(js: str, url_contains: str = "") -> dict:
    record("browser_eval", {"js": js, "url_contains": url_contains})
    return {"page_url": _page(url_contains), "type": "string",
            "value": "mock eval result"}


# --- Chrome CDP session hand (cdp_*): canned session results -----------

def cdp_launch(profile_dir: str = "", debug_port: int = 0,
               start_url: str = "") -> dict:
    record("cdp_launch", {"profile_dir": profile_dir,
                          "debug_port": debug_port,
                          "start_url": start_url})
    return {"cdp_port": debug_port or 9333, "pid": 4242,
            "profile_dir": profile_dir or "mock-profile",
            "url": start_url or "about:blank", "title": "Mock Page"}


def cdp_connect(debug_port: int = 0) -> dict:
    record("cdp_connect", {"debug_port": debug_port})
    return {"cdp_port": debug_port or 9333, "browser": "Chrome/156.0.0.0",
            "url": _PAGE_URL, "title": "Mock Page"}


def cdp_targets(target_id: str = "") -> dict:
    return {"cdp_port": 9333,
            "targets": [{"id": "mock-target-1", "url": _PAGE_URL,
                         "title": "Mock Page", "active": True}],
            "active_target": "mock-target-1"}


def cdp_navigate(url: str, timeout_s: int = 30) -> dict:
    record("cdp_navigate", {"url": url, "timeout_s": timeout_s})
    return {"url": url, "title": "Mock Page"}


def cdp_snapshot() -> dict:
    return {"page_url": _PAGE_URL, "url": _PAGE_URL, "title": "Mock Page",
            "elements": [
                {"selector": "#go", "tag": "button", "kind": "submit",
                 "label": "Mock Button", "context": "page"},
                {"selector": "input[name=q]", "tag": "input",
                 "kind": "text", "label": "Mock Input",
                 "context": "page"},
            ],
            "cross_origin_iframes": 0}


def cdp_click(selector: str = "", text: str = "") -> dict:
    record("cdp_click", {"selector": selector, "text": text})
    return {"clicked": f"CLICKED:{text or selector} (mock)",
            "url": _PAGE_URL, "title": "Mock Page", "changed": False}


def cdp_type(text: str, selector: str = "", label: str = "",
             clear_first: bool = True) -> dict:
    record("cdp_type", {"text": text, "selector": selector,
                        "label": label, "clear_first": clear_first})
    return {"value": text, "field": "input[name=q]", "verified": True}


def cdp_evaluate(js: str) -> dict:
    record("cdp_evaluate", {"js": js})
    return {"page_url": _PAGE_URL, "type": "string",
            "value": "mock eval result"}


def cdp_readback() -> dict:
    return {"page_url": _PAGE_URL, "url": _PAGE_URL, "title": "Mock Page",
            "ready_state": "complete", "active_element": "input#q"}


def cdp_close(delete_profile: bool = False) -> dict:
    record("cdp_close", {"delete_profile": delete_profile})
    return {"closed": "browser", "forced": False,
            "profile_deleted": delete_profile,
            "profile_dir": "mock-profile"}
