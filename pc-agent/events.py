"""Event-sourced operation log: typed, append-only, replayable.

Phase 1 dual-write companion to audit.py: toolcall.call() appends here
AND to the legacy flat audit.log. The event log is write-only in this
phase (nothing queries it yet).

The write path NEVER raises: append() catches everything, reports to
stderr, and returns None — a broken event pipeline can never break a
tool call. (This is the dual-write guarantee from design §7.)

Layout: %APPDATA%/pc-mcp-bridge/events/YYYY-MM-DD.jsonl (UTC day).
"""
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from auth import app_dir
from event_types import VALID_EVENT_TYPES, validate

EVENT_LOG_VERSION = 1
EVENTS_DIR_NAME = "events"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_event_id(prefix="evt_"):
    """ULID: 48-bit millisecond timestamp + 80-bit randomness, Crockford
    base32. Timestamp-ordered; no coordination needed between writers."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    n = (ts_ms << 80) | secrets.randbits(80)
    chars = [_CROCKFORD[(n >> shift) & 31] for shift in range(125, -1, -5)]
    return prefix + "".join(chars)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def events_dir():
    d = app_dir() / EVENTS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_path(day=None):
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return events_dir() / f"{day}.jsonl"


def build_envelope(type, data, caused_by=None, session_id=None):
    """Build a validated event envelope. Raises ValueError on bad input."""
    errors = validate(type, data)
    if errors:
        raise ValueError(f"invalid {type} event: " + "; ".join(errors))
    return {
        "v": EVENT_LOG_VERSION,
        "event_id": new_event_id(),
        "ts": utc_now_iso(),
        "type": type,
        "caused_by": caused_by,
        "session_id": session_id,
        "data": data,
    }


def append(type, data, caused_by=None, session_id=None, day=None):
    """Append one typed event as a single JSONL line (flush, no fsync).

    Returns the event_id, or None on failure. Never raises: failures go
    to stderr and the caller continues (dual-write guarantee).
    """
    try:
        event = build_envelope(type, data, caused_by, session_id)
        line = json.dumps(event, default=str)
        with _day_path(day).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return event["event_id"]
    except Exception as e:  # noqa: BLE001 - write path must never raise
        print(f"[events] append failed ({type}): {e}", file=sys.stderr)
        return None


def _iter_paths(path):
    p = Path(path) if path else _day_path()
    if p.is_dir():
        yield from sorted(p.glob("*.jsonl"))
    elif p.exists():
        yield p


def _check_envelope(ev, lineno, path):
    if not isinstance(ev, dict):
        return f"{path}:{lineno}: not a JSON object"
    if ev.get("v") != EVENT_LOG_VERSION:
        # Fail loud on unknown schema versions (design §3): a future
        # writer's events must not be silently misread by this reader.
        raise ValueError(
            f"{path}:{lineno}: unsupported event version {ev.get('v')!r}")
    if ev.get("type") not in VALID_EVENT_TYPES:
        return f"{path}:{lineno}: unknown event type {ev.get('type')!r}"
    if not ev.get("event_id"):
        return f"{path}:{lineno}: missing event_id"
    return None


def replay(path=None, from_ts=None, to_ts=None, filter_type=None):
    """Read events back in file order.

    path: a .jsonl file, a directory of them, or None (today's file).
    Returns (events, stats) with stats = {"lines", "parsed", "corrupt"}.
    Corrupt lines are skipped and counted; unknown schema versions raise.
    """
    found, corrupt, lines = [], 0, 0
    for p in _iter_paths(path):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                continue
            bad = _check_envelope(ev, lineno, p)
            if bad:
                print(f"[events] {bad}", file=sys.stderr)
                corrupt += 1
                continue
            ts = ev.get("ts", "")
            if from_ts and ts < from_ts:
                continue
            if to_ts and ts > to_ts:
                continue
            if filter_type and ev.get("type") != filter_type:
                continue
            found.append(ev)
    return found, {"lines": lines, "parsed": len(found), "corrupt": corrupt}
