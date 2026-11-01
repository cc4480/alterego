"""Browser refs (Batch 6, item 4): cdp_snapshot attaches stable refs in
both providers, refs resolve only for the LAST snapshot, stale refs fail
loud, and ref is exclusive with selector/label.

The Windows provider path is exercised with a stubbed chrome_cdp session
(the real CDP websocket cannot run on this Linux VM); approval runs in
the conftest-pinned dontAsk mode, so _approved passes without a dialog.
"""
import pytest

import browser_refs
import toolcall
from seams import definitions
from seams.providers import mock as mock_state
from seams.providers.mock import browser as mock_browser
from seams.providers.windows import browser as win_browser


@pytest.fixture(autouse=True)
def _clear_refs():
    browser_refs.clear()
    yield
    browser_refs.clear()


# ---- the store: last snapshot only ---------------------------------------

def test_store_attaches_refs_in_order():
    els = browser_refs.store_snapshot([
        {"selector": "#a", "tag": "button"},
        {"selector": "#b", "tag": "input"},
    ])
    assert [e["ref"] for e in els] == ["e1", "e2"]
    assert els[0]["selector"] == "#a"  # original keys preserved
    assert browser_refs.resolve("e1") == "#a"
    assert browser_refs.resolve("e2") == "#b"


def test_store_replaces_previous_snapshot():
    browser_refs.store_snapshot([{"selector": "#old"}])
    browser_refs.store_snapshot([{"selector": "#new"}])
    assert browser_refs.resolve("e1") == "#new"
    # A second snapshot with fewer elements: the old e2 is stale.
    browser_refs.store_snapshot([{"selector": "#x"}, {"selector": "#y"}])
    browser_refs.store_snapshot([{"selector": "#only"}])
    with pytest.raises(ValueError, match="re-snapshot"):
        browser_refs.resolve("e2")


def test_resolve_unknown_and_empty_ref_fails_loud():
    browser_refs.store_snapshot([{"selector": "#a"}])
    with pytest.raises(ValueError, match="re-snapshot"):
        browser_refs.resolve("e99")
    with pytest.raises(ValueError, match="re-snapshot"):
        browser_refs.resolve("e0")
    with pytest.raises(ValueError):
        browser_refs.resolve("")


def test_elements_without_selector_get_no_ref():
    els = browser_refs.store_snapshot([
        {"tag": "div"},
        {"selector": "#a", "tag": "button"},
    ])
    assert "ref" not in els[0]
    assert els[1]["ref"] == "e1"


# ---- mock provider: refs on snapshot, ref addressing ----------------------

def test_mock_snapshot_attaches_refs():
    r = mock_browser.cdp_snapshot()
    assert [e["ref"] for e in r["elements"]] == ["e1", "e2"]
    assert r["elements"][0]["selector"] == "#go"
    assert r["elements"][1]["selector"] == "input[name=q]"
    assert set(r.keys()) == set(
        definitions.by_name("cdp_snapshot").result_keys)


def test_mock_click_by_ref_uses_resolved_selector():
    mock_browser.cdp_snapshot()
    r = mock_browser.cdp_click(ref="e2")
    assert r["clicked"] == "CLICKED:input[name=q] (mock)"
    assert mock_state.CALLS[-1] == {
        "tool": "cdp_click",
        "args": {"selector": "input[name=q]", "text": ""}}


def test_mock_type_by_ref_uses_resolved_selector():
    mock_browser.cdp_snapshot()
    r = mock_browser.cdp_type("hello", ref="e2")
    assert r["value"] == "hello"
    assert mock_state.CALLS[-1]["args"]["selector"] == "input[name=q]"


def test_mock_ref_xor_selector_and_label():
    mock_browser.cdp_snapshot()
    with pytest.raises(ValueError, match="exclusive"):
        mock_browser.cdp_click(selector="#go", ref="e1")
    with pytest.raises(ValueError, match="exclusive"):
        mock_browser.cdp_click(text="Go", ref="e1")
    with pytest.raises(ValueError, match="exclusive"):
        mock_browser.cdp_type("x", selector="#go", ref="e1")
    with pytest.raises(ValueError, match="exclusive"):
        mock_browser.cdp_type("x", label="q", ref="e1")


