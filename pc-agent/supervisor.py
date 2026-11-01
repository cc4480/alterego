"""Supervisor for the pc-mcp-bridge server (Batch 1: hot reload + watchdog).

The supervisor owns one child ``python pc-agent/server.py`` process. It:

- starts the child and restarts it when it exits or when its heartbeat
  goes stale (a hung process may still hold the port),
- reaps stray python processes holding the listen port before each
  start, so a zombie from a bad shutdown can never squat the port,
- restarts the child on a reload request (see hot_reload.py) so new
  code goes live without a manual restart ritual,
- stops restarting after too many crashes in a short window (storm
  breaker) instead of spinning forever,
- writes supervisor.json in the data dir so ``supervisor_status`` and
  ``doctor`` can report on it without new IPC.

This module imports only stdlib + psutil and no bridge code, so the
supervisor stays alive even when server code is broken. Tokens survive
a restart: the server reloads them from disk, and the tunnel is a
separate process the supervisor never touches.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a declared dependency
    psutil = None

DEFAULT_PORT = 8765
STATUS_FILE = "supervisor.json"
SENTINEL_FILE = "reload.request"
STATE_FILE = "hotreload.json"
HEARTBEAT_FILE = "heartbeat.txt"
CHILD_LOG_FILE = "server-stdout.log"


def data_dir(explicit=None):
    """Bridge data dir: auth.app_dir() when importable, else %APPDATA%."""
    if explicit:
        return Path(explicit)
    try:
        from auth import app_dir
        return Path(app_dir())
    except Exception:
        return Path(os.environ.get("APPDATA") or Path.home()) / "pc-mcp-bridge"


def _json_path(data, name):
    d = data_dir(data)
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def status_path(data=None):
    return _json_path(data, STATUS_FILE)


def sentinel_path(data=None):
    return _json_path(data, SENTINEL_FILE)


def state_path(data=None):
    return _json_path(data, STATE_FILE)


def heartbeat_path(data=None):
    return data_dir(data) / HEARTBEAT_FILE


def request_reload(data=None, reason="manual"):
    """Ask the supervisor to restart the server with fresh code."""
    p = sentinel_path(data)
    p.write_text(json.dumps({"requested_ts": time.time(),
                             "reason": reason}) + "\n",
                 encoding="utf-8")
    return {"reloading": True, "reason": reason,
            "sentinel": str(p)}


def reload_requested(data=None, since_ts=0.0):
    """True when a reload sentinel is newer than ``since_ts``."""
    try:
        p = sentinel_path(data)
        if not p.exists():
            return False
        return p.stat().st_mtime > since_ts
    except OSError:
        return False


def clear_reload_request(data=None):
    try:
        sentinel_path(data).unlink()
    except OSError:
        pass


def heartbeat_age_s(data=None):
    """Seconds since the server last wrote its heartbeat, or None."""
    try:
        return time.time() - heartbeat_path(data).stat().st_mtime
    except OSError:
        return None


def _proc_alive(pid):
    if not pid:
        return False
    if psutil is not None:
        try:
            return psutil.Process(int(pid)).is_running()
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def supervisor_status(data=None):
    """Read the supervisor status file. Never raises."""
    try:
        raw = status_path(data).read_text(encoding="utf-8")
        st = json.loads(raw)
    except Exception as e:  # noqa: BLE001 - status read must not crash
        return {"running": False, "present": False,
                "detail": f"no supervisor status file ({type(e).__name__})",
                "heartbeat_age_s": heartbeat_age_s(data)}
    pid = st.get("supervisor_pid")
    alive = _proc_alive(pid)
    hb = heartbeat_age_s(data)
    return {"running": bool(alive), "present": True,
            "supervisor_pid": pid, "child_pid": st.get("child_pid"),
            "restarts": st.get("restarts", 0),
            "reloads": st.get("reloads", 0),
            "last_exit_code": st.get("last_exit_code"),
            "last_exit_ts": st.get("last_exit_ts"),
            "storm_breaker": bool(st.get("storm_breaker", False)),
            "heartbeat_age_s": hb,
            "updated_ts": st.get("updated_ts"),
            "port": st.get("port", DEFAULT_PORT),
            "detail": ("supervisor process alive" if alive
                       else "supervisor process not running")}


def find_port_holders(port, exclude_pids=()):
    """Processes LISTENing on ``port``. Returns [{pid, name, cmdline}]."""
    if psutil is None or not port:
        return []
    exclude = {int(p) for p in exclude_pids if p}
    holders = []
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:
        return []
    for conn in conns:
        try:
            if not conn.laddr or conn.laddr.port != port:
                continue
            if conn.status != "LISTEN" or not conn.pid:
                continue
            if int(conn.pid) in exclude:
                continue
            proc = psutil.Process(int(conn.pid))
            holders.append({"pid": int(conn.pid),
                            "name": proc.name(),
                            "cmdline": " ".join(proc.cmdline())})
        except Exception:
            continue
    return holders


def reap_zombies(port, exclude_pids=(), dry_run=False):
    """Terminate stray python processes holding ``port``.

    Only python.exe / pythonw.exe / python processes are ever touched,
    and never the supervisor itself or its child (pass their pids in
    ``exclude_pids``). Returns a list of {pid, name, action} dicts.
    """
    reaped = []
    for holder in find_port_holders(port, exclude_pids):
        name = (holder.get("name") or "").lower()
        if "python" not in name:
            continue
        pid = holder["pid"]
        if dry_run:
            reaped.append({"pid": pid, "name": holder["name"],
                           "action": "would_terminate"})
            continue
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
                action = "terminated"
            except psutil.TimeoutExpired:
                proc.kill()
                action = "killed"
        except Exception as e:  # noqa: BLE001 - report, keep going
            action = f"failed: {type(e).__name__}"
        reaped.append({"pid": pid, "name": holder["name"],
                       "action": action})
    return reaped


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Supervisor:
    """Own one child server process; restart on exit, stall, or reload."""

    def __init__(self, command, cwd, data=None, port=DEFAULT_PORT,
                 poll_s=1.0, heartbeat_stale_s=180.0,
                 max_restarts=5, restart_window_s=60.0,
                 backoff_base_s=1.0, backoff_max_s=30.0):
        self.command = list(command)
        self.cwd = str(cwd)
        self.data = data_dir(data)
        self.port = port
        self.poll_s = poll_s
        self.heartbeat_stale_s = heartbeat_stale_s
        self.max_restarts = max_restarts
        self.restart_window_s = restart_window_s
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.restarts = 0
        self.reloads = 0
        self.last_exit_code = None
        self.last_exit_ts = None
        self.storm_breaker = False
        self._crash_times = deque()
        self._child = None
        self._child_started_ts = 0.0
        self._child_started_mono = 0.0

    # -- status ---------------------------------------------------------
    def _write_status(self):
        st = {"supervisor_pid": os.getpid(),
              "started_ts": time.time(),
              "child_pid": self._child.pid if self._child else None,
              "child_started_ts": self._child_started_ts,
              "restarts": self.restarts,
              "reloads": self.reloads,
              "last_exit_code": self.last_exit_code,
              "last_exit_ts": self.last_exit_ts,
              "storm_breaker": self.storm_breaker,
              "updated_ts": time.time(),
              "port": self.port}
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            status_path(self.data).write_text(json.dumps(st) + "\n",
                                              encoding="utf-8")
        except OSError:
            pass
        return st

    # -- child lifecycle --------------------------------------------------
    def _start_child(self):
        reap_zombies(self.port, exclude_pids={os.getpid()})
        self.data.mkdir(parents=True, exist_ok=True)
        log_path = self.data / CHILD_LOG_FILE
        old_log = getattr(self, "_log_f", None)
        if old_log is not None:
            try:
                old_log.close()
            except Exception:
                pass
        log_f = open(log_path, "ab", buffering=0)
        self._log_f = log_f
        self._child = subprocess.Popen(
            self.command, cwd=self.cwd,
            stdout=log_f, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)
        self._child_started_ts = time.time()
        self._child_started_mono = time.monotonic()
        self._write_status()
        return self._child

    def _stop_child(self, timeout_s=10.0):
        child = self._child
        if child is None:
            return
        try:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        except Exception:
            pass
        self._child = None

    def _note_crash(self):
        now = time.monotonic()
        self._crash_times.append(now)
        while self._crash_times and \
                now - self._crash_times[0] > self.restart_window_s:
            self._crash_times.popleft()
        return len(self._crash_times) >= self.max_restarts

    # -- main loop --------------------------------------------------------
    def run_forever(self):
        """Supervise until the storm breaker trips. Returns exit reason."""
        try:
            import hot_reload as _hr
            watch_stop = threading.Event()
            watch_thread = threading.Thread(
                target=_hr.watch_loop,
                kwargs={"data": self.data, "stop_event": watch_stop},
                daemon=True)
            watch_thread.start()
        except Exception:
            watch_stop = None
        self._start_child()
        backoff = self.backoff_base_s
        try:
            while True:
                time.sleep(self.poll_s)
                self._write_status()
                # Reload request wins over everything else.
                if reload_requested(self.data, self._child_started_ts):
                    clear_reload_request(self.data)
                    self._stop_child()
                    self.reloads += 1
                    self._start_child()
                    backoff = self.backoff_base_s
                    continue
                poll = self._child.poll() if self._child else None
                if poll is not None:
                    self.last_exit_code = poll
                    self.last_exit_ts = time.time()
                    self.restarts += 1
                    if self._note_crash():
                        self.storm_breaker = True
                        self._write_status()
                        return "storm_breaker"
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.backoff_max_s)
                    self._start_child()
                    continue
                # Hung child? Heartbeat stale long past startup.
                uptime = time.monotonic() - self._child_started_mono
                hb = heartbeat_age_s(self.data)
                if (uptime > self.heartbeat_stale_s + 30
                        and hb is not None and hb > self.heartbeat_stale_s):
                    self._stop_child()
                    self.last_exit_code = "hung"
                    self.last_exit_ts = time.time()
                    self.restarts += 1
                    if self._note_crash():
                        self.storm_breaker = True
                        self._write_status()
                        return "storm_breaker"
                    self._start_child()
        except KeyboardInterrupt:
            self._stop_child()
            self._write_status()
            return "interrupted"
        finally:
            try:
                watch_stop.set()
            except Exception:
                pass


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Supervise pc-mcp-bridge.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data", default=None)
    ap.add_argument("--max-restarts", type=int, default=5)
    args = ap.parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    sup = Supervisor([sys.executable, "pc-agent/server.py"],
                     cwd=repo_root, data=args.data, port=args.port,
                     max_restarts=args.max_restarts)
    reason = sup.run_forever()
    print(f"supervisor stopped: {reason}")
    return 0 if reason == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
