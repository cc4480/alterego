# Tests for Batch 1: supervisor + hot reload.
# No docstrings in this file: it is embedded in tooling that cannot
# carry triple-quoted strings, so comments carry the explanations.

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import supervisor as sup
import hot_reload as hr


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_snapshot_detects_change(tmp_path):
    root = tmp_path / "repo"
    f = root / "pc-agent" / "foo.py"
    _write(f, "x = 1\n")
    before = hr.snapshot(root)
    assert set(before) == {"pc-agent/foo.py"}
    _write(f, "x = 2\n")
    after = hr.snapshot(root)
    assert after["pc-agent/foo.py"] != before["pc-agent/foo.py"]


def test_watcher_reports_settled_change(tmp_path):
    root = tmp_path / "repo"
    f = root / "pc-agent" / "foo.py"
    _write(f, "x = 1\n")
    w = hr.Watcher(root, poll_s=0.01, debounce_s=0.0)
    assert w.poll() == []
    _write(f, "x = 2\n")
    assert w.poll() == []
    assert w.poll() == ["pc-agent/foo.py"]


def test_watcher_debounce_holds_unsettled(tmp_path):
    root = tmp_path / "repo"
    f = root / "pc-agent" / "foo.py"
    _write(f, "x = 1\n")
    w = hr.Watcher(root, poll_s=0.01, debounce_s=60.0)
    _write(f, "x = 2\n")
    assert w.poll() == []


def test_reload_request_roundtrip(tmp_path):
    data = tmp_path / "data"
    assert sup.reload_requested(data, 0.0) is False
    sup.request_reload(data, reason="test")
    assert sup.reload_requested(data, 0.0) is True
    sup.clear_reload_request(data)
    assert sup.reload_requested(data, 0.0) is False


def test_hot_reload_state_roundtrip(tmp_path):
    data = tmp_path / "data"
    assert hr.hot_reload_enabled(data) is False
    hr.set_hot_reload(data, True)
    assert hr.hot_reload_enabled(data) is True
    hr.set_hot_reload(data, False)
    assert hr.hot_reload_enabled(data) is False


def test_supervisor_status_no_file(tmp_path):
    st = sup.supervisor_status(tmp_path / "data")
    assert st["running"] is False
    assert st["present"] is False


class _FakeAddr:
    def __init__(self, port):
        self.port = port


class _FakeConn:
    def __init__(self, port, status, pid):
        self.laddr = _FakeAddr(port)
        self.status = status
        self.pid = pid


class _FakeProc:
    def __init__(self, name, cmdline):
        self._name = name
        self._cmdline = cmdline
        self.terminated = False
        self.killed = False

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class _FakePsutil:
    def __init__(self, conns, procs):
        self._conns = conns
        self._procs = procs

    def net_connections(self, kind="tcp"):
        return self._conns

    def Process(self, pid):
        return self._procs[pid]


def test_reap_zombies_dry_run(monkeypatch):
    procs = {111: _FakeProc("python.exe", ["python", "server.py"]),
             222: _FakeProc("notepad.exe", ["notepad.exe"])}
    fake = _FakePsutil([_FakeConn(8765, "LISTEN", 111),
                        _FakeConn(8765, "LISTEN", 222)], procs)
    monkeypatch.setattr(sup, "psutil", fake)
    reaped = sup.reap_zombies(8765, exclude_pids={999}, dry_run=True)
    assert reaped == [{"pid": 111, "name": "python.exe",
                       "action": "would_terminate"}]
    assert procs[111].terminated is False


def test_supervisor_storm_breaker(tmp_path):
    data = tmp_path / "data"
    s = sup.Supervisor([sys.executable, "-c", "import sys; sys.exit(3)"],
                       cwd=str(tmp_path), data=data, port=0,
                       poll_s=0.02, max_restarts=2,
                       restart_window_s=60.0,
                       backoff_base_s=0.02, backoff_max_s=0.05)
    assert s.run_forever() == "storm_breaker"
    assert s.restarts == 2
    st = sup.supervisor_status(data)
    assert st["storm_breaker"] is True


def _kill_child(s):
    child = s._child
    if child is not None:
        try:
            if child.poll() is None:
                child.terminate()
        except OSError:
            pass


def test_supervisor_reload_restarts_child(tmp_path):
    data = tmp_path / "data"
    s = sup.Supervisor(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path), data=data, port=0, poll_s=0.02,
        max_restarts=2, restart_window_s=60.0,
        backoff_base_s=0.02, backoff_max_s=0.05)
    t = threading.Thread(target=s.run_forever, daemon=True)
    t.start()
    try:
        deadline = time.time() + 10
        while s._child is None and time.time() < deadline:
            time.sleep(0.05)
        assert s._child is not None
        first_pid = s._child.pid
        sup.request_reload(data, reason="test")
        deadline = time.time() + 10
        while s.reloads < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert s.reloads == 1
        deadline = time.time() + 10
        while (s._child is None or s._child.pid == first_pid) \
                and time.time() < deadline:
            time.sleep(0.05)
        assert s._child is not None and s._child.pid != first_pid
        # Drive the storm breaker so run_forever returns.
        for _ in range(4):
            if not t.is_alive():
                break
            _kill_child(s)
            time.sleep(0.3)
        t.join(timeout=10)
        assert not t.is_alive()
    finally:
        s._stop_child()


def test_check_supervisor_unknown_when_absent(monkeypatch, tmp_path):
    try:
        from seams.providers.windows import misc as misc_mod
    except ImportError:
        pytest.skip("windows misc provider not importable here")

    class Stub:
        @staticmethod
        def supervisor_status(data=None):
            return {"present": False, "running": False}

    monkeypatch.setitem(sys.modules, "supervisor", Stub)
    result = misc_mod.check_supervisor()
    assert result["status"] == "unknown"
    assert any(fn.__name__ == "check_supervisor" for fn in misc_mod.CHECKS)
