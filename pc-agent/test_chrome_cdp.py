"""Tests for chrome_cdp (fake WebSocket; no Chrome, no network).

The centerpiece is the regression the 2026-10-28 proof bought us:
flatten-mode CDP sessions silently dropped command responses when
events and other responses interleaved. This hand uses a per-target
socket + a background reader that routes by id — the tests below feed
exactly that hostile frame mix and require correct, loud behavior.
"""
import json
import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chrome_cdp

_CLOSE = object()


class FakeWS:
    """Queue-backed socket. recv() blocks like a real one; send()
    records the frame and lets the test's responder queue replies."""

    def __init__(self, responder=None):
        self.inbox = queue.Queue()
        self.sent = []
        self.responder = responder
        self.closed = False

    def feed(self, frame):
        self.inbox.put(frame if isinstance(frame, str)
                       else json.dumps(frame))

    def send(self, msg):
        m = json.loads(msg)
        self.sent.append(m)
        if self.responder:
            for frame in self.responder(m) or []:
                self.feed(frame)

    def recv(self):
        item = self.inbox.get(timeout=10)
        if item is _CLOSE:
            raise ConnectionError("socket closed")
        return item

    def close(self):
        self.closed = True
        self.inbox.put(_CLOSE)


def _session(responder):
    """A ChromeSession wired to a FakeWS with its reader running."""
    s = chrome_cdp.ChromeSession()
    s.ws = FakeWS(responder)
    s._alive = True
    s._reader = threading.Thread(target=s._reader_loop, daemon=True)
    s._reader.start()
    return s


def _ok(mid, result=None):
    return {"id": mid, "result": result or {}}


def _eval_reply(mid, value, typ="object"):
    return {"id": mid, "result": {"result": {"type": typ, "value": value}}}


def test_reader_routes_interleaved_frames():
    """Events and a stranger's response arrive BEFORE the awaited
    response; the command must still get its own result."""
    def responder(m):
        if m["method"] == "Runtime.evaluate":
            return [
                {"method": "Page.loadEventFired", "params": {}},
                _ok(999, {"stray": True}),       # not our id: absorbed
                {"method": "Runtime.consoleAPICalled", "params": {}},
                _eval_reply(m["id"], "hello", "string"),
            ]
        return [_ok(m["id"])]
    s = _session(responder)
    val, typ = s.evaluate("document.title")
    assert (val, typ) == ("hello", "string"), (val, typ)
    s.detach()
    print("PASS reader routes interleaved frames")


