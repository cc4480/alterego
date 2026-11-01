"""Batch 5: elicitation, change subscriptions, resources, Apps widget.

Elicitation and change state are module-level and shared by both
providers, so every test starts from a reset store. Resources are
tested through the pure bridge_resources module under the mock
provider; the @mcp.resource / Apps wiring itself is probed in the
scratch venv (server.py cannot be imported without mcp).
"""
import json
import threading
import time

import pytest

import changes
import elicitations
import toolcall
from bridge_resources import (dashboard_state, file_text, health_text,
                              state_text, windows_text)
from dashboard_ui import DASHBOARD_HTML, DASHBOARD_URI


@pytest.fixture(autouse=True)
def _fresh_state():
    elicitations.reset()
    changes.reset()
    yield
    elicitations.reset()
    changes.reset()


# ---- elicitation -----------------------------------------------------------

def test_elicit_input_via_pipeline():
    res = toolcall.call("elicit_input",
                        {"question": "Which window?",
                         "options": ["A", "B"]}, write=False)
    assert res["status"] == "pending"
    assert res["elicitation_id"].startswith("elic_")
    assert res["question"] == "Which window?"
    assert res["options"] == ["A", "B"]
    assert set(res) == {"elicitation_id", "question", "options",
                        "status", "tool", "argument"}


def test_elicit_input_validation_direct():
    with pytest.raises(ValueError, match="question"):
        elicitations.create_elicitation("", ["A", "B"])
    with pytest.raises(ValueError, match="2 to 10"):
        elicitations.create_elicitation("Q?", ["only"])
    with pytest.raises(ValueError, match="unique"):
        elicitations.create_elicitation("Q?", ["A", "A"])
    with pytest.raises(ValueError, match="unknown tool"):
        elicitations.create_elicitation("Q?", ["A", "B"],
                                        tool="no_such_tool")
    with pytest.raises(ValueError, match="no argument"):
        elicitations.create_elicitation("Q?", ["A", "B"],
                                        tool="list_windows",
                                        argument="nope")
    with pytest.raises(ValueError, match="requires a tool"):
        elicitations.create_elicitation("Q?", ["A", "B"],
                                        argument="window")


def test_elicit_input_validation_via_pipeline_is_error_envelope():
    res = toolcall.call("elicit_input",
                        {"question": "Q?", "options": ["only"]},
                        write=False)
    assert "error" in res


def test_answer_elicitation_lifecycle():
    created = toolcall.call("elicit_input",
                            {"question": "Pick one?",
                             "options": ["A", "B"],
                             "tool": "uia_find", "argument": "window"},
                            write=False)
    eid = created["elicitation_id"]
    answered = toolcall.call("answer_elicitation",
                             {"elicitation_id": eid, "choice": "B"},
                             write=False)
    assert answered == {"elicitation_id": eid, "question": "Pick one?",
                        "choice": "B", "status": "answered"}
    listed = toolcall.call("list_elicitations", {"status": "answered"},
                           write=False)
    assert listed["total"] == 1
    assert listed["elicitations"][0]["answer"] == "B"
    pending = toolcall.call("list_elicitations", {"status": "pending"},
                            write=False)
    assert pending["total"] == 0
    double = toolcall.call("answer_elicitation",
                           {"elicitation_id": eid, "choice": "A"},
                           write=False)
    assert "error" in double
    unknown = toolcall.call("answer_elicitation",
                            {"elicitation_id": "elic_nope",
                             "choice": "A"}, write=False)
    assert "error" in unknown
    off_list = toolcall.call("elicit_input",
                             {"question": "Q?", "options": ["A", "B"]},
                             write=False)
    bad = toolcall.call("answer_elicitation",
                        {"elicitation_id": off_list["elicitation_id"],
                         "choice": "C"}, write=False)
    assert "error" in bad


def test_elicitation_journaled_in_event_log():
    toolcall.call("elicit_input",
                  {"question": "Q?", "options": ["A", "B"]}, write=False)
    res = toolcall.call("query_events",
                        {"type": "ElicitationCreated", "limit": 10},
                        write=False)
    assert res["total"] >= 1


# ---- change journal + watches -----------------------------------------------

def test_poll_changes_empty_then_sequenced():
    res = toolcall.call("poll_changes", {}, write=False)
    assert res == {"changes": [], "count": 0, "total": 0, "next_seq": 0}
    changes.record_change("file", "pc://files/seed.txt", {"x": 1})
    changes.record_change("file", "pc://files/seed.txt", {"x": 2})
    page = toolcall.call("poll_changes", {"since_seq": 1, "limit": 1},
                         write=False)
    assert page["count"] == 1
    assert page["changes"][0]["seq"] == 2
    assert page["total"] == 2
    assert page["next_seq"] == 2
    beyond = toolcall.call("poll_changes", {"since_seq": 99}, write=False)
    assert beyond["changes"] == []


