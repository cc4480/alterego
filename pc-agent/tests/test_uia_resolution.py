"""UIA resolution discipline (plan F2, 2026-10-31).

The Windows UIA helper is a PowerShell script, so on this host the
suite stubs the transport (_uia_invoke / subproc) and tests the Python
contract around it:

- structured helper errors become typed Python errors
  (UiaAmbiguousError / UiaReadbackError) with candidates attached;
- click/set payloads carry the resolve-uniquely sentinel (-1) when no
  index is given, and enumerate generously (stopping at the first
  substring match was the 2026-10-29 Hermes battery failure: 'Message'
  resolved to an 'Add imessage' button instead of the composer);
- uia_set_text's focus fallback verifies by reading the field back;
- the PowerShell script itself is pinned structurally: tiered matching,
  Safe-Int coordinate casts (raw [int] casts on BoundingRectangle
  doubles throw on NaN/out-of-range values and kill the query), and
  pure ASCII (the helper file is written with errors='strict').

Live PowerShell semantics are exercised by the full-fire battery on
the PC, not here.
"""
import json

import pytest

from seams.providers.windows import read as win_read
from seams.providers.windows import write as win_write
from seams.providers.windows.read import (
    UIA_ENUM_MAX,
    UiaAmbiguousError,
    UiaReadbackError,
)

HERMES_CANDIDATES = [
    {"name": "Add imessage", "control_type": "ControlType.Button",
     "automation_id": "", "x": 10, "y": 10, "width": 90, "height": 24},
    {"name": "Message", "control_type": "ControlType.Edit",
     "automation_id": "composer", "x": 10, "y": 400, "width": 300,
     "height": 40},
]


def _transport_returning(payload):
    def fake_run(cmd, timeout_s, **kwargs):
        return 0, json.dumps(payload), "", False
    return fake_run


@pytest.fixture
def transport(monkeypatch):
    """Install a fake subproc transport; returns the setter."""
    def install(payload):
        monkeypatch.setattr(win_read.subproc, "run_noinherit",
                            _transport_returning(payload))
    return install


# ---------------------------------------------------------------------------
# _uia_invoke: structured helper errors become typed Python errors
# ---------------------------------------------------------------------------

def test_invoke_translates_ambiguous_target(transport):
    transport({"ok": False, "error_code": "ambiguous_target",
               "error": "ambiguous target: 2 elements match",
               "candidates": HERMES_CANDIDATES})
    with pytest.raises(UiaAmbiguousError) as ei:
        win_read._uia_invoke({"op": "click", "window": "Hermes"})
    # The candidate list is the disambiguation tool -- it must survive.
    assert ei.value.candidates == HERMES_CANDIDATES
    assert "Add imessage" in str(ei.value)
    assert "Message" in str(ei.value)


def test_invoke_translates_ambiguous_window(transport):
    transport({"ok": False, "error_code": "ambiguous_window",
               "error": "ambiguous window: 2 windows match",
               "candidates": [{"name": "Notes", "hwnd": 111},
                              {"name": "Notes", "hwnd": 222}]})
    with pytest.raises(UiaAmbiguousError) as ei:
        win_read._uia_invoke({"op": "find", "window": "Notes"})
    assert "222" in str(ei.value)


def test_invoke_translates_readback_mismatch(transport):
    transport({"ok": False, "error_code": "readback_mismatch",
               "error": "set_text readback mismatch"})
    with pytest.raises(UiaReadbackError):
        win_read._uia_invoke({"op": "set", "window": "Hermes"})


def test_invoke_translates_readback_unavailable(transport):
    transport({"ok": False, "error_code": "readback_unavailable",
               "error": "cannot read back the value"})
    with pytest.raises(UiaReadbackError):
        win_read._uia_invoke({"op": "get", "window": "Hermes"})


def test_invoke_passes_ok_payload_through(transport):
    transport({"ok": True, "count": 0, "elements": []})
    assert win_read._uia_invoke({"op": "find", "window": "x"}) == \
        {"ok": True, "count": 0, "elements": []}


def test_invoke_passes_uncoded_error_through(transport):
    # Plain failures stay dicts; callers raise RuntimeError as before.
    transport({"ok": False, "error": "window not found"})
    res = win_read._uia_invoke({"op": "find", "window": "nope"})
    assert res["ok"] is False and res["error"] == "window not found"


# ---------------------------------------------------------------------------
# uia_find: ranking passthrough + non-ASCII window titles
# ---------------------------------------------------------------------------

def test_uia_find_returns_ranked_elements(monkeypatch):
    seen = {}

    def fake_invoke(payload):
        seen.update(payload)
        return {"ok": True, "count": 2, "elements": [
            {"name": "Message", "match_tier": 1},
            {"name": "Add imessage", "match_tier": 3}]}

    monkeypatch.setattr(win_read, "_uia_invoke", fake_invoke)
    res = win_read.uia_find("Hermes", name_rx="Message")
    assert seen["op"] == "find"
    assert res["count"] == 2
    assert [e["match_tier"] for e in res["elements"]] == [1, 3]


