"""Service Definitions for signed task records (group: tasks)."""
from seams.definitions.base import ToolDef

_PORTABLE = ("Pure Python (JSONL + HMAC, stdlib) — fully portable. The "
             "app-data dir differs per OS (%APPDATA% vs ~/.config); a "
             "provider-level detail, not a gap.")

TASK_CREATE = ToolDef(
    name="task_create",
    group="tasks",
    doc="Create a signed task record with a step plan. No approval. "
        "Plan steps are numbered from 1: the first plan step is "
        "step 1 for task_checkpoint and task_status.",
    args={"goal": {"type": "str", "required": True},
          "plan": {"type": "list", "required": True,
                   "items": {"type": "string"},
                   "description": "Ordered plan step texts; steps "
                                  "are numbered from 1 in order."}},
    result_keys=["task_id", "goal", "steps"],
    approval_tier="silent",
    side_effects="local-fs",
    platform_notes=_PORTABLE,
)

TASK_CHECKPOINT = ToolDef(
    name="task_checkpoint",
    group="tasks",
    doc="Append a signed checkpoint to a task. No approval. "
        "step is 1-based: step=1 is the first plan step, and step "
        "must be between 1 and the number of plan steps inclusive — "
        "0 and larger values error.",
    args={"task_id": {"type": "str", "required": True},
          "step": {"type": "int", "required": True, "minimum": 1,
                   "description": "1-based plan step number: 1 is "
                                  "the first plan step."},
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
    doc="Get a task's verified status, or list all tasks. No approval. "
        "Each checkpoint's step and done_steps are 1-based plan step "
        "numbers (step 1 = the first plan step), and each checkpoint "
        "also carries plan_step, the text of the step it belongs to.",
    args={"task_id": {"type": "str", "default": "", "required": False}},
    result_keys=["task_id", "goal", "plan", "created_ts", "checkpoints",
                 "done_steps", "tasks"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_PORTABLE,
)

ALL_TASKS_DEFS = [TASK_CREATE, TASK_CHECKPOINT, TASK_STATUS]
