# Tests for Batch 3: parallel batch execution + job progress reporting.
# conftest pins the mock provider and dontAsk mode before imports.

import threading
import time

import pytest

import jobs
import toolcall
from seams.providers.windows import support as win_support


# --- batch partitioning ---------------------------------------------------

def test_partition_groups_shared_resources():
    plan = [("screenshot", {}), ("list_windows", {}), ("system_info", {}),
            ("browser_navigate", {}), ("browser_click", {}),
            ("uia_find", {})]
    chains = win_support._partition_batch(plan, jobs.RESOURCE_MAP)
    assert chains == [[0, 1, 5], [2], [3, 4]]


def test_partition_unknown_tool_is_singleton():
    plan = [("no_such_tool", {}), ("screenshot", {})]
    assert win_support._partition_batch(plan, jobs.RESOURCE_MAP) == [[0], [1]]


# --- batch execution (toolcall.call stubbed; registry/threads are real) ----

def _slow_stub(name, args, write=False, caused_by=None):
    time.sleep(0.3)
    return {"ok": True, "tool": name}


def test_batch_runs_independent_calls_concurrently(monkeypatch):
    monkeypatch.setattr(toolcall, "call", _slow_stub)
    calls = [{"tool": "system_info", "args": {}},
             {"tool": "idle_seconds", "args": {}},
             {"tool": "list_processes", "args": {}}]
    started = time.monotonic()
    r = win_support.batch(calls)
    elapsed = time.monotonic() - started
    assert r["calls"] == 3
    assert [e["tool"] for e in r["results"]] == [
        "system_info", "idle_seconds", "list_processes"]
    assert all(e["result"]["ok"] for e in r["results"])
    assert elapsed < 0.75  # sequential floor is 0.9s


def test_batch_serializes_shared_resource_calls(monkeypatch):
    monkeypatch.setattr(toolcall, "call", _slow_stub)
    calls = [{"tool": "screenshot", "args": {}},
             {"tool": "list_windows", "args": {}}]
    started = time.monotonic()
    r = win_support.batch(calls)
    elapsed = time.monotonic() - started
    assert [e["tool"] for e in r["results"]] == [
        "screenshot", "list_windows"]
    assert elapsed >= 0.6  # one chain: 2 x 0.3s sequential


def test_batch_unknown_tool_is_error_envelope(monkeypatch):
    monkeypatch.setattr(toolcall, "call", _slow_stub)
    r = win_support.batch([{"tool": "no_such_tool", "args": {}},
                           {"tool": "system_info", "args": {}}])
    assert r["results"][0] == {"tool": "no_such_tool",
                               "error": "unknown tool: no_such_tool"}
    assert r["results"][1]["result"]["ok"] is True


def test_batch_validation():
    with pytest.raises(ValueError):
        win_support.batch([])
    with pytest.raises(ValueError):
        win_support.batch([{"tool": "batch", "args": {}}])


# --- job progress ----------------------------------------------------------

def _make_manager(tmp_path, executor, **kw):
    kw.setdefault("watchdog_interval_s", 0.1)
    return jobs.JobManager(db_path=str(tmp_path / "jobs.db"),
                           executor=executor, **kw)


def test_report_progress_visible_while_running(tmp_path):
    release = threading.Event()

    def executor(tool, args, ctx):
        ctx.report_progress("indexing files", 3, 7)
        release.wait(timeout=5)
        return {"ok": True}

    mgr = _make_manager(tmp_path, executor)
    try:
        job_id = mgr.submit("system_info", {})["job_id"]
        deadline = time.monotonic() + 5
        st = None
        while time.monotonic() < deadline:
            st = mgr.status(job_id)
            if (st["state"] == "running"
                    and st.get("progress", {}).get("message")
                    == "indexing files"):
                break
            time.sleep(0.02)
        assert st["state"] == "running"
        prog = st["progress"]
        assert prog["message"] == "indexing files"
        assert prog["current"] == 3 and prog["total"] == 7
        assert isinstance(st["elapsed_s"], float) and st["elapsed_s"] >= 0
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            st = mgr.status(job_id)
            if st["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert st["state"] == "succeeded"
        assert isinstance(st["elapsed_s"], float) and st["elapsed_s"] >= 0
    finally:
        release.set()
        mgr.shutdown()


def test_job_context_snapshot_defaults():
    ctx = jobs.JobContext("job_x")
    snap = ctx.snapshot()
    assert snap["message"] is None
    assert snap["current"] is None and snap["total"] is None
    ctx.report_progress("hello")
    snap = ctx.snapshot()
    assert snap["message"] == "hello"
    assert snap["current"] is None and snap["total"] is None


def test_report_progress_validates_numbers():
    ctx = jobs.JobContext("job_x")
    with pytest.raises(ValueError):
        ctx.report_progress("x", current="three")
    with pytest.raises(ValueError):
        ctx.report_progress("x", total="seven")


def test_report_progress_resets_stall_timer():
    ctx = jobs.JobContext("job_x")
    time.sleep(0.15)
    assert ctx.idle_s() >= 0.1
    ctx.report_progress("still here")
    assert ctx.idle_s() < 0.1


def test_elapsed_on_finished_job(tmp_path):
    mgr = _make_manager(tmp_path, lambda t, a, c: {"ok": True})
    try:
        job_id = mgr.submit("system_info", {})["job_id"]
        deadline = time.monotonic() + 5
        st = None
        while time.monotonic() < deadline:
            st = mgr.status(job_id)
            if st["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert st["state"] == "succeeded"
        assert isinstance(st["elapsed_s"], float) and st["elapsed_s"] >= 0
    finally:
        mgr.shutdown()