def test_uia_find_unicode_window_title(monkeypatch):
    seen = {}

    def fake_invoke(payload):
        seen.update(payload)
        return {"ok": True, "count": 1, "elements": [
            {"name": "✳ Claude Code", "match_tier": 3}]}

    monkeypatch.setattr(win_read, "_uia_invoke", fake_invoke)
    res = win_read.uia_find("✳ Claude Code")
    assert seen["window"] == "✳ Claude Code"
    assert res["elements"][0]["name"] == "✳ Claude Code"


def test_uia_request_file_is_ascii_safe(monkeypatch):
    """The request JSON is written ensure_ascii=True: a non-ASCII
    window title must round-trip through the real temp-file path."""
    captured = {}

    def fake_run(cmd, timeout_s, **kwargs):
        req_path = cmd[-1]
        with open(req_path, "rb") as f:
            captured["raw"] = f.read()
        return 0, '{"ok": true, "count": 0, "elements": []}', "", False

    monkeypatch.setattr(win_read.subproc, "run_noinherit", fake_run)
    win_read._uia_invoke({"op": "find", "window": "✳ Claude Code",
                          "name_rx": "", "control_type": "",
                          "max_results": 50})
    captured["raw"].decode("ascii")  # must not raise
    assert json.loads(captured["raw"])["window"] == "✳ Claude Code"


# ---------------------------------------------------------------------------
# The PowerShell script itself (structural pins)
# ---------------------------------------------------------------------------

def test_ps_script_is_pure_ascii():
    win_read._UIA_PS.encode("ascii")  # writer uses errors='strict'


def test_ps_script_has_tiered_resolution():
    ps = win_read._UIA_PS
    assert "function Name-Tier" in ps
    assert "'^(?:'" in ps                    # anchored full-match tier
    assert "ambiguous_target" in ps
    assert "ambiguous_window" in ps
    assert "not_found" in ps


def test_ps_script_uses_safe_int_for_coordinates():
    ps = win_read._UIA_PS
    assert "function Safe-Int" in ps
    assert "[int]$r.X" not in ps             # the raw cast that threw
    assert "Safe-Int $r.X" in ps


def test_ps_script_verifies_set_value_readback():
    assert "readback_mismatch" in win_read._UIA_PS
    assert "readback_unavailable" in win_read._UIA_PS


# ---------------------------------------------------------------------------
# uia_click payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def write_env(monkeypatch):
    env = {"payloads": [], "typed": []}
    monkeypatch.setattr(win_write, "_approved",
                        lambda tool, summary: None)
    monkeypatch.setattr(win_write, "_send_unicode",
                        lambda text: env["typed"].append(text))
    return env


def _scripted_invoke(env, by_op):
    def fake_invoke(payload):
        env["payloads"].append(dict(payload))
        outcome = by_op[payload["op"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fake_invoke


def test_click_omitted_index_sends_resolve_sentinel(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"click": {"ok": True, "method": "invoke",
                              "name": "Send", "x": 5, "y": 6}}))
    res = win_write.uia_click("Hermes", "Send")
    payload = write_env["payloads"][0]
    assert payload["index"] == -1            # resolve uniquely or fail
    assert payload["max_results"] == UIA_ENUM_MAX
    assert res == {"ok": True, "method": "invoke", "name": "Send",
                   "x": 5, "y": 6}


def test_click_explicit_index_passes_through(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"click": {"ok": True, "method": "invoke",
                              "name": "Send", "x": 5, "y": 6}}))
    win_write.uia_click("Hermes", "Send", index=2)
    assert write_env["payloads"][0]["index"] == 2


def test_click_ambiguous_propagates(write_env, monkeypatch):
    err = UiaAmbiguousError("ambiguous target: 2 elements match",
                            HERMES_CANDIDATES)
    monkeypatch.setattr(win_write, "_uia_invoke",
                        _scripted_invoke(write_env, {"click": err}))
    with pytest.raises(UiaAmbiguousError) as ei:
        win_write.uia_click("Hermes", "Message")
    assert ei.value.candidates == HERMES_CANDIDATES


def test_click_rejects_explicit_negative_index(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"click": {"ok": True}}))
    with pytest.raises(ValueError):
        win_write.uia_click("Hermes", "Send", index=-1)
    assert write_env["payloads"] == []       # refused before any invoke


# ---------------------------------------------------------------------------
# uia_set_text: readback proof on both paths
# ---------------------------------------------------------------------------

