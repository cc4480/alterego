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
