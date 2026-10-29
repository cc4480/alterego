"""Wave 2 Slice H — browser dialog guard + clean receipts, paste_text
verified-write bar, and UIA runtime_id / single-enumeration addressing.

Browser tests follow test_browser_cdp.py's pattern (a fake Browser is
monkeypatched into browser_cdp; no Edge needed). Paste/UIA tests run
through toolcall.call with the mock provider. The Windows PowerShell
helper cannot run on this host, so its addressing/read-back logic is
covered at the payload-builder level plus source-level checks on the
helper script (the live-PC retest is named in the slice report).
"""
import pytest

import browser_cdp
import toolcall
from seams.providers import mock as mock_state
from seams.providers.mock import UIA


# ---- 1. browser dialog guard + clean error receipts -----------------------

class _FakeBrowser:
    def __init__(self, page_url, eval_value=None, eval_error=None):
        self.page_url = page_url
        self._eval_value = eval_value
        self._eval_error = eval_error
        self.eval_calls = 0
        self.closed = False

    def eval(self, js):
        self.eval_calls += 1
        if self._eval_error is not None:
            raise self._eval_error
        return self._eval_value, "string"

    def close(self):
        self.closed = True


def _patch_browser(monkeypatch, fake):
    monkeypatch.setattr(browser_cdp, "Browser",
                        lambda url_contains=None: fake)
    return fake


def test_internal_url_classifier():
    assert browser_cdp._is_internal_url("edge://sync-confirmation-dialog/")
    assert browser_cdp._is_internal_url("edge://settings")
    assert browser_cdp._is_internal_url("chrome://extensions")
    assert browser_cdp._is_internal_url("about:config")
    assert not browser_cdp._is_internal_url("about:blank")
    assert not browser_cdp._is_internal_url("https://example.com")
    assert not browser_cdp._is_internal_url("")


def test_browser_click_dialog_guard_fires(monkeypatch):
    fake = _patch_browser(monkeypatch, _FakeBrowser(
        "edge://sync-confirmation-dialog/", eval_value="CLICKED:X"))
    r = browser_cdp.click_text("More information")
    assert r["status"] == "blocked_dialog"
    assert r["result"] == "BLOCKED"
    assert r["page_url"] == "edge://sync-confirmation-dialog/"
    assert "edge://sync-confirmation-dialog/" in r["detail"]
    assert fake.eval_calls == 0  # never acted on the dialog surface
    assert fake.closed


def test_browser_fill_dialog_guard_fires(monkeypatch):
    fake = _patch_browser(monkeypatch, _FakeBrowser(
        "edge://sync-confirmation-dialog/", eval_value="FILLED:x"))
    r = browser_cdp.fill_field("Search", "pneuma")
    assert r["status"] == "blocked_dialog"
    assert "edge://sync-confirmation-dialog/" in r["detail"]
    assert fake.eval_calls == 0


def test_browser_snapshot_dialog_guard_fires(monkeypatch):
    fake = _patch_browser(monkeypatch, _FakeBrowser(
        "chrome://settings", eval_value={"url": "chrome://settings",
                                         "title": "x", "elements": ["a"],
                                         "cross_origin_iframes": 0}))
    r = browser_cdp.snapshot()
    assert r["status"] == "blocked_dialog"
    assert r["elements"] == []
    assert "chrome://settings" in r["detail"]
    assert fake.eval_calls == 0


def test_browser_snapshot_about_blank_is_not_blocked(monkeypatch):
    snap = {"url": "about:blank", "title": "", "elements": [],
            "cross_origin_iframes": 0}
    _patch_browser(monkeypatch,
                   _FakeBrowser("about:blank", eval_value=snap))
    r = browser_cdp.snapshot()
    assert r["status"] == "ok" and r["detail"] is None
    assert r["url"] == "about:blank"


def test_browser_fill_not_found_stays_not_found(monkeypatch):
    _patch_browser(monkeypatch, _FakeBrowser(
        "https://example.com", eval_value="NOT-FOUND"))
    r = browser_cdp.fill_field("Search", "pneuma")
    assert r == {"page_url": "https://example.com", "result": "NOT-FOUND",
                 "status": "not_found", "detail": None}


