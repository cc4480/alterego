"""Hot reload: watch bridge code for changes, restart via the supervisor.

When hot reload is enabled, a debounced change to a watched ``.py``
file writes a reload request (see supervisor.request_reload). The
supervisor restarts the server process with fresh code; the tunnel
and the paired session token are untouched. When hot reload is
disabled, nothing watches and nothing restarts on its own.

The watcher is deliberately boring: poll + content hash, no new
dependencies, no in-process module reloading. In-process reload of
the MCP tool registry is not reliably supported by the SDK, and a
half-reloaded server is worse than a one-second supervised restart.
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import supervisor as _sup

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "browser-profile",
             ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc"}
MAX_FILE_BYTES = 2 * 1024 * 1024


def repo_root():
    return Path(__file__).resolve().parent.parent


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root):
    """{relative path: sha256} for every watched .py file under root."""
    root = Path(root)
    snap = {}
    watch = root / "pc-agent"
    if not watch.is_dir():
        return snap
    for path in sorted(watch.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            snap[str(rel)] = _file_hash(path)
        except OSError:
            continue
    return snap


def hot_reload_enabled(data=None):
    try:
        raw = _sup.state_path(data).read_text(encoding="utf-8")
        return bool(json.loads(raw).get("enabled", False))
    except Exception:
        return False


def set_hot_reload(data=None, enabled=False):
    p = _sup.state_path(data)
    p.write_text(json.dumps({"enabled": bool(enabled),
                             "updated_ts": time.time(),
                             "last_change": None}) + "\n",
                 encoding="utf-8")
    return {"enabled": bool(enabled)}


def _record_change(data, changed):
    try:
        p = _sup.state_path(data)
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            st = {}
        st["last_change"] = {"files": list(changed),
                             "ts": time.time()}
        p.write_text(json.dumps(st) + "\n", encoding="utf-8")
    except OSError:
        pass


class Watcher:
    """Debounced file watcher. poll() reports a change only once it
    has been stable (same hash) across the debounce window."""

    def __init__(self, root, poll_s=1.0, debounce_s=2.0):
        self.root = Path(root)
        self.poll_s = poll_s
        self.debounce_s = debounce_s
        self._stable = snapshot(self.root)
        self._pending = {}  # relpath -> (hash, first_seen_monotonic)

    def poll(self):
        """Return the list of changed files, or [] when nothing settled."""
        current = snapshot(self.root)
        now = time.monotonic()
        changed = set(self._stable) ^ set(current)
        changed |= {p for p in current
                    if p in self._stable and current[p] != self._stable[p]}
        settled = []
        for path in sorted(changed):
            h = current.get(path)
            first = self._pending.get(path)
            if first is not None and first[0] == h \
                    and now - first[1] >= self.debounce_s:
                settled.append(path)
                self._pending.pop(path, None)
            elif first is None or first[0] != h:
                self._pending[path] = (h, now)
        # Drop pending entries whose file reverted to the stable hash.
        for path in list(self._pending):
            if path not in changed:
                self._pending.pop(path, None)
        for path in settled:
            if path in current:
                self._stable[path] = current[path]
            else:
                self._stable.pop(path, None)
        return settled


def watch_loop(root=None, data=None, stop_event=None, poll_s=1.0,
               debounce_s=2.0):
    """Run until stop_event is set. Called from the supervisor thread."""
    root = Path(root) if root else repo_root()
    watcher = Watcher(root, poll_s=poll_s, debounce_s=debounce_s)
    stop = stop_event or threading.Event()
    while not stop.is_set():
        if hot_reload_enabled(data):
            changed = watcher.poll()
            if changed:
                _record_change(data, changed)
                _sup.request_reload(data, reason="hot_reload")
        else:
            # Stay in sync so enabling later does not replay old edits.
            watcher.poll()
        stop.wait(poll_s)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Hot-reload watcher.")
    ap.add_argument("--root", default=None)
    ap.add_argument("--data", default=None)
    args = ap.parse_args(argv)
    print("hot-reload watcher running (Ctrl-C to stop)")
    try:
        watch_loop(root=args.root, data=args.data)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
