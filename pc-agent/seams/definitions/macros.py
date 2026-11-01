"""Service Definitions for macros (group: macros).

A macro records the combination of tool calls for a frequent task and
replays it as one call. The plan is cached, never read results: every
replay re-runs each step live through the batch layer.
"""
from seams.definitions.base import ToolDef

MACRO_RECORD = ToolDef(
    name="macro_record",
    group="macros",
    doc="Record a named macro: a cached combination of tool calls (the "
        "plan/sequence) for a frequent task, replayed as one call with "
        "macro_replay. Steps run live on every replay — nothing is "
        "memoized. 1-20 steps; a step may not name batch, a portmanteau "
        "tool, or another macro.",
    args={"name": {"type": "str", "required": True},
          "steps": {"type": "list", "required": True}},
    result_keys=["name", "steps", "step_count"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Shared macros module (JSON store in the app dir) — "
                   "both providers delegate to it (single source of "
                   "truth).",
)

MACRO_LIST = ToolDef(
    name="macro_list",
    group="macros",
    doc="Recorded macros, sorted by name, with their steps.",
    args={},
    result_keys=["macros", "count"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Shared macros module — both providers delegate "
                   "to it.",
)

MACRO_REPLAY = ToolDef(
    name="macro_replay",
    group="macros",
    doc="Replay a recorded macro as one call: every step re-runs live "
        "through the batch layer (independent calls run concurrently, "
        "desktop/browser calls serialize). One approval covers all "
        "writes in the macro.",
    args={"name": {"type": "str", "required": True}},
    result_keys=["macro", "calls", "results"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="Pure orchestration over the provider registry: fully "
                   "portable. The Windows provider approves once itself "
                   "and mutes the batch layer's own gate so exactly one "
                   "dialog fires.",
)

MACRO_DELETE = ToolDef(
    name="macro_delete",
    group="macros",
    doc="Delete a recorded macro. Fails loud on unknown names.",
    args={"name": {"type": "str", "required": True}},
    result_keys=["name", "deleted"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Shared macros module — both providers delegate "
                   "to it.",
)

ALL_MACRO_DEFS = [MACRO_RECORD, MACRO_LIST, MACRO_REPLAY, MACRO_DELETE]