def test_browser_click_success_is_ok(monkeypatch):
    _patch_browser(monkeypatch, _FakeBrowser(
        "https://example.com", eval_value="CLICKED:More information [page]"))
    r = browser_cdp.click_text("More information")
    assert r["status"] == "ok" and r["detail"] is None
    assert r["result"].startswith("CLICKED:")


def test_browser_fill_cdp_exception_is_clean_receipt(monkeypatch):
    # The live failure: fill on a missing field surfaced the raw
    # exception as "CDPError: Uncaught". It must come back as the
    # structured receipt, message preserved in detail.
    _patch_browser(monkeypatch, _FakeBrowser(
        "https://example.com",
        eval_error=browser_cdp.CDPError("Uncaught")))
    r = browser_cdp.fill_field("Search", "pneuma")
    assert set(r.keys()) == {"page_url", "result", "status", "detail"}
    assert r["status"] == "error"
    assert r["result"] == "ERROR"
    assert r["detail"] == "Uncaught"
    assert "CDPError" not in r["result"]
    assert "CDPError: Uncaught" not in str(r["status"])


def test_browser_click_cdp_exception_is_clean_receipt(monkeypatch):
    _patch_browser(monkeypatch, _FakeBrowser(
        "https://example.com",
        eval_error=browser_cdp.CDPError("Uncaught")))
    r = browser_cdp.click_text("More information")
    assert r["status"] == "error" and r["detail"] == "Uncaught"


# ---- 2. paste_text verified-write bar (mock) -------------------------------

def _composer():
    return UIA["elements"][0]


def test_paste_text_verified_landing():
    r = toolcall.call("paste_text", {"text": "hello paste"}, write=True)
    assert r == {"pasted_chars": 11, "verified": True}
    assert "hello paste" in _composer()["value"]


def test_paste_text_landed_but_different_is_not_verified():
    UIA["paste_corrupt"] = True  # different text lands
    r = toolcall.call("paste_text", {"text": "hello paste"}, write=True)
    assert r == {"pasted_chars": 11, "verified": False}
    assert "hello paste" not in _composer()["value"]


def test_paste_text_no_readback_available_is_unverified():
    UIA["focused_runtime_id"] = "7,99999,9"  # focus resolves to nothing
    r = toolcall.call("paste_text", {"text": "hello paste"}, write=True)
    assert r == {"pasted_chars": 11, "verified": False}


def test_paste_text_focus_without_value_pattern_is_unverified():
    # Focus sits on the Send button (InvokePattern only): no value or
    # text to read back, so the receipt must not imply success.
    UIA["focused_runtime_id"] = "7,12345,2"
    r = toolcall.call("paste_text", {"text": "hello paste"}, write=True)
    assert r["verified"] is False
    assert UIA["elements"][1]["value"] == ""


# ---- 2b. paste_text read-back logic (Windows provider, unit) ---------------

def test_windows_paste_readback_logic(monkeypatch):
    from seams.providers.windows import write as win_write

    def foc(payload):
        return {"ok": True, "value": "prefix hello paste suffix",
                "text": ""}

    monkeypatch.setattr(win_write, "_uia_invoke", foc)
    assert win_write._paste_readback("hello paste") is True

    monkeypatch.setattr(win_write, "_uia_invoke",
                        lambda payload: {"ok": True, "value": "other",
                                         "text": "other"})
    assert win_write._paste_readback("hello paste") is False

    monkeypatch.setattr(win_write, "_uia_invoke",
                        lambda payload: {"ok": False})
    assert win_write._paste_readback("hello paste") is False

    def boom(payload):
        raise RuntimeError("UIA helper failed")

    monkeypatch.setattr(win_write, "_uia_invoke", boom)
    assert win_write._paste_readback("hello paste") is False


# ---- 3. UIA addressing: runtime_id + one enumeration (mock) ----------------

