"""Projections: pure functions folding event envelopes into queryable views.

events -> state. Never persisted as source of truth; rebuilt by replaying
the log. Every function takes a list of event envelopes (as returned by
events.replay) and returns plain data.
"""
from collections import defaultdict

# ToolCalled args that name a filesystem path (for files_touched).
_PATH_TOOLS = {"write_file": ("path",), "edit_file": ("path",),
               "delete_file": ("path",), "create_dir": ("path",),
               "copy_file": ("src", "dst"), "move_file": ("src", "dst"),
               "read_file": ("path",), "list_dir": ("path",),
               "file_info": ("path",)}


def _by_id(events):
    return {e["event_id"]: e for e in events if e.get("event_id")}


def _called_for(events):
    """Map caused_by event_id -> the ToolCalled envelope it points to."""
    called = {e["event_id"]: e for e in events
              if e.get("type") == "ToolCalled"}
    return called


def project_session(events, session_id):
    """SessionView: what did this session do?

    Returns {"session_id", "tool_calls", "denials", "approvals_shown",
    "files_touched"} where tool_calls is the ordered list of ToolCalled
    event_ids and denials carry their reasons.
    """
    view = {"session_id": session_id, "tool_calls": [], "denials": [],
            "approvals_shown": 0, "files_touched": []}
    seen_files = set()
    for e in sorted(events, key=lambda x: x.get("ts", "")):
        if e.get("session_id") != session_id:
            continue
        t, d = e.get("type"), e.get("data", {})
        if t == "ToolCalled":
            view["tool_calls"].append(e["event_id"])
            tool = d.get("tool")
            for key in _PATH_TOOLS.get(tool, ()):
                p = (d.get("args") or {}).get(key)
                if isinstance(p, str) and p and p not in seen_files:
                    seen_files.add(p)
                    view["files_touched"].append(p)
        elif t == "ToolDenied":
            view["denials"].append({"event_id": e["event_id"],
                                    "ts": e.get("ts"),
                                    "tool": d.get("tool"),
                                    "denied_by": d.get("denied_by"),
                                    "reason": d.get("reason")})
        elif t == "ApprovalRequested" and d.get("dialog_shown"):
            view["approvals_shown"] += 1
    return view


def project_tool_stats(events, since_ts=None):
    """ToolStats: per-tool usage — calls, denials, failures, avg latency.

    Latency comes from ToolCompleted/ToolFailed latency_ms; a tool with
    no completed/failed calls reports avg_latency_ms=None.
    """
    stats = defaultdict(lambda: {"calls": 0, "denied": 0, "failed": 0,
                                 "_lat": []})
    for e in events:
        ts = e.get("ts", "")
        if since_ts and ts < since_ts:
            continue
        t, d = e.get("type"), e.get("data", {})
        tool = d.get("tool")
        if not tool:
            continue
        if t == "ToolCalled":
            stats[tool]["calls"] += 1
        elif t == "ToolDenied":
            stats[tool]["denied"] += 1
        elif t == "ToolFailed":
            stats[tool]["failed"] += 1
            if isinstance(d.get("latency_ms"), (int, float)):
                stats[tool]["_lat"].append(d["latency_ms"])
        elif t == "ToolCompleted":
            if isinstance(d.get("latency_ms"), (int, float)):
                stats[tool]["_lat"].append(d["latency_ms"])
    out = {}
    for tool in sorted(stats):
        s = stats[tool]
        lat = s.pop("_lat")
        s["avg_latency_ms"] = round(sum(lat) / len(lat), 1) if lat else None
        out[tool] = s
    return out


def project_approvals(events, since_ts=None):
    """ApprovalHistory: every approval dialog shown, newest last.

    Each entry: {ts, tool, tier, result, summary}. The tool name comes
    from the ToolCalled event the ApprovalRequested points at.
    """
    called = _called_for(events)
    out = []
    for e in sorted(events, key=lambda x: x.get("ts", "")):
        if e.get("type") != "ApprovalRequested":
            continue
        ts = e.get("ts", "")
        if since_ts and ts < since_ts:
            continue
        d = e.get("data", {})
        parent = called.get(e.get("caused_by") or "")
        out.append({"ts": ts,
                    "tool": (parent or {}).get("data", {}).get("tool"),
                    "tier": d.get("tier"),
                    "dialog_shown": d.get("dialog_shown"),
                    "result": d.get("dialog_result"),
                    "skipped_reason": d.get("skipped_reason"),
                    "summary": d.get("summary_shown")})
    return out


def project_causal_chain(events, event_id):
    """CausalChain: the full ToolCalled subtree an event belongs to.

    Walks caused_by links up to the root, then returns the root plus
    every event caused by it, ordered by ts. This answers "why was this
    denied?" with the complete chain (ToolCalled, HookEvaluated,
    ApprovalRequested, ToolDenied) — no free-text parsing.
    """
    by_id = _by_id(events)
    target = by_id.get(event_id)
    if target is None:
        return []
    root = target
    seen = {event_id}
    while root.get("caused_by"):
        parent = by_id.get(root["caused_by"])
        if parent is None or parent["event_id"] in seen:
            break
        seen.add(parent["event_id"])
        root = parent
    root_id = root["event_id"]
    chain = [e for e in events
             if e.get("event_id") == root_id
             or e.get("caused_by") == root_id]
    return sorted(chain, key=lambda x: x.get("ts", ""))
