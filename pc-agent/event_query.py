"""Query the event-sourced operation log (design §8).

CLI:
    python event_query.py tools --tool delete_file --since 7d
    python event_query.py replay --session sess_abc123
    python event_query.py chain evt_01J9A...
    python event_query.py approvals --since 7d
    python event_query.py verify sess_abc123
    python event_query.py stats --since 30d

Output is JSON by default, --format human for readable transcripts.
--since accepts 5m, 1h, 24h, 7d, 30d, or an ISO-8601 timestamp (UTC).

The query engine functions below are also imported by the MCP tool
providers (seams/providers/*/misc.py) for the query_events and
replay_session tools — the CLI and the tools share one implementation.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import events
import projections

_DEFAULT_LIMIT = 50


def _parse_since(s):
    """'5m'/'1h'/'24h'/'7d'/'30d' or ISO-8601 -> comparable UTC ISO string."""
    m = re.fullmatch(r"(\d+)([mhd])", (s or "").strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
                 "d": timedelta(days=n)}[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    try:
        ts = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        raise ValueError(f"bad --since {s!r}: use 5m, 1h, 24h, 7d, 30d "
                         "or an ISO-8601 timestamp")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _events_dir(override=None):
    if override:
        return override
    env = os.environ.get("PC_BRIDGE_EVENTS_DIR")
    if env:
        return env
    return str(events.events_dir())


def _read_all(since=None, events_dir=None):
    """All events in the log dir at/after since (ISO string or None)."""
    found, _stats = events.replay(_events_dir(events_dir),
                                  from_ts=since)
    return sorted(found, key=lambda e: e.get("ts", ""))


def query_event_log(tool=None, event_type=None, since="7d", session_id=None,
                    limit=_DEFAULT_LIMIT, events_dir=None):
    """Filtered event search. Returns (events, total, truncated)."""
    since_iso = _parse_all_since(since)
    out = []
    for e in _read_all(since_iso, events_dir):
        d = e.get("data", {})
        if tool and d.get("tool") != tool:
            continue
        if event_type and e.get("type") != event_type:
            continue
        if session_id and e.get("session_id") != session_id:
            continue
        out.append(e)
    total = len(out)
    return out[:limit], total, total > limit


def _parse_all_since(since):
    if since is None:
        return None
    return _parse_since(since)


def query_events(tool=None, type=None, since="7d", session_id=None,
                 limit=_DEFAULT_LIMIT):
    """MCP tool implementation: filtered event search (read-only)."""
    evs, total, truncated = query_event_log(
        tool=tool, event_type=type, since=since, session_id=session_id,
        limit=max(1, min(int(limit or _DEFAULT_LIMIT), 500)))
    return {"events": evs, "total": total, "truncated": truncated}


def session_transcript(session_id, format="human", events_dir=None):
    """Ordered causal transcript of one operator session."""
    evs = [e for e in _read_all(None, events_dir)
           if e.get("session_id") == session_id]
    if format == "human":
        return "\n".join(_human_line(e) for e in evs)
    return evs


def replay_session(session_id, format="human"):
    """MCP tool implementation: full causal transcript of a session."""
    t = session_transcript(session_id, format=format)
    return {"session_id": session_id, "transcript": t}


def verify_session(session_id, events_dir=None):
    """Human-verifiable report cross-check: transcript + roll-up counts."""
    evs = [e for e in _read_all(None, events_dir)
           if e.get("session_id") == session_id]
    view = projections.project_session(evs, session_id)
    failures = sum(1 for e in evs if e.get("type") == "ToolFailed")
    return {
        "session_id": session_id,
        "event_count": len(evs),
        "tool_calls": len(view["tool_calls"]),
        "denials": len(view["denials"]),
        "failures": failures,
        "approvals_shown": view["approvals_shown"],
        "files_touched": view["files_touched"],
        "transcript": [_human_line(e) for e in evs],
    }


# ---- human-readable rendering (America/Chicago) ---------------------------

def _chicago():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Chicago")
    except Exception:  # noqa: BLE001 - tzdata missing; fall back to CDT
        return timezone(timedelta(hours=-5), "CDT")


_CHICAGO = _chicago()


def _local(ts):
    try:
        return datetime.fromisoformat(ts).astimezone(_CHICAGO)
    except (ValueError, TypeError):
        return None


def _human_line(e):
    dt = _local(e.get("ts", ""))
    stamp = dt.strftime("%H:%M:%S.%f")[:-3] if dt else "?"
    t, d = e.get("type"), e.get("data", {})
    detail = {
        "ToolCalled": f"tool={d.get('tool')}",
        "HookEvaluated": (f"{d.get('phase')} hook={d.get('hook_name')} "
                          f"-> {d.get('decision')}"),
        "ApprovalRequested": ("dialog "
                              if d.get("dialog_shown") else "skipped "
                              f"({d.get('skipped_reason')}) "
                              f"result={d.get('dialog_result')}"),
        "ToolCompleted": f"{d.get('tool')} ok ({d.get('latency_ms')}ms)",
        "ToolDenied": f"{d.get('tool')} DENIED by {d.get('denied_by')}",
        "ToolFailed": f"{d.get('tool')} FAILED {d.get('error_type')}",
        "BatchStarted": f"{d.get('tool_count')} calls",
        "BatchCompleted": (f"{d.get('completed')} ok / {d.get('denied')} "
                           f"denied / {d.get('failed')} failed"),
        "SessionPaired": f"method={d.get('pairing_method')}",
        "SessionRevoked": f"reason={d.get('reason')}",
        "ServerStarted": f"v={d.get('version')}",
        "ServerStopping": f"reason={d.get('reason')}",
        "DoctorRun": f"checks={len(d.get('checks', []))}",
    }.get(t, "")
    return f"{stamp}  {t:<16} {detail}".rstrip()


# ---- CLI ------------------------------------------------------------------

def _emit(obj, fmt):
    if fmt == "human":
        if isinstance(obj, list):  # raw event list (tools/chain/approvals)
            print("\n".join(_human_line(e) for e in obj) or "(no events)")
        elif isinstance(obj, dict) and isinstance(obj.get("transcript"),
                                                   str):
            print(obj["transcript"] or "(no events)")  # replay
        elif isinstance(obj, dict) and isinstance(obj.get("transcript"),
                                                  list):
            print(f"session {obj['session_id']}: "  # verify
                  f"{obj['tool_calls']} calls, {obj['denials']} denials, "
                  f"{obj['failures']} failures, "
                  f"{obj['approvals_shown']} approvals shown")
            print("\n".join(obj["transcript"]) or "(no events)")
        else:  # stats, or a tools query (has events/total/truncated keys)
            evs = obj.get("events") if isinstance(obj, dict) else None
            if isinstance(evs, list):
                print(f"{obj['total']} events"
                      + (" (truncated)" if obj.get("truncated") else ""))
                print("\n".join(_human_line(e) for e in evs)
                      or "(no events)")
            else:
                print(json.dumps(obj, indent=2, default=str))
    else:
        print(json.dumps(obj, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Query the bridge event log")
    ap.add_argument("--format", choices=("json", "human"), default="json")
    ap.add_argument("--events-dir", default=None,
                    help="event log dir (default: %%APPDATA%%/pc-mcp-bridge/events)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tools", help="filtered event search")
    p.add_argument("--tool", default=None)
    p.add_argument("--type", default=None)
    p.add_argument("--since", default="7d")
    p.add_argument("--session", default=None)
    p.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)

    p = sub.add_parser("replay", help="full causal transcript of a session")
    p.add_argument("--session", required=True)

    p = sub.add_parser("chain", help="causal chain for one event")
    p.add_argument("event_id")

    p = sub.add_parser("approvals", help="approval dialog history")
    p.add_argument("--since", default="7d")

    p = sub.add_parser("verify",
                       help="human-readable transcript to verify a report")
    p.add_argument("session_id")

    p = sub.add_parser("stats", help="per-tool usage stats")
    p.add_argument("--since", default="30d")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "tools":
            evs, total, truncated = query_event_log(
                tool=a.tool, event_type=a.type, since=a.since,
                session_id=a.session, limit=a.limit,
                events_dir=a.events_dir)
            _emit({"events": evs, "total": total, "truncated": truncated},
                  a.format)
        elif a.cmd == "replay":
            t = session_transcript(a.session, format=a.format,
                                   events_dir=a.events_dir)
            _emit({"session_id": a.session, "transcript": t}, a.format)
        elif a.cmd == "chain":
            chain = projections.project_causal_chain(
                _read_all(None, a.events_dir), a.event_id)
            if a.format == "human":
                print("\n".join(_human_line(e) for e in chain)
                      or "(event not found)")
            else:
                _emit(chain, a.format)
        elif a.cmd == "approvals":
            _emit(projections.project_approvals(
                _read_all(_parse_since(a.since), a.events_dir)), a.format)
        elif a.cmd == "verify":
            _emit(verify_session(a.session_id, a.events_dir), a.format)
        elif a.cmd == "stats":
            _emit(projections.project_tool_stats(
                _read_all(_parse_since(a.since), a.events_dir)), a.format)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
