"""Slice A — UIA resolution semantics: tiered name matching,
find/get unification, structured ambiguity (mock provider).

Fixtures are shaped like the real acceptance-battery failures
(bridge-flawless record items 5-6):

- Hermes: a Button named "Add imessage" sits next to the chat
  composer Edit named "Message". The old unanchored substring match
  resolved name_rx "Message" to the Button; the tiered contract
  (exact literal tier first) must resolve it to the Edit.
- Notepad: two unnamed Panes plus a Document "Text Editor" — the
  empty-name / multi-candidate shapes.

Semantics under test (shared resolution contract):
- Tier A = case-insensitive literal name equality; Tier B =
  case-insensitive regex FULL match; first non-empty tier wins.
  NO substring/unanchored fallback.
- control_type accepts "Edit" and "ControlType.Edit".
- find returns the winning set; get/set/click resolve through the
  same set (same enumeration, same cap), so find index i and
  get(index=i) name the same element.
- No index: 1 candidate resolves, 0 is 'element not found', >1 is a
  structured 'ambiguous target:' error naming the candidates, and a
  set against an ambiguous query mutates nothing.
- runtime_id addressing still wins; explicit index still works.
"""
import copy

import pytest

import toolcall
from seams.providers import mock as mock_state
from seams.providers.mock import UIA


def _el(name, control_type, automation_id="", runtime_id="",
        value="", patterns=None, x=0, y=0, width=10, height=10):
    return {"name": name, "control_type": control_type,
            "automation_id": automation_id, "value": value,
            "has_keyboard_focus": False, "runtime_id": runtime_id,
            "patterns": patterns or [],
            "x": x, "y": y, "width": width, "height": height,
            "corrupt_write": False}


def _hermes():
    return [
        _el("Add imessage", "ControlType.Button",
            automation_id="addImessageBtn", runtime_id="7,12345,11",
            patterns=["InvokePattern"], x=0, y=0, width=120, height=30),
        _el("Message", "ControlType.Edit",
            automation_id="messageBox", runtime_id="7,12345,12",
            patterns=["ValuePattern", "TextPattern"],
            x=0, y=40, width=300, height=30),
        _el("Send and receive iMessage texts", "ControlType.Text",
            runtime_id="7,12345,13", x=0, y=80, width=300, height=20),
    ]


def _notepad():
    return [
        _el("", "ControlType.Pane", runtime_id="7,12345,21"),
        _el("", "ControlType.Pane", runtime_id="7,12345,22"),
        _el("Text Editor", "ControlType.Document",
            automation_id="editor", runtime_id="7,12345,23",
            patterns=["ValuePattern", "TextPattern"]),
    ]


def _two_composers():
    return [
        _el("Message", "ControlType.Edit",
            automation_id="composerA", runtime_id="7,12345,31",
            patterns=["ValuePattern", "TextPattern"]),
        _el("Message", "ControlType.Edit",
            automation_id="composerB", runtime_id="7,12345,32",
            patterns=["ValuePattern", "TextPattern"]),
        _el("Send", "ControlType.Button",
            automation_id="sendBtn", runtime_id="7,12345,33",
            patterns=["InvokePattern"]),
    ]


@pytest.fixture()
def hermes():
    UIA["elements"] = copy.deepcopy(_hermes())
    return UIA["elements"]


@pytest.fixture()
def notepad():
    UIA["elements"] = copy.deepcopy(_notepad())
    return UIA["elements"]


# ---- the Hermes failure: exact tier beats the substring neighbour --------

def test_message_resolves_to_edit_not_add_imessage_button(hermes):
    r = toolcall.call("uia_get", {"window": "mock", "name_rx": "Message"})
    assert r["ok"] is True
    assert r["name"] == "Message"
    assert r["control_type"] == "ControlType.Edit"
    assert r["runtime_id"] == "7,12345,12"
    found = toolcall.call("uia_find",
                          {"window": "mock", "name_rx": "Message"})
    assert found["count"] == 1
    assert found["elements"][0]["control_type"] == "ControlType.Edit"


def test_anchored_regex_full_match_works(hermes):
    r = toolcall.call("uia_get",
                      {"window": "mock", "name_rx": "^Message$"})
    assert r["name"] == "Message"
    assert r["control_type"] == "ControlType.Edit"
    # A regex that full-matches only via the anchored tier works too.
    r2 = toolcall.call("uia_get",
                       {"window": "mock", "name_rx": "Mess.ge"})
    assert r2["runtime_id"] == "7,12345,12"


