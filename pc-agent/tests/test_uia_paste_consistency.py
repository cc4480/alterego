"""Slice C -- paste_text read-back truthfulness + find/get consistency.

The live defect (wave-2 acceptance, Win11 Notepad): paste_text
delivered its text verbatim, but its read-back reported
verified:false because it only read the FOCUSED element -- in
Notepad keyboard focus sits on a pattern-less client Pane while the
text lives in a sibling Document control. The verdict must reflect
reality both ways: a landed paste -> true; a failed/corrupt paste
-> false.

(a)-(c) run through toolcall.call with the mock provider, whose
paste/read-back mirror the Windows provider's expect-escalation
(focused -> window search; the mock's flat element list is one
window). (d) asserts uia_find/uia_get agreement on a mixed
Hermes-shaped fixture. The Windows helper itself cannot run on this
host, so its escalation is covered at the _paste_readback unit level
plus source-level checks on the PowerShell script.
"""
import toolcall
from seams.providers.mock import UIA


def _el(name, control_type, runtime_id, value="", patterns=None):
    return {"name": name, "control_type": control_type,
            "automation_id": "", "value": value,
            "has_keyboard_focus": False, "runtime_id": runtime_id,
            "patterns": list(patterns or []),
            "x": 10, "y": 20, "width": 200, "height": 30,
            "corrupt_write": False}


def _set_elements(elements, focused_runtime_id):
    UIA["elements"] = elements
    UIA["focused_runtime_id"] = focused_runtime_id


def _notepad_fixture():
    """Focused pattern-less Pane + sibling Document hosting text."""
    pane = _el("Text editor", "ControlType.Pane", "7,12345,10",
               value="", patterns=[])
    doc = _el("", "ControlType.Document", "7,12345,11",
              value="", patterns=["TextPattern"])
    _set_elements([pane, doc], pane["runtime_id"])
    return pane, doc


# ---- (a) Notepad-shaped paste: landed in the Document -> true ------------

def test_paste_notepad_shape_verified_true():
    pane, doc = _notepad_fixture()
    r = toolcall.call("paste_text", {"text": "hello notepad"},
                      write=True)
    assert r == {"pasted_chars": 13, "verified": True}
    # The text really landed -- in the Document, not the focused Pane.
    assert "hello notepad" in doc["value"]
    assert pane["value"] == ""


def test_mock_focused_readback_reports_window_source():
    _pane, doc = _notepad_fixture()
    doc["value"] = "prefix hello notepad suffix"
    from seams.providers.mock.read import uia_focused
    foc = uia_focused(expect="hello notepad")
    assert foc["ok"] is True
    assert foc["verified_via"] == "window"
    assert foc["source_control_type"] == "ControlType.Document"
    assert foc["source_name"] == doc["name"]
    # No expect: exactly the usual fields, no verified_via (parity
    # with the Windows helper's backward-compatible response).
    plain = uia_focused()
    assert plain["ok"] is True and "verified_via" not in plain
    # Expect that is nowhere in the window: verified_via "none".
    assert uia_focused(expect="absent text")["verified_via"] == "none"


# ---- (b) corrupt paste -> false -------------------------------------------

def test_paste_corrupt_notepad_shape_verified_false():
    _pane, doc = _notepad_fixture()
    UIA["paste_corrupt"] = True  # different text lands in the Document
    r = toolcall.call("paste_text", {"text": "hello notepad"},
                      write=True)
    assert r == {"pasted_chars": 13, "verified": False}
    assert "hello notepad" not in doc["value"]
    assert "<different text>" in doc["value"]


# ---- (c) dropped paste: no element contains the text -> false -------------

def test_paste_dropped_no_container_verified_false():
    # Focus on the pattern-less Pane, but the window has no
    # Document/Edit sibling to receive the paste: it drops.
    pane = _el("Text editor", "ControlType.Pane", "7,12345,10",
               value="", patterns=[])
    button = _el("Send", "ControlType.Button", "7,12345,12",
                 value="", patterns=["InvokePattern"])
    _set_elements([pane, button], pane["runtime_id"])
    r = toolcall.call("paste_text", {"text": "hello notepad"},
                      write=True)
    assert r == {"pasted_chars": 13, "verified": False}
    assert all("hello notepad" not in (el["value"] or "")
               for el in UIA["elements"])


