"""Slice D — input reliability: honest focus, UIA read-back, verified
target-bound typing, and the send_message composite (mock provider).

Contracts mirrored from the Windows provider:
- focus_window failure is an ERROR naming the actual foreground window.
- uia_set_text reads back and raises on mismatch.
- type_text/hotkey with a target run a focus gate; a failing gate
  types nothing. Bare calls are labelled verified:false.
- send_message confirms value cleared + text in message list + focus
  still on the control; any failed check is an error.
"""
import pytest

import toolcall
from seams.providers import mock as mock_state
from seams.providers.mock import UIA


def _composer():
    return UIA["elements"][0]


# ---- Q5: honest focus_window ---------------------------------------------

def test_focus_window_success():
    r = toolcall.call("focus_window", {"hwnd": 12346}, write=True)
    assert r == {"hwnd": 12346, "focused": True}
    assert UIA["foreground_hwnd"] == 12346


def test_focus_window_failure_is_error_naming_actual_foreground():
    UIA["foreground_hwnd"] = 99999  # Competing App holds the foreground
    UIA["focus_fail"] = True
    r = toolcall.call("focus_window", {"hwnd": 12345}, write=True)
    assert set(r.keys()) == {"error"}
    assert "99999" in r["error"] and "Competing App" in r["error"]
    assert UIA["foreground_hwnd"] == 99999  # unchanged


# ---- Q6: UIA read-back ----------------------------------------------------

def test_uia_get_returns_fields():
    toolcall.call("uia_set_text",
                  {"window": "mock", "name_rx": "Message", "text": "hey"},
                  write=True)
    r = toolcall.call("uia_get", {"window": "mock", "name_rx": "Message"})
    assert r["ok"] is True
    assert r["value"] == "hey" and r["text"] == "hey"
    assert r["has_keyboard_focus"] is True
    assert r["runtime_id"] == "7,12345,1"
    assert "ValuePattern" in r["patterns"]
    assert r["name"] == "Message"


def test_uia_find_elements_carry_new_fields():
    r = toolcall.call("uia_find", {"window": "mock"})
    el = r["elements"][0]
    for key in ("value", "has_keyboard_focus", "runtime_id"):
        assert key in el, key
    assert el["runtime_id"] == "7,12345,1"


def test_uia_set_text_verified_roundtrip():
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello"}, write=True)
    assert r["verified"] is True and r["value_after"] == "hello"
    assert _composer()["value"] == "hello"


def test_uia_set_text_mismatch_raises():
    _composer()["corrupt_write"] = True  # set lands doubled (tonight's bug)
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "verification failed" in r["error"]
    assert "hellohello" in r["error"]  # read-back value is named


# ---- S4: verified target-bound typing -------------------------------------

def test_type_text_bare_is_labelled_unverified():
    r = toolcall.call("type_text", {"text": "hi"}, write=True)
    assert r == {"typed_chars": 2, "verified": False}


def test_type_text_targeted_verified_and_lands():
    r = toolcall.call("type_text",
                      {"text": "abc", "window": "Mock Window",
                       "name_rx": "Message"}, write=True)
    assert r == {"typed_chars": 3, "verified": True}
    assert _composer()["value"] == "abc"


def test_type_text_failing_gate_types_nothing():
    UIA["focused_runtime_id"] = "7,99999,9"  # focus is elsewhere
    r = toolcall.call("type_text",
                      {"text": "abc", "window": "Mock Window",
                       "name_rx": "Message"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "focus gate failed" in r["error"]
    assert _composer()["value"] == ""  # nothing landed
    assert not any(c["tool"] == "type_text" for c in mock_state.CALLS)


def test_type_text_wrong_window_gate_types_nothing():
    UIA["foreground_hwnd"] = 99999  # Competing App in front
    r = toolcall.call("type_text",
                      {"text": "abc", "window": "Mock Window",
                       "name_rx": "Message"}, write=True)
    assert set(r.keys()) == {"error"}
    assert _composer()["value"] == ""


def test_hotkey_bare_unverified_and_targeted_verified():
    bare = toolcall.call("hotkey", {"keys": "enter"}, write=True)
    assert bare == {"hotkey": "enter", "pressed": True, "verified": False}
    tgt = toolcall.call("hotkey",
                        {"keys": "enter", "window": "Mock Window",
                         "name_rx": "Message"}, write=True)
    assert tgt["verified"] is True


def test_hotkey_failing_gate_presses_nothing():
    UIA["foreground_hwnd"] = 12346  # Mock Console, not the target window
    r = toolcall.call("hotkey",
                      {"keys": "enter", "window": "Mock Window"},
                      write=True)
    assert set(r.keys()) == {"error"}
    assert not any(c["tool"] == "hotkey" for c in mock_state.CALLS)


# ---- S4: send_message composite -------------------------------------------

def test_send_message_happy_path_confirmation():
    r = toolcall.call("send_message",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello there"}, write=True)
    assert r["ok"] is True and r["verified"] is True
    assert r["checks"] == {"value_cleared": True,
                            "in_message_list": True,
                            "focus_on_control": True}
    assert _composer()["value"] == ""
    assert UIA["messages"] == ["hello there"]


def test_send_message_disturbed_focus_errors_without_typing():
    UIA["focused_runtime_id"] = "7,99999,9"  # focus yanked mid-sequence
    r = toolcall.call("send_message",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "focus gate failed" in r["error"]
    assert _composer()["value"] == ""  # nothing was typed into it
    assert UIA["messages"] == []  # and nothing was sent


def test_send_message_dropped_message_is_error_not_success():
    UIA["drop_sent_message"] = True  # Enter lands nowhere
    r = toolcall.call("send_message",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "confirmation failed" in r["error"]
    assert "in_message_list" in r["error"]