def test_substring_only_query_finds_nothing(hermes):
    # Documented semantics change: "Mess" is a substring of "Message"
    # but neither an exact name nor a full match — no fallback.
    found = toolcall.call("uia_find",
                          {"window": "mock", "name_rx": "Mess"})
    assert found["count"] == 0 and found["elements"] == []
    r = toolcall.call("uia_get", {"window": "mock", "name_rx": "Mess"})
    assert set(r.keys()) == {"error"}
    assert "element not found" in r["error"]
    assert "index out of range" not in r["error"]


def test_send_message_via_exact_tier_composer(hermes):
    UIA["focused_runtime_id"] = "7,12345,12"  # focus on the composer
    r = toolcall.call("send_message",
                      {"window": "mock", "name_rx": "Message",
                       "text": "hello hermes"}, write=True)
    assert r["ok"] is True and r["verified"] is True
    assert UIA["messages"] == ["hello hermes"]
    assert hermes[1]["value"] == ""  # composer cleared after send


# ---- control_type filter: short and ProgrammaticName forms -----------------

def test_control_type_short_and_long_forms_filter(hermes):
    for ct in ("Edit", "ControlType.Edit", "edit"):
        found = toolcall.call(
            "uia_find", {"window": "mock", "control_type": ct})
        assert [e["name"] for e in found["elements"]] == ["Message"], ct
        r = toolcall.call("uia_get", {"window": "mock",
                                      "name_rx": "Message",
                                      "control_type": ct})
        assert r["runtime_id"] == "7,12345,12", ct


# ---- find/get unification ---------------------------------------------------

def test_find_and_get_agree_for_every_index(hermes):
    found = toolcall.call("uia_find", {"window": "mock"})
    assert [e["name"] for e in found["elements"]] == [
        "Add imessage", "Message", "Send and receive iMessage texts"]
    for i, el in enumerate(found["elements"]):
        got = toolcall.call("uia_get",
                            {"window": "mock", "index": i})
        assert got["name"] == el["name"]
        assert got["runtime_id"] == el["runtime_id"]
        assert got["control_type"] == el["control_type"]


def test_find_and_get_agree_under_control_type_filter(notepad):
    found = toolcall.call("uia_find",
                          {"window": "mock", "control_type": "Pane"})
    assert found["count"] == 2
    for i, el in enumerate(found["elements"]):
        got = toolcall.call("uia_get",
                            {"window": "mock", "control_type": "Pane",
                             "index": i})
        assert got["runtime_id"] == el["runtime_id"]


# ---- ambiguity: structured error, and a set mutates nothing -----------------

def test_ambiguous_query_errors_and_set_mutates_nothing():
    UIA["elements"] = copy.deepcopy(_two_composers())
    r = toolcall.call("uia_get", {"window": "mock", "name_rx": "Message"})
    assert set(r.keys()) == {"error"}
    assert "ambiguous target:" in r["error"]
    assert "composerA" in r["error"] and "composerB" in r["error"]
    assert "7,12345,31" in r["error"] and "7,12345,32" in r["error"]

    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "Message",
                       "text": "must not land"}, write=True)
    assert set(r.keys()) == {"error"}
    assert "ambiguous target:" in r["error"]
    # No element's value was mutated by the ambiguous write.
    assert [el["value"] for el in UIA["elements"]] == ["", "", ""]

    # find still returns the whole winning set.
    found = toolcall.call("uia_find",
                          {"window": "mock", "name_rx": "Message"})
    assert found["count"] == 2


def test_unnamed_panes_are_ambiguous_without_index(notepad):
    r = toolcall.call("uia_get", {"window": "mock",
                                  "control_type": "ControlType.Pane"})
    assert set(r.keys()) == {"error"}
    assert "ambiguous target:" in r["error"]
    assert "7,12345,21" in r["error"] and "7,12345,22" in r["error"]
    # The Document resolves uniquely by name.
    doc = toolcall.call("uia_get",
                        {"window": "mock", "name_rx": "Text Editor"})
    assert doc["control_type"] == "ControlType.Document"