# ---- (d) find/get consistency on a mixed Hermes-shaped fixture ------------

def test_find_get_agree_on_mixed_hermes_fixture():
    add = _el("Add imessage", "ControlType.Button", "7,12345,21",
              patterns=["InvokePattern"])
    edit = _el("Message", "ControlType.Edit", "7,12345,22",
               patterns=["ValuePattern", "TextPattern"])
    label = _el("Messages", "ControlType.Text", "7,12345,23",
                patterns=["TextPattern"])
    _set_elements([add, edit, label], edit["runtime_id"])

    # Written against current (substring, case-insensitive) matching
    # semantics: "Message" also substring-matches "Add imessage", so
    # find's first hit need not be the Edit -- the invariant under
    # test is that find and get, given the same query, address the
    # SAME element.
    found = toolcall.call("uia_find",
                          {"window": "mock", "name_rx": "Message"})
    got = toolcall.call("uia_get",
                        {"window": "mock", "name_rx": "Message"})
    assert found["count"] >= 1 and got["ok"] is True
    assert found["elements"][0]["runtime_id"] == got["runtime_id"]

    # Anchored query unambiguously addresses the Edit; find/get must
    # still agree, on the Edit itself.
    found2 = toolcall.call("uia_find",
                           {"window": "mock", "name_rx": "^Message$"})
    got2 = toolcall.call("uia_get",
                         {"window": "mock", "name_rx": "^Message$"})
    assert found2["elements"][0]["runtime_id"] == \
        got2["runtime_id"] == edit["runtime_id"]


# ---- Windows provider: _paste_readback trusts the escalation --------------

def test_windows_paste_readback_escalation(monkeypatch):
    from seams.providers.windows import write as win_write

    seen = {}

    def foc_window(payload):
        seen.update(payload)
        return {"ok": True, "value": None, "text": "",
                "verified_via": "window",
                "source_name": "",
                "source_control_type": "ControlType.Document"}

    monkeypatch.setattr(win_write, "_uia_invoke", foc_window)
    assert win_write._paste_readback("hello paste") is True
    # The pasted text is passed to the helper as the expect field.
    assert seen["expect"] == "hello paste"

    monkeypatch.setattr(
        win_write, "_uia_invoke",
        lambda payload: {"ok": True, "value": "", "text": "",
                         "verified_via": "ancestor"})
    assert win_write._paste_readback("hello paste") is True

    monkeypatch.setattr(
        win_write, "_uia_invoke",
        lambda payload: {"ok": True, "value": "", "text": "",
                         "verified_via": "none"})
    assert win_write._paste_readback("hello paste") is False

    # Focused containment still verifies on its own (no escalation).
    monkeypatch.setattr(
        win_write, "_uia_invoke",
        lambda payload: {"ok": True, "value": "xx hello paste yy",
                         "text": "", "verified_via": "focused"})
    assert win_write._paste_readback("hello paste") is True


def test_windows_focused_helper_escalation_source():
    from seams.providers.windows import read as win_read

    ps = win_read._UIA_PS
    ps.encode("ascii")  # PowerShell 5.1: the helper stays pure ASCII
    focused = ps[ps.index("$req.op -eq 'focused'"):]
    focused = focused[:focused.index("Find-Window $req.window")]
    # The escalation lives inside the focused op block only.
    assert "expect" in focused
    assert "RawViewWalker" in focused  # ancestor walk (GetParent)
    # The descendant escalation uses the same provider-backed
    # FindAll as the main enumeration (walker DFS retired — it
    # missed lazily-realized Chromium subtrees).
    assert "$winEl.FindAll(" in focused
    assert "verified_via" in focused
    assert "'ancestor'" in focused and "'window'" in focused
    assert "source_name" in focused and "source_control_type" in focused
    # Backward compatibility: no-expect requests exit with the usual
    # response before any escalation field is added.
    assert focused.index("$expect -eq ''") < focused.index(
        "$resp['verified_via']")
