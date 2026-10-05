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

ALL_MISC_DEFS = [MEMORY_RECALL, DOCTOR, ARBITRATE]