# ---- runtime_id and explicit index still work --------------------------------

def test_runtime_id_addressing_still_wins():
    UIA["elements"] = copy.deepcopy(_two_composers())
    # The name query alone is ambiguous; the runtime_id is the address.
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "Message",
                       "text": "by rid", "runtime_id": "7,12345,32"},
                      write=True)
    assert r["verified"] is True and r["name"] == "Message"
    assert UIA["elements"][0]["value"] == ""
    assert UIA["elements"][1]["value"] == "by rid"
    el = mock_state.find_uia_element(runtime_id="7,12345,31")
    assert el["automation_id"] == "composerA"


def test_explicit_index_still_works():
    UIA["elements"] = copy.deepcopy(_two_composers())
    r = toolcall.call("uia_set_text",
                      {"window": "mock", "name_rx": "Message",
                       "text": "second", "index": 1}, write=True)
    assert r["verified"] is True
    assert UIA["elements"][0]["value"] == ""
    assert UIA["elements"][1]["value"] == "second"
    got = toolcall.call("uia_get", {"window": "mock",
                                    "name_rx": "Message", "index": 0})
    assert got["automation_id"] == "composerA"
    out = toolcall.call("uia_get", {"window": "mock",
                                    "name_rx": "Message", "index": 2})
    assert "element index out of range" in out["error"]


# ---- contains probe (send_message confirmation) -------------------------------

def test_mock_contains_probe_searches_elements_and_messages(hermes):
    from seams.providers.mock import read as mock_read
    hermes[1]["value"] = "hello draft"
    UIA["messages"] = ["hello world", "unrelated"]
    found = mock_read.uia_find("mock", contains="hello")
    names = [e["name"] for e in found["elements"]]
    assert "Message" in names        # via its value
    assert "hello world" in names    # message-list entry
    assert "unrelated" not in names
    # Case-sensitive, matching the Python `text in name` check.
    assert mock_read.uia_find("mock", contains="Hello")["count"] == 0


# ---- Windows side: payload plumbing + helper script contract ------------------

def test_windows_payload_marks_index_given():
    from seams.providers.windows import read as win_read
    p = win_read._uia_payload("get", "Hermes", "Message", "")
    assert p["index_given"] is False and p["index"] == 0
    p2 = win_read._uia_payload("get", "Hermes", "Message", "", 2)
    assert p2["index_given"] is True and p2["index"] == 2
    # find enumerates at the unified cap; return_limit carries the
    # caller's max_results.
    assert p["max_results"] == win_read.UIA_ENUM_MAX_RESULTS == 5000


def test_windows_failure_helper_names_ambiguity_candidates():
    from seams.providers.windows import read as win_read
    res = {"ok": False, "error_code": "ambiguous_target",
           "error": "ambiguous target: 2 elements match",
           "candidates": [
               {"name": "Message", "control_type": "ControlType.Edit",
                "automation_id": "composerA", "runtime_id": "7,1,1"},
               {"name": "Message", "control_type": "ControlType.Edit",
                "automation_id": "composerB", "runtime_id": "7,1,2"}]}
    err = win_read._uia_failure(res, "fallback")
    assert str(err).startswith("ambiguous target:")
    assert "composerA" in str(err) and "composerB" in str(err)
    plain = win_read._uia_failure({"ok": False, "error": "boom"},
                                  "fallback")
    assert str(plain) == "boom"


def test_windows_helper_script_implements_tiered_resolution():
    from seams.providers.windows import read as win_read
    ps = win_read._UIA_PS
    ps.encode("ascii")  # PowerShell 5.1: pure ASCII, as before
    # Anchored tier-B pattern and tier-A literal equality.
    assert "'^(?:' + $nameRx + ')$'" in ps
    assert "$_.name -ieq $nameRx" in ps
    # The old unanchored substring name filter is gone.
    assert "$n -notmatch $req.name_rx" not in ps
    # Structured ambiguity + distinct not-found + index_given plumbing.
    assert "ambiguous_target" in ps
    assert "error='element not found'" in ps
    assert "index_given" in ps
    # Contains probe + control-type short-form matching.
    assert "$req.contains" in ps
    assert "-replace '^ControlType\\.', ''" in ps
    # find caps returned results separately from the enumeration cap.
    assert "return_limit" in ps
