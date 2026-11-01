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

ARBITRATE = ToolDef(
    name="arbitrate",
    group="misc",
    doc="Rank candidate tool sequences by risk. No approval.",
    args={"trajectories": {"type": "list", "required": True}},
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
          "limit": {"type": "int", "default": 50, "required": False},
          "offset": {"type": "int", "default": 0, "required": False}},
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

SEARCH_TOOLS = ToolDef(
    name="search_tools",
    group="misc",
    doc="Find tools by keyword over names, docs, and groups — use this "
        "instead of reading all tool schemas. Empty query lists every "
        "tool. Results are scored (name matches first) and paginated.",
    args={"query": {"type": "str", "required": False, "default": ""},
          "group": {"type": "str", "required": False, "default": ""},
          "max_results": {"type": "int", "required": False, "default": 10},
          "offset": {"type": "int", "required": False, "default": 0}},
    result_keys=["query", "count", "total", "tools"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure metadata search over definitions — zero "
                   "platform coupling.",
)

COMPLETE_ARG = ToolDef(
    name="complete_arg",
    group="misc",
    doc="Complete an argument value from live state: window titles for "
        "uia_find/wait_for window, profile-relative paths for the file "
        "tools' path. Unknown tool or unsupported argument is an error; "
        "a supported pair with no matches returns an empty list.",
    args={"tool": {"type": "str", "required": True},
          "argument": {"type": "str", "required": True},
          "prefix": {"type": "str", "required": False, "default": ""},
          "max_results": {"type": "int", "required": False, "default": 10}},
    result_keys=["tool", "argument", "prefix", "values", "total"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Window titles come from list_windows; paths from the "
                   "profile-root listing. Both providers implement it. "
                   "The MCP completion primitive cannot do this — it "
                   "only completes prompt/resource arguments.",
)

ELICIT_INPUT = ToolDef(
    name="elicit_input",
    group="misc",
    doc="Ask a structured question instead of failing or guessing: "
        "records a question with a 2-10 option pick-list and returns it "
        "for the client to answer with answer_elicitation. One round "
        "trip each way. Pending questions are in-memory only.",
    args={"question": {"type": "str", "required": True},
          "options": {"type": "list", "required": True},
          "tool": {"type": "str", "required": False, "default": ""},
          "argument": {"type": "str", "required": False, "default": ""},
          "context": {"type": "str", "required": False, "default": ""}},
    result_keys=["elicitation_id", "question", "options", "status",
                 "tool", "argument"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared elicitations module — both providers delegate "
                   "to it (single source of truth, like event_query).",
)

ANSWER_ELICITATION = ToolDef(
    name="answer_elicitation",
    group="misc",
    doc="Answer a pending elicitation with one of its options. Fails "
        "loud on unknown ids, already-answered questions, and "
        "off-list choices.",
    args={"elicitation_id": {"type": "str", "required": True},
          "choice": {"type": "str", "required": True}},
    result_keys=["elicitation_id", "question", "choice", "status"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared elicitations module — both providers delegate "
                   "to it.",
)

LIST_ELICITATIONS = ToolDef(
    name="list_elicitations",
    group="misc",
    doc="Pending/answered/all elicitations, newest first.",
    args={"status": {"type": "str", "required": False, "default": ""},
          "limit": {"type": "int", "required": False, "default": 20}},
    result_keys=["elicitations", "count", "total"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared elicitations module — both providers delegate "
                   "to it.",
)

WATCH_PATH = ToolDef(
    name="watch_path",
    group="misc",
    doc="Register a profile-jailed file to watch for changes. Fails "
        "loud if the file cannot be read (missing, too large, or "
        "outside the profile). Changes land in the journal read by "
        "poll_changes.",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "watching", "fingerprint"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared changes module — both providers delegate "
                   "to it (single source of truth).",
)

UNWATCH_PATH = ToolDef(
    name="unwatch_path",
    group="misc",
    doc="Drop a watched file. Fails loud if it is not watched.",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "watching"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared changes module — both providers delegate "
                   "to it.",
)

LIST_WATCHES = ToolDef(
    name="list_watches",
    group="misc",
    doc="Registered watches with their last fingerprints.",
    args={},
    result_keys=["watches", "count"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared changes module — both providers delegate "
                   "to it.",
)

POLL_CHANGES = ToolDef(
    name="poll_changes",
    group="misc",
    doc="Change journal entries after a sequence cursor: window-set "
        "and watched-file changes the watcher thread recorded. Pass "
        "next_seq back as since_seq. Cheap: only new entries return.",
    args={"since_seq": {"type": "int", "required": False, "default": 0},
          "limit": {"type": "int", "required": False, "default": 50}},
    result_keys=["changes", "count", "total", "next_seq"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared changes module — both providers delegate "
                   "to it.",
)

DASHBOARD = ToolDef(
    name="dashboard",
    group="misc",
    doc="Bridge status summary for the MCP Apps widget: health, window "
        "count/titles, and the ui:// resource URI. The same data as "
        "text for clients without Apps support.",
    args={},
    result_keys=["title", "summary", "health", "windows_count",
                 "window_titles", "ui_resource_uri"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared bridge_resources module — both providers "
                   "delegate to it.",
)

TOOL_FINDER = ToolDef(
    name="tool_finder",
    group="misc",
    doc="Find tools by keyword over names, docs, and groups — the "
        "plain-name alias for search_tools. Returns matching tool "
        "calls (name, group, doc, tier, args), never file locations. "
        "Empty query lists every tool, paginated.",
    args={"query": {"type": "str", "required": False, "default": ""},
          "group": {"type": "str", "required": False, "default": ""},
          "max_results": {"type": "int", "required": False, "default": 10},
          "offset": {"type": "int", "required": False, "default": 0}},
    result_keys=["query", "count", "total", "tools"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure metadata search over definitions — zero "
                   "platform coupling.",
)

ALL_MISC_DEFS = [MEMORY_RECALL, DOCTOR, ARBITRATE, QUERY_EVENTS,
                 REPLAY_SESSION, SEARCH_TOOLS, COMPLETE_ARG,
                 ELICIT_INPUT, ANSWER_ELICITATION, LIST_ELICITATIONS,
                 WATCH_PATH, UNWATCH_PATH, LIST_WATCHES, POLL_CHANGES,
                 DASHBOARD, TOOL_FINDER]
