"""Change journal + watchers: push-style awareness without polling
whole lists.

Native MCP push (``subscriptions/listen`` + ``ResourceUpdated``)
needs a publisher on the server's event loop; the watcher here runs
in a daemon thread with no loop access, so it records to this journal
instead. Clients poll ``poll_changes`` with a sequence cursor (cheap:
only entries after the cursor come back), or read the ``pc://``
resources fresh — resource reads are never cached.

The journal is in-memory: a restart clears it and fingerprints are
recomputed on the next check. Fingerprints come from the provider
registry, so the same code runs on Windows and mock: windows from
``list_windows``, files from ``read_file``'s SHA-256 (1 MB cap, same
as the read itself). The journal write can never break a call, and a
failing check never kills the watcher thread.
"""

import hashlib
import threading
import time
from datetime import datetime, timezone

_LOCK = threading.Lock()
_JOURNAL = []
_SEQ = 0
_WATCHES = {}
_WINDOW_FP = None
_THREAD = None
_THREAD_LOCK = threading.Lock()

WATCH_INTERVAL_S = 5.0
_WINDOWS_URI = "pc://desktop/windows"


def _next_seq():
    global _SEQ
    _SEQ += 1
    return _SEQ


def _ts():
    return datetime.now(timezone.utc).isoformat()


def record_change(kind, uri, detail):
    """Append a journal entry. Returns the entry."""
    with _LOCK:
        entry = {"seq": _next_seq(), "ts": _ts(), "kind": kind,
                 "uri": uri, "detail": detail}
        _JOURNAL.append(entry)
        if len(_JOURNAL) > 500:
            del _JOURNAL[:-500]
        return dict(entry)


def poll_changes(since_seq=0, limit=50):
    """Journal entries with seq greater than since_seq, in order."""
    since_seq = max(0, int(since_seq or 0))
    limit = max(1, min(int(limit or 50), 200))
    with _LOCK:
        total = len(_JOURNAL)
        page = [dict(e) for e in _JOURNAL if e["seq"] > since_seq]
        next_seq = _SEQ
    page = page[:limit]
    return {"changes": page, "count": len(page), "total": total,
            "next_seq": next_seq}


def _resolve(name):
    from seams.registry import resolve
    return resolve(name)


def _file_fingerprint(path):
    """SHA-256 of a profile-jailed file via the read provider."""
    result = _resolve("read_file")(path)
    return result["sha256"]


def watch_path(path):
    """Register a file to watch. Fails loud if it cannot be read."""
    if not (path or "").strip():
        raise ValueError("path must be a non-empty string")
    try:
        fingerprint = _file_fingerprint(path)
    except Exception as exc:
        raise ValueError(f"cannot watch {path!r}: {exc}")
    result = _resolve("read_file")(path)
    canonical = result["path"]
    with _LOCK:
        _WATCHES[canonical] = fingerprint
    return {"path": canonical, "watching": True,
            "fingerprint": fingerprint}


def unwatch_path(path):
    """Drop a watched file. Accepts the path watch_path returned."""
    if not (path or "").strip():
        raise ValueError("path must be a non-empty string")
    with _LOCK:
        if path in _WATCHES:
            del _WATCHES[path]
            return {"path": path, "watching": False}
    try:
        canonical = _resolve("read_file")(path)["path"]
    except Exception:
        canonical = None
    with _LOCK:
        if canonical and canonical in _WATCHES:
            del _WATCHES[canonical]
            return {"path": canonical, "watching": False}
    raise ValueError(f"not watched: {path!r}")


def list_watches():
    """Registered watches with their last fingerprints."""
    with _LOCK:
        watches = [{"path": p, "fingerprint": fp}
                   for p, fp in sorted(_WATCHES.items())]
    return {"watches": watches, "count": len(watches)}


def _window_fingerprint():
    """Hash of the visible window set (hwnd, pid, title)."""
    result = _resolve("list_windows")()
    wins = sorted((w.get("hwnd"), w.get("pid"), w.get("title", ""))
                  for w in result.get("windows", []))
    blob = repr(wins).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def check_once():
    """One fingerprint pass. Returns the new journal entries."""
    global _WINDOW_FP
    entries = []
    try:
        fp = _window_fingerprint()
    except Exception:
        fp = None
    if fp is not None:
        with _LOCK:
            changed = _WINDOW_FP is not None and fp != _WINDOW_FP
            _WINDOW_FP = fp
        if changed:
            try:
                count = len(_resolve("list_windows")().get("windows", []))
            except Exception:
                count = -1
            entries.append(record_change(
                "windows", _WINDOWS_URI,
                {"count": count, "fingerprint": fp[:12]}))
    with _LOCK:
        watches = dict(_WATCHES)
    for path, old_fp in watches.items():
        try:
            fp = _file_fingerprint(path)
        except Exception:
            continue
        if fp != old_fp:
            with _LOCK:
                _WATCHES[path] = fp
            entries.append(record_change(
                "file", f"pc://files/{path}",
                {"fingerprint": fp[:12]}))
    return entries


def watch_loop(interval_s=WATCH_INTERVAL_S, stop_event=None):
    """Daemon loop: fingerprint, record, sleep. Never raises."""
    while stop_event is None or not stop_event.is_set():
        try:
            check_once()
        except Exception:
            pass
        if stop_event is not None:
            if stop_event.wait(interval_s):
                break
        else:
            time.sleep(interval_s)


def start_watcher(interval_s=WATCH_INTERVAL_S):
    """Start the watcher thread once. Returns True if it started."""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(target=watch_loop,
                                   args=(interval_s,),
                                   daemon=True)
        _THREAD.start()
        return True


def reset():
    """Test-only: clear journal, watches, and window fingerprint."""
    global _SEQ, _WINDOW_FP
    with _LOCK:
        _JOURNAL.clear()
        _WATCHES.clear()
        _SEQ = 0
        _WINDOW_FP = None