def test_find_then_click_by_runtime_id_lands_on_same_element():
    found = toolcall.call("uia_find", {"window": "mock"})
    target = found["elements"][1]
    assert target["name"] == "Send" and target["runtime_id"]
    r = toolcall.call("uia_click",
                      {"window": "mock", "name_rx": "",
                       "runtime_id": target["runtime_id"]}, write=True)
    assert r["ok"] is True and r["name"] == "Send"
    # Centre of the fixture element (220,20,60x30).
    assert (r["x"], r["y"]) == (250, 35)
    call = [c for c in mock_state.CALLS if c["tool"] == "uia_click"][-1]
    assert call["args"]["runtime_id"] == target["runtime_id"]


def test_find_then_set_text_by_runtime_id_lands_on_same_element():
    found = toolcall.call("uia_find", {"window": "mock"})
    target = found["elements"][0]
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "", "text": "by rid",
                       "runtime_id": target["runtime_id"]}, write=True)
    assert r["verified"] is True and r["name"] == "Message"
    assert _composer()["value"] == "by rid"


def test_index_path_click_lands_on_find_element_at_that_index():
    found = toolcall.call("uia_find", {"window": "mock"})
    assert [e["name"] for e in found["elements"]] == ["Message", "Send"]
    r = toolcall.call("uia_click",
                      {"window": "mock", "name_rx": "", "index": 1},
                      write=True)
    assert r["name"] == found["elements"][1]["name"] == "Send"
    r0 = toolcall.call("uia_click",
                       {"window": "mock", "name_rx": "", "index": 0},
                       write=True)
    assert r0["name"] == found["elements"][0]["name"] == "Message"


def test_index_path_set_text_lands_on_find_element():
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "", "text": "indexed",
                       "index": 0}, write=True)
    assert r["verified"] is True and r["name"] == "Message"
    assert _composer()["value"] == "indexed"


def test_uia_click_out_of_range_errors_clearly():
    r = toolcall.call("uia_click",
                      {"window": "mock", "name_rx": "", "index": 5},
                      write=True)
    assert set(r.keys()) == {"error"}
    assert "element index out of range" in r["error"]


def test_uia_click_unknown_runtime_id_errors_clearly():
    r = toolcall.call("uia_click",
                      {"window": "mock", "name_rx": "",
                       "runtime_id": "7,12345,404"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "runtime_id not found" in r["error"]


def test_uia_set_text_unknown_runtime_id_errors_clearly():
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "", "text": "hi",
                       "runtime_id": "7,12345,404"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "runtime_id not found" in r["error"]


# ---- 3b. Windows payload builder + helper-script addressing ----------------

def test_windows_uia_payload_uses_one_enumeration():
    from seams.providers.windows import read as win_read

    # Index path: same cap as uia_find's clamp, never index+1.
    p = win_read._uia_payload("click", "Notepad", "File", "", 3)
    assert p["max_results"] == win_read.UIA_ENUM_MAX_RESULTS == 200
    assert p["index"] == 3 and p["runtime_id"] == ""
    # RuntimeId path: filters cleared, rid is the sole address.
    p2 = win_read._uia_payload("set", "Notepad", "StaleName",
                               "ControlType.Edit", 7,
                               runtime_id="7,42,1", text="hi")
    assert p2["runtime_id"] == "7,42,1"
    assert p2["name_rx"] == "" and p2["control_type"] == ""
    assert p2["max_results"] == 200 and p2["text"] == "hi"


def test_windows_uia_helper_script_addresses_by_runtime_id():
    from seams.providers.windows import read as win_read

    ps = win_read._UIA_PS
    # The helper is written out as strict ASCII for PowerShell 5.1.
    ps.encode("ascii")
    # Selection by RuntimeId exists and precedes the click/set ops.
    assert "element runtime_id not found" in ps
    assert ps.index("ridWanted") < ps.index("$op -eq 'click'")
    # The focused op carries value/text for paste read-back and no
    # longer depends on a window lookup.
    assert "value=(El-Value $fe)" in ps
    focused_block = ps[ps.index("$req.op -eq 'focused'"):]
    assert focused_block.index("exit 0") < focused_block.index(
        "Find-Window $req.window")
