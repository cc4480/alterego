# Tests for Batch 2: desktop_state + wait_for (mock pipeline + provider units).
# conftest pins the mock provider and dontAsk mode before imports.

import pytest

import toolcall
from seams.providers.mock import read as mock_read
from seams.providers.windows import read as win_read


def test_mock_desktop_state_via_pipeline():
    r = toolcall.call("desktop_state", {})
    assert r["windows"][0]["title"] == "Mock Window"
    assert r["focused"]["title"] == "Mock Window"
    assert r["elements"][0]["name"] == "mock"
    assert r["screenshot"]["png_base64"]
    assert r["errors"] == {}


def test_mock_desktop_state_no_screenshot():
    r = toolcall.call("desktop_state", {"include_screenshot": False})
    assert r["screenshot"] is None
    assert r["windows"]


def test_mock_wait_for_window_found():
    r = toolcall.call("wait_for", {"kind": "window", "target": "Mock"})
    assert r["found"] is True
    assert r["attempts"] == 1


def test_mock_wait_for_miss():
    r = toolcall.call("wait_for", {"kind": "element",
                                   "target": "zzz-no-such-element"})
    assert r["found"] is False


def test_mock_wait_for_bad_kind_raises():
    with pytest.raises(ValueError):
        mock_read.wait_for("nope", "x")


def test_wait_for_finds_after_retries(monkeypatch):
    calls = {"n": 0}

    def probe(kind, target, window, control_type):
        calls["n"] += 1
        if calls["n"] < 3:
            return False, {"count": 0}
        return True, {"count": 1, "elements": [{"name": "x"}]}

    monkeypatch.setattr(win_read, "_wait_probe", probe)
    r = win_read.wait_for("element", "x", timeout_s=5, interval_s=0.1)
    assert r["found"] is True
    assert r["attempts"] == 3


def test_wait_for_timeout(monkeypatch):
    monkeypatch.setattr(win_read, "_wait_probe",
                        lambda *a: (False, {"count": 0}))
    r = win_read.wait_for("window", "Nope", timeout_s=0.3,
                          interval_s=0.1)
    assert r["found"] is False
    assert r["attempts"] >= 3
    assert r["waited_s"] >= 0.25


def test_wait_for_bad_args():
    with pytest.raises(ValueError):
        win_read.wait_for("nope", "x")
    with pytest.raises(ValueError):
        win_read.wait_for("window", "[bad(")
    with pytest.raises(ValueError):
        win_read.wait_for("window", "x", timeout_s=0)
    with pytest.raises(ValueError):
        win_read.wait_for("window", "")


def test_desktop_state_composes_sections(monkeypatch):
    monkeypatch.setattr(
        win_read, "list_windows",
        lambda: {"windows": [{"hwnd": 7, "pid": 1, "title": "T"}]})
    monkeypatch.setattr(
        win_read, "_focused_window",
        lambda: {"hwnd": 7, "pid": 1, "title": "T"})
    monkeypatch.setattr(
        win_read, "uia_find",
        lambda **kw: {"ok": True, "count": 1,
                      "elements": [{"name": "b"}]})
    monkeypatch.setattr(
        win_read, "screenshot",
        lambda **kw: {"png_base64": "AA", "width": 2, "height": 2})
    r = win_read.desktop_state()
    assert r["windows"][0]["title"] == "T"
    assert r["focused"]["hwnd"] == 7
    assert r["elements"] == [{"name": "b"}]
    assert r["screenshot"]["width"] == 2
    assert r["errors"] == {}


def test_desktop_state_section_failure_is_reported(monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(win_read, "list_windows", _boom)
    monkeypatch.setattr(win_read, "_focused_window", lambda: None)
    r = win_read.desktop_state(include_screenshot=False)
    assert r["windows"] == []
    assert "windows" in r["errors"]
    assert "focused" in r["errors"]
    assert r["screenshot"] is None
