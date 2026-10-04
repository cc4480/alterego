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
    test_js_snippets_present()
    print("ALL BROWSER_CDP TESTS PASS")