def test_mock_stale_ref_fails_before_recording():
    mock_browser.cdp_snapshot()
    mock_browser.cdp_snapshot()  # same canned elements, fresh mapping
    # e3 never existed in either snapshot.
    with pytest.raises(ValueError, match="re-snapshot"):
        mock_browser.cdp_click(ref="e3")
    # A ref valid only for an older, replaced snapshot: store a 1-element
    # snapshot directly, then the 2-element mock snapshot replaces it.
    browser_refs.store_snapshot([{"selector": "#solo"}])
    browser_refs.store_snapshot([{"selector": "#a"}, {"selector": "#b"}])
    with pytest.raises(ValueError, match="re-snapshot"):
        mock_browser.cdp_click(ref="e3")


def test_ref_through_pipeline():
    result = toolcall.call("cdp_snapshot", {}, write=False)
    assert [e["ref"] for e in result["elements"]] == ["e1", "e2"]
    result = toolcall.call("cdp_click", {"selector": "", "text": "",
                                         "ref": "e1"}, write=True)
    assert result["clicked"] == "CLICKED:#go (mock)"
    result = toolcall.call("cdp_click", {"selector": "", "text": "",
                                        "ref": "e9"}, write=True)
    assert set(result.keys()) == {"error"}
    assert "re-snapshot" in result["error"]


# ---- defs: ref arg present, result keys unchanged --------------------------

def test_defs_carry_ref_arg():
    click = definitions.by_name("cdp_click")
    assert set(click.args) == {"selector", "text", "ref"}
    assert click.result_keys == ["clicked", "url", "title", "changed"]
    typ = definitions.by_name("cdp_type")
    assert set(typ.args) == {"text", "selector", "label", "clear_first",
                             "ref"}
    assert typ.result_keys == ["value", "field", "verified"]


# ---- windows provider: ref resolution ahead of the CDP call ---------------

def test_windows_click_by_ref(monkeypatch):
    seen = {}

    def _fake_snapshot():
        return {"page_url": "https://example.com", "url": "https://example.com",
                "title": "T",
                "elements": [{"selector": "#go", "tag": "button",
                              "kind": "submit", "label": "Go",
                              "context": "page"}],
                "cross_origin_iframes": 0}

    def _fake_click(selector, text):
        seen["selector"] = selector
        seen["text"] = text
        return {"clicked": "CLICKED:Go", "url": "https://example.com",
                "title": "T", "changed": False}

    monkeypatch.setattr(win_browser.chrome_cdp, "snapshot", _fake_snapshot)
    monkeypatch.setattr(win_browser.chrome_cdp, "click", _fake_click)
    r = win_browser.cdp_snapshot()
    assert r["elements"][0]["ref"] == "e1"
    r = win_browser.cdp_click(ref="e1")
    assert seen == {"selector": "#go", "text": ""}
    assert r["clicked"] == "CLICKED:Go"


def test_windows_type_by_ref(monkeypatch):
    seen = {}

    def _fake_fill(text, selector, label, clear_first):
        seen.update(text=text, selector=selector, label=label)
        return {"value": text, "field": selector, "verified": True}

    monkeypatch.setattr(win_browser.chrome_cdp, "fill", _fake_fill)
    browser_refs.store_snapshot([{"selector": "input[name=q]"}])
    r = win_browser.cdp_type("hello", ref="e1")
    assert seen == {"text": "hello", "selector": "input[name=q]",
                    "label": ""}
    assert r["verified"] is True


def test_windows_stale_ref_fails_before_approval_or_cdp(monkeypatch):
    called = []

    def _fake_click(selector, text):
        called.append(1)
        return {"clicked": "x", "url": "", "title": "", "changed": False}

    monkeypatch.setattr(win_browser.chrome_cdp, "click", _fake_click)
    with pytest.raises(ValueError, match="re-snapshot"):
        win_browser.cdp_click(ref="e1")
    assert called == []
