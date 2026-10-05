"""Tests for exec fixes: tree kill, exec_status offset, cwd arg.

Uses the mock provider for contract tests, plus direct unit tests of the
Windows provider logic (cwd validation, offset math, _kill_tree fallback)
without spawning real processes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import toolcall
from seams.providers.mock import CALLS


def _last_call(tool):
    return [c for c in CALLS if c["tool"] == tool][-1]["args"]


# ---- exec_status offset (mock contract) -----------------------------------

def test_exec_status_offset_zero_returns_all():
    r = toolcall.call("exec_status",
                      {"job_id": "mock-job-001", "offset": 0})
    assert r["output_from_offset"] == r["output_tail"]
    assert r["output_size"] > 0
    assert r["output_size"] == len(r["output_from_offset"].encode("utf-8"))


def test_exec_status_offset_partial():
    r0 = toolcall.call("exec_status", {"job_id": "mock-job-001"})
    size = r0["output_size"]
    half = size // 2
    r1 = toolcall.call("exec_status",
                       {"job_id": "mock-job-001", "offset": half})
    assert r1["output_size"] == size
    assert len(r1["output_from_offset"].encode("utf-8")) == size - half
    # incremental chain: offset=0 then offset=size -> empty remainder
    r2 = toolcall.call("exec_status",
                       {"job_id": "mock-job-001", "offset": size})
    assert r2["output_from_offset"] == ""
    assert r2["output_size"] == size


def test_exec_status_offset_past_end_clamps():
    r = toolcall.call("exec_status",
                      {"job_id": "mock-job-001", "offset": 10 ** 9})
    assert r["output_from_offset"] == ""
    assert r["output_size"] > 0


def test_exec_status_default_offset_is_zero():
    r = toolcall.call("exec_status", {"job_id": "mock-job-001"})
    assert "output_from_offset" in r
    assert "output_size" in r


# ---- cwd arg (mock records it) --------------------------------------------

def test_shell_exec_cwd_recorded():
    toolcall.call("shell_exec", {"command": "echo hi", "cwd": "some/dir"},
                  write=True)
    assert _last_call("shell_exec")["cwd"] == "some/dir"


def test_shell_exec_cwd_defaults_none():
    toolcall.call("shell_exec", {"command": "echo hi"}, write=True)
    assert _last_call("shell_exec")["cwd"] is None


def test_shell_pwsh_cwd_recorded():
    toolcall.call("shell_pwsh", {"script": "echo hi", "cwd": "some/dir"},
                  write=True)
    assert _last_call("shell_pwsh")["cwd"] == "some/dir"


def test_exec_background_cwd_recorded():
    toolcall.call("exec_background",
                  {"command": "echo hi", "cwd": "some/dir"}, write=True)
    assert _last_call("exec_background")["cwd"] == "some/dir"


# ---- Windows provider unit tests (no real processes) -----------------------

def _win_support():
    from seams.providers.windows import support
    return support


def test_resolve_cwd_none_passthrough():
    support = _win_support()
    assert support._resolve_cwd(None) is None


def test_resolve_cwd_outside_profile_rejected(monkeypatch):
    support = _win_support()
    monkeypatch.setenv("PC_BRIDGE_PROFILE_ROOT", "/tmp/profile-root")
    with pytest.raises(ValueError, match="outside your user profile"):
        support._resolve_cwd("/etc")


def test_resolve_cwd_not_a_dir_rejected(monkeypatch, tmp_path):
    support = _win_support()
    monkeypatch.setenv("PC_BRIDGE_PROFILE_ROOT", str(tmp_path))
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        support._resolve_cwd(str(f))


def test_resolve_cwd_valid_dir(monkeypatch, tmp_path):
    support = _win_support()
    monkeypatch.setenv("PC_BRIDGE_PROFILE_ROOT", str(tmp_path))
    sub = tmp_path / "sub"
    sub.mkdir()
    assert support._resolve_cwd(str(sub)) == str(sub.resolve())


def test_kill_tree_falls_back_to_popen_kill(monkeypatch):
    """When taskkill is unavailable, _kill_tree still kills via Popen."""
    support = _win_support()
    calls = []

    class FakeProc:
        pid = 99999

        def kill(self):
            calls.append("popen_kill")

    def fake_run(*a, **k):
        raise OSError("taskkill not found")

    monkeypatch.setattr(support.subprocess, "run", fake_run)
    support._kill_tree(FakeProc())
    assert calls == ["popen_kill"]


def test_kill_tree_prefers_taskkill(monkeypatch):
    """taskkill /T /F is invoked with the process PID."""
    support = _win_support()
    seen = []

    class FakeProc:
        pid = 4242

        def kill(self):
            seen.append("popen_kill")

    def fake_run(cmd, **k):
        seen.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(support.subprocess, "run", fake_run)
    support._kill_tree(FakeProc())
    assert ["taskkill", "/PID", "4242", "/T", "/F"] in seen


def test_exec_cancel_unknown_job_mock_canned():
    # The mock never validates job ids — it returns the canned envelope.
    r = toolcall.call("exec_cancel", {"job_id": "definitely-not-a-job"},
                      write=True)
    assert r["status"] == "cancelled"


def test_exec_cancel_unknown_job_real_provider():
    # The real provider raises on unknown job ids.
    support = _win_support()
    with pytest.raises(ValueError, match="unknown job_id"):
        support.exec_cancel("definitely-not-a-job")


# ---- real job lifecycle (Windows provider code, runs on Linux too) --------

def test_real_background_lifecycle(monkeypatch):
    """exec_background -> exec_status(offset) -> exec_cancel on real procs.

    _kill_tree falls back to Popen.kill where taskkill is absent (Linux).
    Uses a sleep so the job is still running when cancelled.
    """
    support = _win_support()
    monkeypatch.setattr(support, "_approved", lambda tool, summary: None)
    r = support.exec_background("sleep 30", timeout_s=60)
    jid = r["job_id"]
    assert r["status"] == "started"
    s0 = support.exec_status(jid, offset=0)
    assert s0["status"] == "running"
    assert s0["output_size"] >= 0
    # poll incrementally: second read starts where the first ended
    s1 = support.exec_status(jid, offset=s0["output_size"])
    assert s1["output_size"] == s0["output_size"]
    assert s1["output_from_offset"] == ""
    c = support.exec_cancel(jid)
    assert c["status"] == "cancelled"
    # job table records the cancellation
    s2 = support.exec_status(jid)
    assert s2["status"] == "cancelled"


def test_real_background_cwd_validation(monkeypatch, tmp_path):
    support = _win_support()
    monkeypatch.setattr(support, "_approved", lambda tool, summary: None)
    monkeypatch.setenv("PC_BRIDGE_PROFILE_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="outside your user profile"):
        support.exec_background("echo hi", cwd="/etc")
