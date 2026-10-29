"""query_events bounded read: equivalence with the old full-replay
algorithm + proof that out-of-window day files are never opened.

The reference implementation below IS the pre-fix algorithm (replay
the whole directory via events.replay, sort everything, filter,
slice). The new query_event_log must return identical
(events, total, truncated) on fixture logs that include corrupt
lines, out-of-order timestamps, and every filter — while skipping
day files older than the since window entirely.
"""
import json
from pathlib import Path

import pytest

import event_query
import events


def _ev(i, ts, tool="list_dir", etype="ToolCalled", session="sess_a"):
    return {"v": 1, "event_id": f"evt_{i:04d}", "ts": ts, "type": etype,
            "caused_by": None, "session_id": session,
            "data": {"tool": tool}}


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _old_query(events_dir, tool=None, event_type=None, since_iso=None,
               session_id=None, limit=50):
    """The pre-fix query_event_log, verbatim in behaviour."""
    found, _ = events.replay(events_dir, from_ts=since_iso)
    out = []
    for e in sorted(found, key=lambda e: e.get("ts", "")):
        d = e.get("data", {})
        if tool and d.get("tool") != tool:
            continue
        if event_type and e.get("type") != event_type:
            continue
        if session_id and e.get("session_id") != session_id:
            continue
        out.append(e)
    return out[:limit], len(out), len(out) > limit


@pytest.fixture()
def fixture_log(tmp_path):
    """Multi-day log: a pre-window day, two in-window days with
    out-of-order lines, corrupt lines of each tolerant kind, and a
    non-date-named file (must always be read)."""
    d = tmp_path / "events"
    # Pre-window day (2026-10-20): would match filters, but is before
    # the since day — excluded by ts in BOTH implementations.
    _write(d / "2026-10-20.jsonl", [
        json.dumps(_ev(1, "2026-10-20T09:00:00+00:00")),
        json.dumps(_ev(2, "2026-10-20T10:00:00+00:00", tool="read_file")),
        "{corrupt json line",
    ])
    # In-window day 1: out-of-order ts, corrupt + invalid-envelope
    # lines interleaved.
    _write(d / "2026-10-28.jsonl", [
        json.dumps(_ev(10, "2026-10-28T12:00:00+00:00")),
        json.dumps(_ev(11, "2026-10-28T08:00:00+00:00", tool="read_file",
                       etype="ToolCompleted", session="sess_b")),
        "not json at all",
        json.dumps({"v": 1, "event_id": "evt_x", "ts":
                    "2026-10-28T09:00:00+00:00", "type": "BogusType",
                    "data": {}}),                      # unknown type: skip
        json.dumps({"v": 1, "ts": "2026-10-28T09:30:00+00:00",
                    "type": "ToolCalled", "data": {}}),  # no id: skip
        "",                                            # blank: skip
        json.dumps(_ev(12, "2026-10-28T08:00:00+00:00")),  # ts tie w/ 11
    ])
    # In-window day 2.
    _write(d / "2026-10-29.jsonl", [
        json.dumps(_ev(20, "2026-10-29T07:00:00+00:00", tool="read_file")),
        json.dumps(_ev(21, "2026-10-29T07:05:00+00:00")),
        json.dumps(_ev(22, "2026-10-29T07:10:00+00:00",
                       etype="ToolFailed")),
    ])
    # Non-date filename: always a candidate in both implementations.
    _write(d / "extra.jsonl", [
        json.dumps(_ev(30, "2026-10-29T08:00:00+00:00")),
    ])
    return str(d)


SINCE = "2026-10-28T00:00:00+00:00"

CASES = [
    {},
    {"tool": "list_dir"},
    {"tool": "read_file"},
    {"tool": "no_such_tool"},
    {"event_type": "ToolCompleted"},
    {"event_type": "ToolFailed"},
    {"session_id": "sess_b"},
    {"tool": "list_dir", "event_type": "ToolCalled"},
    {"limit": 1},
    {"limit": 2},
    {"tool": "list_dir", "limit": 2},
    {"limit": 500},
]


@pytest.mark.parametrize("kw", CASES)
def test_identical_to_old_algorithm(fixture_log, kw):
    limit = kw.pop("limit", 50)
    expected = _old_query(fixture_log, since_iso=SINCE, limit=limit, **kw)
    got = event_query.query_event_log(
        since=SINCE, limit=limit, events_dir=fixture_log, **kw)
    assert got == expected


def test_identical_with_since_none(fixture_log):
    expected = _old_query(fixture_log, since_iso=None, limit=3)
    got = event_query.query_event_log(
        since=None, limit=3, events_dir=fixture_log)
    assert got == expected


def test_window_results_are_the_in_window_matches(fixture_log):
    evs, total, truncated = event_query.query_event_log(
        since=SINCE, limit=50, events_dir=fixture_log)
    assert total == 7 and truncated is False
    assert [e["event_id"] for e in evs] == [
        "evt_0011", "evt_0012", "evt_0010",
        "evt_0020", "evt_0021", "evt_0022", "evt_0030"]


def test_large_log_bounded_read(tmp_path, monkeypatch):
    """50k-line history: 49k lines in pre-window day files, 1k in the
    window. The bounded read must return the window's matches without
    ever opening the pre-window files (instrumented, not wall-clock).
    """
    d = tmp_path / "events"
    old_lines = [json.dumps(_ev(i, "2026-10-01T00:00:00+00:00"))
                 for i in range(49000)]
    _write(d / "2026-10-01.jsonl", old_lines)
    new_lines = [json.dumps(
        _ev(50000 + i, f"2026-10-29T{i // 60:02d}:{i % 60:02d}:00+00:00",
            tool="read_file" if i % 3 == 0 else "list_dir"))
        for i in range(1000)]
    _write(d / "2026-10-29.jsonl", new_lines)

    opened = []
    real_open = Path.open

    def counting_open(self, *a, **k):
        opened.append(self.name)
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", counting_open)
    evs, total, truncated = event_query.query_event_log(
        tool="read_file", since="2026-10-28T00:00:00+00:00",
        limit=5, events_dir=str(d))
    assert "2026-10-01.jsonl" not in opened  # 49k lines never touched
    assert opened == ["2026-10-29.jsonl"]
    # Identical semantics on the window that WAS read:
    assert total == 334 and truncated is True
    assert [e["event_id"] for e in evs] == [
        "evt_50000", "evt_50003", "evt_50006", "evt_50009", "evt_50012"]


def test_unsupported_version_in_window_still_raises(tmp_path):
    """Fail-loud schema behaviour is preserved for in-window files."""
    d = tmp_path / "events"
    _write(d / "2026-10-29.jsonl", [
        json.dumps({"v": 99, "event_id": "evt_z",
                    "ts": "2026-10-29T00:00:00+00:00",
                    "type": "ToolCalled", "data": {}}),
    ])
    with pytest.raises(ValueError, match="unsupported event version"):
        event_query.query_event_log(
            since="2026-10-28T00:00:00+00:00", events_dir=str(d))