def test_set_text_value_method_is_single_invoke(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"set": {"ok": True, "method": "value",
                            "name": "Message"}}))
    res = win_write.uia_set_text("Hermes", "Message", "hello")
    assert res == {"ok": True, "method": "value", "name": "Message"}
    assert len(write_env["payloads"]) == 1   # helper verified internally
    assert write_env["typed"] == []


def test_set_text_focus_path_readback_match(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"set": {"ok": True, "method": "focus",
                            "name": "Message"},
                    "get": {"ok": True, "name": "Message",
                            "value": "hello", "channel": "value"}}))
    res = win_write.uia_set_text("Hermes", "Message", "hello")
    assert res["ok"] is True and res["method"] == "focus"
    assert write_env["typed"] == ["hello"]
    get_payload = write_env["payloads"][1]
    assert get_payload["op"] == "get"
    assert get_payload["index"] == -1        # same targeting as the set


def test_set_text_focus_path_readback_mismatch_errors(write_env,
                                                      monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"set": {"ok": True, "method": "focus",
                            "name": "Message"},
                    "get": {"ok": True, "name": "Message",
                            "value": "typed into the wrong field",
                            "channel": "value"}}))
    with pytest.raises(UiaReadbackError):
        win_write.uia_set_text("Hermes", "Message", "hello")


def test_set_text_focus_path_readback_unavailable_errors(write_env,
                                                         monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"set": {"ok": True, "method": "focus",
                            "name": "Message"},
                    "get": UiaReadbackError("cannot read back")}))
    with pytest.raises(UiaReadbackError):
        win_write.uia_set_text("Hermes", "Message", "hello")


# ---------------------------------------------------------------------------
# Stale-window resilience: 'window not found' re-resolution
# ---------------------------------------------------------------------------

def _sequenced_invoke(env, outcomes):
    """Fake _uia_invoke returning `outcomes` in call order (last repeats)."""
    def fake_invoke(payload):
        env["payloads"].append(dict(payload))
        outcome = outcomes[min(len(env["payloads"]) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fake_invoke


_NOT_FOUND = {"ok": False, "error": "window not found"}


def test_click_title_spec_reresolves_and_retries_once(write_env,
                                                      monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [_NOT_FOUND,
                    {"ok": True, "method": "invoke", "name": "Send",
                     "x": 5, "y": 6}]))
    res = win_write.uia_click("Hermes", "Send")
    assert res["ok"] is True and res["name"] == "Send"
    assert len(write_env["payloads"]) == 2   # initial + exactly one retry
    assert all(p["window"] == "Hermes" for p in write_env["payloads"])


def test_click_title_spec_still_missing_after_retry_errors(write_env,
                                                           monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [_NOT_FOUND]))
    with pytest.raises(RuntimeError) as ei:
        win_write.uia_click("Hermes", "Send")
    assert "Hermes" in str(ei.value)
    assert "re-resolution" in str(ei.value)
    assert len(write_env["payloads"]) == 2   # retried once, no loop


def test_click_stale_hwnd_gets_specific_error_no_retry(write_env,
                                                       monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [_NOT_FOUND]))
    with pytest.raises(RuntimeError) as ei:
        win_write.uia_click("12345", "Send")
    msg = str(ei.value)
    assert "12345" in msg and "no longer resolves" in msg
    assert "list_windows" in msg
    assert len(write_env["payloads"]) == 1   # nothing to re-find by


def test_set_text_title_spec_reresolves_and_retries_once(write_env,
                                                         monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [_NOT_FOUND,
                    {"ok": True, "method": "value", "name": "Message"}]))
    res = win_write.uia_set_text("Hermes", "Message", "hello")
    assert res == {"ok": True, "method": "value", "name": "Message"}
    assert len(write_env["payloads"]) == 2


def test_set_text_stale_hwnd_gets_specific_error(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [_NOT_FOUND]))
    with pytest.raises(RuntimeError) as ei:
        win_write.uia_set_text("999", "Message", "hello")
    assert "999" in str(ei.value)
    assert "no longer resolves" in str(ei.value)
    assert len(write_env["payloads"]) == 1


def test_click_other_errors_are_not_retried(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _sequenced_invoke(
        write_env, [{"ok": False, "error": "element index out of range"}]))
    with pytest.raises(RuntimeError) as ei:
        win_write.uia_click("Hermes", "Send")
    assert "element index out of range" in str(ei.value)
    assert len(write_env["payloads"]) == 1


def test_set_text_readback_forgives_line_endings(write_env, monkeypatch):
    monkeypatch.setattr(win_write, "_uia_invoke", _scripted_invoke(
        write_env, {"set": {"ok": True, "method": "focus",
                            "name": "Message"},
                    "get": {"ok": True, "name": "Message",
                            "value": "line1\r\nline2\r\n",
                            "channel": "value"}}))
    res = win_write.uia_set_text("Hermes", "Message", "line1\nline2")
    assert res["ok"] is True
