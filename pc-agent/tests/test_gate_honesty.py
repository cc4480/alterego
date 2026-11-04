"""Gate honesty: ApprovalRequested reports consulted gates, never predictions.

The pipeline takes the approval gate's own record (pop_gate_record):
shown dialogs log shown=True with the real outcome; posture skips log
shown=False with the skip reason; providers that never consult the gate
log no ApprovalRequested at all. Mock provider, no UI — the Win32 layer
is stubbed wherever a shown dialog is exercised.
"""
import approval
import event_types
import toolcall
from conftest import read_events

import pytest


@pytest.fixture(autouse=True)
def clean_gate_state():
    approval.pop_gate_record()
    approval._RECENT_TIMEOUTS.clear()
    yield
    approval.pop_gate_record()


@pytest.fixture()
def default_mode(monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "default")
    monkeypatch.setattr(approval, "AUTO_APPROVE", False)
    monkeypatch.setattr(approval, "FULL_ACCESS", False)


def _approval_events():
    return [e for e in read_events() if e["type"] == "ApprovalRequested"]


def test_dontask_skip_recorded_without_dialog():
    assert approval.request_approval("honesty-skip", tier="routine",
                                     tool_name="type_text") is True
    rec = approval.pop_gate_record()
    assert rec == {"shown": False, "result": None, "skipped": "dontAsk",
                   "tier": "routine", "tool": "type_text"}
    assert approval.pop_gate_record() is None  # consume-once


def test_shown_outcomes_recorded(default_mode, monkeypatch):
    monkeypatch.setattr(approval, "_show_dialog_once",
                        lambda *a: (True, False))
    assert approval.request_approval("honesty-yes") is True
    assert approval.pop_gate_record()["result"] == "yes"
    monkeypatch.setattr(approval, "_show_dialog_once",
                        lambda *a: (False, False))
    assert approval.request_approval("honesty-no") is False
    rec = approval.pop_gate_record()
    assert (rec["shown"], rec["result"]) == (True, "no")
    monkeypatch.setattr(approval, "_show_dialog_once",
                        lambda *a: (False, True))
    assert approval.request_approval("honesty-timeout") is False
    rec = approval.pop_gate_record()
    assert (rec["shown"], rec["result"]) == (True, "timeout")


def test_timeout_cache_deny_records_no_dialog(default_mode, monkeypatch):
    monkeypatch.setattr(approval, "_show_dialog_once",
                        lambda *a: (False, True))
    assert approval.request_approval("honesty-cached") is False
    assert approval.pop_gate_record()["shown"] is True
    assert approval.request_approval("honesty-cached") is False
    rec = approval.pop_gate_record()
    assert (rec["shown"], rec["result"]) == (False, "no")


def test_consulted_gate_emits_factual_event():
    # Simulate a provider that consulted and was skipped: seed the record.
    assert approval.request_approval("honesty-seed", tier="ask",
                                     tool_name="type_text") is True
    toolcall.call("type_text", {"text": "hi"}, write=True)
    evts = _approval_events()
    assert len(evts) == 1
    d = evts[0]["data"]
    assert (d["tier"], d["dialog_shown"], d["dialog_result"],
            d["skipped_reason"]) == ("ask", False, None, "dontAsk")
    called = [e for e in read_events() if e["type"] == "ToolCalled"]
    assert evts[0]["caused_by"] == called[0]["event_id"]


def test_never_consulted_logs_no_event():
    toolcall.call("macro_record",
                  {"name": "m1",
                   "steps": [{"tool": "system_info", "args": {}}]},
                  write=True)
    toolcall.call("macro_delete", {"name": "m1"}, write=True)
    assert _approval_events() == []
    done = [e for e in read_events() if e["type"] == "ToolCompleted"]
    assert len(done) == 2  # the calls ran; only the claim is gone


def test_read_path_discards_stray_record():
    approval.request_approval("honesty-stray")  # seeds (dontAsk skip)
    toolcall.call("idle_seconds", {})
    assert approval.pop_gate_record() is None
    assert _approval_events() == []


def test_approval_requested_schema_accepts_factual_shapes():
    assert event_types.validate("ApprovalRequested", {
        "tier": "routine", "dialog_shown": False, "dialog_result": None,
        "skipped_reason": "dontAsk"}) == []
    assert event_types.validate("ApprovalRequested", {
        "tier": "always_ask", "dialog_shown": True,
        "dialog_result": "timeout"}) == []
    assert event_types.validate("WsConnected", {"client": "muse"}) == []
    assert event_types.validate("WsDisconnected", {}) != []