def test_watch_unwatch_via_pipeline():
    watched = toolcall.call("watch_path", {"path": "seed.txt"},
                            write=False)
    assert watched["watching"] is True
    assert watched["path"].endswith("seed.txt")
    assert len(watched["fingerprint"]) == 64
    listed = toolcall.call("list_watches", {}, write=False)
    assert listed["count"] == 1
    dropped = toolcall.call("unwatch_path",
                            {"path": watched["path"]}, write=False)
    assert dropped == {"path": watched["path"], "watching": False}
    assert toolcall.call("list_watches", {}, write=False)["count"] == 0
    again = toolcall.call("unwatch_path", {"path": "seed.txt"},
                          write=False)
    assert "error" in again
    missing = toolcall.call("watch_path", {"path": "no_such_file.txt"},
                            write=False)
    assert "error" in missing


def test_window_change_recorded(monkeypatch):
    monkeypatch.setattr(changes, "_window_fingerprint", lambda: "w1")
    assert changes.check_once() == []
    monkeypatch.setattr(changes, "_window_fingerprint", lambda: "w2")
    entries = changes.check_once()
    assert len(entries) == 1
    assert entries[0]["kind"] == "windows"
    assert entries[0]["uri"] == "pc://desktop/windows"
    assert changes.check_once() == []


def test_file_change_recorded(monkeypatch):
    toolcall.call("watch_path", {"path": "seed.txt"}, write=False)
    assert changes.check_once() == []
    real_fp = changes._file_fingerprint
    monkeypatch.setattr(changes, "_file_fingerprint",
                        lambda p: "0" * 64)
    entries = changes.check_once()
    assert len(entries) == 1
    assert entries[0]["kind"] == "file"
    assert "seed.txt" in entries[0]["uri"]
    monkeypatch.setattr(changes, "_file_fingerprint", real_fp)
    flipped_back = changes.check_once()
    assert len(flipped_back) == 1
    assert flipped_back[0]["kind"] == "file"
    assert changes.check_once() == []


def test_watch_loop_runs_and_stops():
    stop = threading.Event()
    thread = threading.Thread(target=changes.watch_loop, args=(0.02,),
                              kwargs={"stop_event": stop}, daemon=True)
    thread.start()
    time.sleep(0.06)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_start_watcher_idempotent():
    assert changes.start_watcher(interval_s=60) is True
    assert changes.start_watcher(interval_s=60) is False


# ---- resources (pure module, mock provider) ----------------------------------

def test_health_resource():
    data = json.loads(health_text())
    assert set(data) == {"checks", "summary", "counts"}
    assert data["counts"]["ok"] >= 1


def test_windows_resource():
    data = json.loads(windows_text())
    assert data["total"] == 2
    assert [w["title"] for w in data["windows"]] == [
        "Mock Window", "Mock Console"]


def test_state_resource_has_no_screenshot():
    data = json.loads(state_text())
    assert "windows" in data
    assert data["screenshot"] is None


def test_file_resource_and_jailing():
    assert isinstance(file_text("seed.txt"), str)
    with pytest.raises(FileNotFoundError):
        file_text("no_such_file.txt")
    with pytest.raises(ValueError):
        file_text("../escape.txt")


def test_dashboard_state_and_tool():
    state = dashboard_state()
    assert state["ui_resource_uri"] == DASHBOARD_URI
    assert state["windows_count"] == 2
    assert state["window_titles"][0] == "Mock Window"
    res = toolcall.call("dashboard", {}, write=False)
    assert set(res) == {"title", "summary", "health", "windows_count",
                        "window_titles", "ui_resource_uri"}
    assert res == state


def test_tool_finder_via_pipeline():
    res = toolcall.call("tool_finder", {"query": "window"}, write=False)
    assert res["total"] >= 1
    assert any(t["name"] == "list_windows" for t in res["tools"])
    assert set(res) == {"query", "count", "total", "tools"}
    assert all("pc://" not in t["name"] and "/" not in t["name"]
               for t in res["tools"])


# ---- dashboard widget HTML ----------------------------------------------------

def test_dashboard_html_is_static_panel():
    assert DASHBOARD_HTML.startswith("<!doctype html>")
    assert "AlterEgo Bridge" in DASHBOARD_HTML
    assert "dashboard" in DASHBOARD_HTML
    assert "pc://bridge/health" in DASHBOARD_HTML
    assert DASHBOARD_URI == "ui://alterego/dashboard.html"
