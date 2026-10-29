"""Service Definitions for miscellaneous tools (group: misc).

memory_recall: on-PC memory archive search.
doctor: bridge health checks.
arbitrate: risk-shaped arbitration of tool trajectories. (The MCP surface
registers this as `arbitrate_tool`; the pipeline, profiles, and audit log
know it as `arbitrate` — see the registry alias.)
"""
from seams.definitions.base import ToolDef

MEMORY_RECALL = ToolDef(
    name="memory_recall",
    group="misc",
    doc="Search the on-PC memory archive (transcripts + subjects). "
        "No approval.",
    args={"query": {"type": "str", "required": True},
          "limit": {"type": "int", "default": 5, "required": False}},
    result_keys=["query", "results", "files_searched", "files_matched",
                 "note"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure Python keyword search over markdown files. The "
                   "archive root differs per OS (%APPDATA% vs $HOME) via "
                   "the PC_BRIDGE_MEMORY_DIR override. No known gaps.",
)

DOCTOR = ToolDef(
    name="doctor",
    group="misc",
    doc="Bridge health check: Python, port, Defender, Startup, tunnel, "
        "disk. No approval.",
    args={},
    result_keys=["checks", "summary", "counts"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="The check framework is portable; the individual checks "
                   "are Windows-specific (Defender exclusions, Startup "
                   ".bat, cloudflared config). A Linux provider implements "
                   "its own checks (port holder via /proc or ss, systemd "
                   "unit instead of the Startup .bat, disk via "
                   "shutil.disk_usage) — the Defender check is absent, "
                   "not faked. The Windows provider already degrades "
                   "non-Windows checks to warnings.",
)

_TRAJECTORY_STEP_SCHEMA = {
    "type": "object",
    "properties": {"tool": {"type": "string"},
                   "args": {"type": "object"}},
    "required": ["tool"],
}
_TRAJECTORY_ITEM_SCHEMA = {
    "anyOf": [
        {"type": "array",
         "items": {"anyOf": [{"type": "string"},
                             _TRAJECTORY_STEP_SCHEMA]}},
        {"type": "object",
         "properties": {"tools": {
             "type": "array",
             "items": {"anyOf": [{"type": "string"},
                                 _TRAJECTORY_STEP_SCHEMA]}}},
         "required": ["tools"]},
        _TRAJECTORY_STEP_SCHEMA,
        {"type": "string"},
    ],
}

ARBITRATE = ToolDef(
    name="arbitrate",
    group="misc",
    doc="Rank candidate tool sequences by risk. No approval. "
        "trajectories is a non-empty list of trajectories (candidate "
        "tool sequences to compare). Each trajectory may be written "
        "as: [\"tool_name\", ...] (a list of tool names); "
        "[{\"tool\": \"tool_name\", \"args\": {...}}, ...] (a list of "
        "step objects; \"args\" optional, accepted but ignored — only "
        "tool names affect scoring); {\"tools\": [\"tool_name\", ...]} "
        "(a dict naming its tools); {\"tool\": \"tool_name\", "
        "\"args\": {...}} (a single step object); or \"tool_name\" "
        "(a single tool name). All shapes are normalised to tool-name "
        "lists before scoring.",
    args={"trajectories": {
        "type": "list", "required": True,
        "items": _TRAJECTORY_ITEM_SCHEMA,
        "description": "Non-empty list of trajectories; each item "
                       "uses one of the trajectory shapes in the doc."}},
    result_keys=["ranked", "recommended"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure risk-scoring logic over tool_profiles — zero "
                   "platform coupling. Fully portable.",
)

QUERY_EVENTS = ToolDef(
    name="query_events",
    group="misc",
    doc="Search the event-sourced operation log: filter by tool, event "
        "type, session, and time window. Read-only, no approval.",
    args={"tool": {"type": "str", "required": False},
          "type": {"type": "str", "required": False},
          "since": {"type": "str", "default": "7d", "required": False},
          "session_id": {"type": "str", "required": False},
          "limit": {"type": "int", "default": 50, "required": False}},
    result_keys=["events", "total", "truncated"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Platform-independent query over the JSONL event log "
                   "(%APPDATA%/pc-mcp-bridge/events). Both providers "
                   "re-export the shared event_query.py implementation.",
)

REPLAY_SESSION = ToolDef(
    name="replay_session",
    group="misc",
    doc="Full causal transcript of one operator session, for verifying "
        "reported operations against the log. Read-only, no approval.",
    args={"session_id": {"type": "str", "required": True},
          "format": {"type": "str", "default": "human", "required": False}},
    result_keys=["session_id", "transcript"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Platform-independent; shares event_query.py with the "
                   "query_events tool and the event_query.py CLI.",
)

ALL_MISC_DEFS = [MEMORY_RECALL, DOCTOR, ARBITRATE, QUERY_EVENTS,
                 REPLAY_SESSION]
