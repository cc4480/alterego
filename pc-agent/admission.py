"""Admission control + live stats for the tool execution path.

Why this exists (2026-11-14 hang investigation, causes #2 and #4):
sync tool calls run on anyio's shared thread pool with NO queue bound
and NO visibility. Under a burst, calls queued invisibly for minutes
while /health stayed green — the "wedge". This module is the fix:

- a bounded execution gate: at most MAX_INFLIGHT tool bodies run at
  once; extra callers wait at most QUEUE_WAIT_S for a slot, then fail
  FAST with an explicit busy error instead of hanging silently, so the
  queue drains instead of compounding;
- an in-flight registry + counters, exported via stats() so /health
  shows the tool path's real state (in-flight, waiting, oldest age,
  busy rejections) instead of anyone guessing from silence.

Defaults are sized for the owner's bar — 100+ concurrent calls —
and every limit is env-tunable without a code change.
"""
import os
import threading
import time
from contextlib import contextmanager


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


MAX_INFLIGHT = _env_int("PC_BRIDGE_MAX_INFLIGHT", 160)
QUEUE_WAIT_S = _env_int("PC_BRIDGE_QUEUE_WAIT_S", 20)

_gate = threading.Semaphore(MAX_INFLIGHT)
_lock = threading.Lock()
_in_flight = {}          # call_id -> (tool_name, started_monotonic)
_waiting = 0
_next_id = 0
_counts = {"started": 0, "completed": 0, "busy_rejected": 0,
           "failed": 0}


class BusyError(Exception):
    """No execution slot freed within QUEUE_WAIT_S."""


@contextmanager
def slot(tool_name):
    """Hold one execution slot for a tool body. Raises BusyError."""
    global _waiting, _next_id
    with _lock:
        _waiting += 1
    acquired = _gate.acquire(timeout=QUEUE_WAIT_S)
    with _lock:
        _waiting -= 1
        if not acquired:
            _counts["busy_rejected"] += 1
    if not acquired:
        raise BusyError(
            "bridge busy: %d tool calls already running and none "
            "finished within %ds — retry shortly"
            % (MAX_INFLIGHT, QUEUE_WAIT_S))
    with _lock:
        _next_id += 1
        call_id = _next_id
        _in_flight[call_id] = (tool_name, time.monotonic())
        _counts["started"] += 1
    try:
        yield
        with _lock:
            _counts["completed"] += 1
    except BaseException:
        with _lock:
            _counts["failed"] += 1
        raise
    finally:
        with _lock:
            _in_flight.pop(call_id, None)
        _gate.release()


def stats():
    """Snapshot for /health: the tool path's real load state."""
    now = time.monotonic()
    with _lock:
        ages = [now - t for _, t in _in_flight.values()]
        return {
            "max_inflight": MAX_INFLIGHT,
            "queue_wait_s": QUEUE_WAIT_S,
            "in_flight": len(_in_flight),
            "waiting": _waiting,
            "oldest_in_flight_s": round(max(ages), 1) if ages else 0,
            "started": _counts["started"],
            "completed": _counts["completed"],
            "failed": _counts["failed"],
            "busy_rejected": _counts["busy_rejected"],
        }
