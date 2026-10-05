"""Service Definitions for signed task records (group: tasks)."""
from seams.definitions.base import ToolDef

_PORTABLE = ("Pure Python (JSONL + HMAC, stdlib) — fully portable. The "
             "app-data dir differs per OS (%APPDATA% vs ~/.config); a "
             "provider-level detail, not a gap.")

TASK_CREATE = ToolDef(
    name="task_create",
    group="tasks",
    doc="Create a signed task record with a step plan. No approval.",
    args={"goal": {"type": "str", "required": True},
          "plan": {"type": "list", "required": True}},
    result_keys=["task_id", "goal", "steps"],
    approval_tier="silent",
    side_effects="local-fs",
    platform_notes=_PORTABLE,
)

TASK_CHECKPOINT = ToolDef(
    name="task_checkpoint",
    group="tasks",
    doc="Append a signed checkpoint to a task. No approval.",
    args={"task_id": {"type": "str", "required": True},
          "step": {"type": "int", "required": True},
          "result": {"type": "str", "required": True},
          "done": {"type": "bool", "default": True, "required": False}},
    result_keys=["task_id", "step", "done"],
    approval_tier="silent",
    side_effects="local-fs",
    platform_notes=_PORTABLE,
)

TASK_STATUS = ToolDef(
    name="task_status",
    group="tasks",
    doc="Get a task's verified status, or list all tasks. No approval.",
    args={"task_id": {"type": "str", "default": "", "required": False}},
    result_keys=["task_id", "goal", "plan", "created_ts", "checkpoints",
                 "done_steps", "tasks"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_PORTABLE,
)

ALL_TASKS_DEFS = [TASK_CREATE, TASK_CHECKPOINT, TASK_STATUS]
