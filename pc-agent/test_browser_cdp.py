"""Tests for browser_cdp (mocked WebSocket; no Edge needed)."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import browser_cdp


class FakeWS:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, msg):
        self.sent.append(json.loads(msg))

    def recv(self):
        return json.dumps(self.replies.pop(0))

    def close(self):
        pass


def _browser_with(replies):
    b = browser_cdp.Browser.__new__(browser_cdp.Browser)
    b.ws = FakeWS(replies)
    b._id = 0
    b.page_url = "https://example.com"
    return b


def test_cmd_matches_by_id():
    b = _browser_with([
        {"id": 99, "result": {}},          # stray event/other id: skipped
        {"id": 1, "result": {"ok": 1}},
    ])
    r = b.cmd("Page.navigate", {"url": "x"})
    assert r == {"ok": 1}, r
    assert b.ws.sent[0]["method"] == "Page.navigate"
    print("PASS cmd id matching")


def test_cmd_error_raises():
    b = _browser_with([{"id": 1, "error": {"message": "boom"}}])
    try:
        b.cmd("Bad.method")
    except browser_cdp.CDPError as e:
        assert "boom" in str(e)
        print("PASS cmd error")
    else:
        raise AssertionError("should have raised")


def test_eval_returns_value():
    b = _browser_with([{"id": 1, "result": {"result": {
        "type": "string", "value": "hi"}}}])
    val, typ = b.eval("1+1")
    assert (val, typ) == ("hi", "string")
    print("PASS eval")


def test_no_edge_gives_clear_error():
    try:
        browser_cdp.snapshot()
    except browser_cdp.CDPError as e:
        # either "not installed" or "cannot reach Edge" — both mention the fix
        assert "9222" in str(e) or "websocket-client" in str(e), str(e)
        print("PASS no-edge error")
    else:
        raise AssertionError("should have raised")


class _Handshake403(Exception):
    """Stands in for websocket-client's WebSocketBadStatusException
    (status_code 403) without needing the library installed."""
    status_code = 403


def _patched_browser(open_ws):
    """Browser() with every transport/launch seam faked: the primary
    endpoint on 9222 'exists' (HTTP discovery is monkeypatched via
    _ws_url), and open_ws decides per-URL whether the WS connects."""
    saved = {}
    for name in ("_require_websocket", "_ensure_edge_headless",
                 "_ensure_fallback_edge", "_ws_url", "_open_ws"):
        saved[name] = getattr(browser_cdp, name)
    browser_cdp._require_websocket = lambda: None
    browser_cdp._ensure_edge_headless = lambda timeout_s=15.0: 9222
    browser_cdp._ensure_fallback_edge = \
        lambda timeout_s=15.0: browser_cdp.FALLBACK_CDP_PORT
    browser_cdp._ws_url = lambda url_contains=None, port=9222: (
        f"ws://127.0.0.1:{port}/devtools/page/T1", "about:blank")
    browser_cdp._open_ws = open_ws
    return saved


def _restore(saved):
    for name, fn in saved.items():
        setattr(browser_cdp, name, fn)


def test_ws_403_falls_back_to_dedicated_edge():
    """Regression (2026-11-02 sweep): a foreign Edge on 9222 launched
    without --remote-allow-origins=* answers /json but 403s the
    WebSocket handshake. Browser() must route to the dedicated
    fallback instance instead of dying — and must not touch 9222's
    browser (no kill/restart seam is even called)."""
    attempts = []

    def open_ws(ws_url):
        attempts.append(ws_url)
        if ":9222/" in ws_url:
            raise _Handshake403("Handshake status 403 Forbidden")
        return FakeWS([])

    saved = _patched_browser(open_ws)
    try:
        b = browser_cdp.Browser()
    finally:
        _restore(saved)
    assert attempts == [
        "ws://127.0.0.1:9222/devtools/page/T1",
        f"ws://127.0.0.1:{browser_cdp.FALLBACK_CDP_PORT}/devtools/page/T1",
    ], attempts
    assert b.cdp_port == browser_cdp.FALLBACK_CDP_PORT, b.cdp_port
    assert b.cdp_port != browser_cdp.CDP_PORT
    print("PASS ws 403 falls back to dedicated Edge")


def test_ws_primary_success_never_falls_back():
    def open_ws(ws_url):
        return FakeWS([])

    saved = _patched_browser(open_ws)

    def no_fallback(timeout_s=15.0):
        raise AssertionError("fallback must not launch when 9222 works")
    browser_cdp._ensure_fallback_edge = no_fallback
    try:
        b = browser_cdp.Browser()
    finally:
        _restore(saved)
    assert b.cdp_port == browser_cdp.CDP_PORT, b.cdp_port
    print("PASS primary success: no fallback")


def test_ws_both_endpoints_fail_raises_with_both_ports():
    def open_ws(ws_url):
        raise _Handshake403("Handshake status 403 Forbidden")

    saved = _patched_browser(open_ws)
    try:
        browser_cdp.Browser()
    except browser_cdp.CDPError as e:
        assert "9222" in str(e) and "9223" in str(e), str(e)
        print("PASS both endpoints fail: loud combined error")
    else:
        raise AssertionError("should have raised")
    finally:
        _restore(saved)


def test_js_snippets_present():
    for name in ("_FIND_CLICK", "_FIND_FILL", "_SNAPSHOT"):
        s = getattr(browser_cdp, name)
        assert isinstance(s, str) and "=>" in s, name
        # every snippet must use the deep query (shadow roots + iframes)
        assert "__cdp_els" in s, name
    assert "shadowRoot" in browser_cdp._DEEP
    assert "contentDocument" in browser_cdp._DEEP
    print("PASS snippets")


if __name__ == "__main__":
    test_cmd_matches_by_id()
    test_cmd_error_raises()
    test_eval_returns_value()
    test_no_edge_gives_clear_error()
    test_ws_403_falls_back_to_dedicated_edge()
    test_ws_primary_success_never_falls_back()
    test_ws_both_endpoints_fail_raises_with_both_ports()
    test_js_snippets_present()
    print("ALL BROWSER_CDP TESTS PASS")