def test_attach_uses_per_target_socket_no_flatten():
    """attach() must dial the target's own webSocketDebuggerUrl and
    never send Target.attachToTarget (flatten mode)."""
    target = {"id": "T1", "type": "page", "url": "https://example.com",
              "title": "Ex",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/T1"}
    opened = []
    real_targets = chrome_cdp._page_targets
    real_open = chrome_cdp.ChromeSession._open_ws
    chrome_cdp._page_targets = lambda port: [target]

    def fake_open(self, ws_url):
        opened.append(ws_url)
        return FakeWS(lambda m: [_ok(m["id"])])
    chrome_cdp.ChromeSession._open_ws = fake_open
    try:
        s = chrome_cdp.ChromeSession()
        s.attach(9333)
    finally:
        chrome_cdp._page_targets = real_targets
        chrome_cdp.ChromeSession._open_ws = real_open
    assert opened == ["ws://127.0.0.1:9333/devtools/page/T1"], opened
    methods = [f["method"] for f in s.ws.sent]
    assert not any(m.startswith("Target.") for m in methods), methods
    assert "Page.enable" in methods and "Runtime.enable" in methods
    s.detach()
    print("PASS attach: per-target socket, no flatten")


def test_cmd_timeout_fails_loud():
    s = _session(lambda m: [])   # the socket never answers
    try:
        s.cmd("Page.navigate", {"url": "x"}, timeout=0.5)
    except chrome_cdp.CDPError as e:
        assert "timeout" in str(e) and "loudly" in str(e), str(e)
        print("PASS cmd timeout fails loud")
    else:
        raise AssertionError("should have raised")
    finally:
        s.detach()


def test_cmd_error_raises():
    s = _session(lambda m: [{"id": m["id"],
                             "error": {"message": "boom"}}])
    try:
        s.cmd("Bad.method")
    except chrome_cdp.CDPError as e:
        assert "boom" in str(e)
        print("PASS cmd error")
    else:
        raise AssertionError("should have raised")
    finally:
        s.detach()


_READBACK_OBJ = {"url": "https://example.com/", "title": "Example",
                 "ready_state": "complete", "active_element": ""}


def _nav_responder(load=True, ready="complete"):
    def responder(m):
        if m["method"] == "Page.navigate":
            frames = [_ok(m["id"])]
            if load:
                frames.append({"method": "Page.loadEventFired",
                               "params": {"timestamp": 1.0}})
            return frames
        if m["method"] == "Runtime.evaluate":
            expr = m["params"]["expression"]
            if "ready_state" in expr:
                return [_eval_reply(m["id"], dict(_READBACK_OBJ))]
            if "readyState" in expr:
                return [_eval_reply(m["id"], ready, "string")]
            return [_eval_reply(m["id"], None, "undefined")]
        return [_ok(m["id"])]
    return responder


def test_navigate_proves_landing():
    s = _session(_nav_responder(load=True))
    rb = s.navigate("https://example.com/", timeout=5)
    assert rb["url"] == "https://example.com/", rb
    assert rb["title"] == "Example", rb
    s.detach()
    print("PASS navigate proves landing")


def test_navigate_without_load_fails_loud():
    s = _session(_nav_responder(load=False, ready="loading"))
    try:
        s.navigate("https://example.com/", timeout=1)
    except chrome_cdp.CDPError as e:
        assert "did not reach load" in str(e), str(e)
        print("PASS navigate without load fails loud")
    else:
        raise AssertionError("should have raised")
    finally:
        s.detach()


def test_graceful_close_sends_browser_close():
    class FakeProc:
        def __init__(self):
            self.waited = False
            self.terminated = False

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def terminate(self):
            self.terminated = True

    s = _session(lambda m: [_ok(m["id"])])
    s.owned = True
    s._proc = FakeProc()
    ws = s.ws  # close_browser() detaches (ws -> None); keep the fake
    forced = s.close_browser()
    methods = [f["method"] for f in ws.sent]
    assert "Browser.close" in methods, methods
    assert forced is False and not s._proc.terminated
    print("PASS graceful close sends Browser.close")


def test_fill_requires_readback_equality():
    state = {"value": ""}

    def responder(m):
        if m["method"] == "Runtime.evaluate":
            expr = m["params"]["expression"]
            if "window.__cdp_field = e" in expr:      # focus snippet
                return [_eval_reply(m["id"], {
                    "found": True, "field": "input[name=q]",
                    "value": state["value"]})]
            if "setter.call(e, '')" in expr:          # clear snippet
                state["value"] = ""
                return [_eval_reply(m["id"], True, "boolean")]
            if "if (!e) return null" in expr:         # value readback
                return [_eval_reply(m["id"], state["value"], "string")]
            return [_eval_reply(m["id"], None, "undefined")]
        if m["method"] == "Input.insertText":
            state["value"] += m["params"]["text"]
            return [_ok(m["id"])]
        return [_ok(m["id"])]

    s = _session(responder)
    chrome_cdp._session = s
    try:
        r = chrome_cdp.fill("proof", label="q")
        assert r == {"value": "proof", "field": "input[name=q]",
                     "verified": True}, r
        # corruption: insertText silently drops the text -> the
        # readback no longer equals what was typed -> must raise
        state["value"] = ""

        def corrupt(m):
            if m["method"] == "Runtime.evaluate":
                expr = m["params"]["expression"]
                if "window.__cdp_field = e" in expr:
                    return [_eval_reply(m["id"], {
                        "found": True, "field": "input[name=q]",
                        "value": state["value"]})]
                if "setter.call(e, '')" in expr:
                    return [_eval_reply(m["id"], True, "boolean")]
                if "if (!e) return null" in expr:
                    return [_eval_reply(m["id"], state["value"],
                                        "string")]
                return [_eval_reply(m["id"], None, "undefined")]
            return [_ok(m["id"])]  # insertText acked but never lands

        s.ws.responder = corrupt
        try:
            chrome_cdp.fill("proof", label="q")
        except chrome_cdp.CDPError as e:
            assert "readback mismatch" in str(e), str(e)
        else:
            raise AssertionError("fill should have raised on mismatch")
        print("PASS fill requires readback equality")
    finally:
        chrome_cdp._session = None
        s.detach()


def test_launch_refuses_real_chrome_profile():
    import tempfile
    real = tempfile.mkdtemp(prefix="fake-real-profile-")
    saved = chrome_cdp.real_chrome_profile_dir
    chrome_cdp.real_chrome_profile_dir = lambda: real
    try:
        chrome_cdp.launch(profile_dir=real)
    except chrome_cdp.CDPError as e:
        assert "real profile" in str(e), str(e)
        print("PASS launch refuses the real Chrome profile")
    else:
        raise AssertionError("should have refused")
    finally:
        chrome_cdp.real_chrome_profile_dir = saved


def test_session_required():
    assert chrome_cdp._session is None
    try:
        chrome_cdp.readback()
    except chrome_cdp.CDPError as e:
        assert "no CDP session" in str(e)
        print("PASS session required")
    else:
        raise AssertionError("should have raised")


def test_snippets_present():
    for name in ("_SNAPSHOT2", "_CLICK_SELECTOR", "_FOCUS_FIELD",
                 "_READBACK", "_SELECTOR_FN"):
        s = getattr(chrome_cdp, name)
        assert isinstance(s, str) and len(s) > 50, name
    assert "__cdp_selector" in chrome_cdp._SNAPSHOT2
    assert "ready_state" in chrome_cdp._READBACK
    print("PASS snippets")


if __name__ == "__main__":
    test_reader_routes_interleaved_frames()
    test_attach_uses_per_target_socket_no_flatten()
    test_cmd_timeout_fails_loud()
    test_cmd_error_raises()
    test_navigate_proves_landing()
    test_navigate_without_load_fails_loud()
    test_graceful_close_sends_browser_close()
    test_fill_requires_readback_equality()
    test_launch_refuses_real_chrome_profile()
    test_session_required()
    test_snippets_present()
    print("ALL CHROME_CDP TESTS PASS")
