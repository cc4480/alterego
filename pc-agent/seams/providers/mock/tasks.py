"""Mock task tools: in-memory task store (no hash chain, no disk).

The Windows provider keeps a signed, hash-chained task file per task; the
mock keeps plain dicts in TASKS. Same tool names, same result_keys — the
chain is a Windows-provider concern, not a pipeline one.
"""
import time

from seams.providers.mock import TASKS, next_task_id


def task_create(goal: str, plan: list) -> dict:
    if not goal or not isinstance(plan, list) or not plan:
        raise ValueError("goal and a non-empty plan list are required")
    task_id = next_task_id()
    TASKS[task_id] = {"task_id": task_id, "goal": goal,
                      "plan": [str(s) for s in plan],
                      "created_ts": int(time.time()),
                      "checkpoints": []}
    return {"task_id": task_id, "goal": goal, "steps": len(plan)}


def task_checkpoint(task_id: str, step: int, result: str,
                    done: bool = True) -> dict:
    t = TASKS.get(task_id)
    if t is None:
        raise ValueError(f"unknown task_id: {task_id}")
    if not isinstance(step, int) or not 0 <= step < len(t["plan"]):
        raise ValueError(f"step must be 0..{len(t['plan']) - 1}")
    t["checkpoints"].append({"step": step,
                             "plan_step": t["plan"][step],
                             "result": str(result)[:2000],
                             "done": bool(done),
                             "ts": int(time.time())})
    return {"task_id": task_id, "step": step, "done": bool(done)}


def task_status(task_id: str = "") -> dict:
    if task_id:
        t = TASKS.get(task_id)
        if t is None:
            raise ValueError(f"unknown task_id: {task_id}")
        done_steps = sum(1 for c in t["checkpoints"] if c["done"])
        return {"task_id": t["task_id"], "goal": t["goal"],
                "plan": t["plan"], "created_ts": t["created_ts"],
                "checkpoints": t["checkpoints"], "done_steps": done_steps,
                "tasks": None}
    return {"task_id": "", "goal": "", "plan": [], "created_ts": 0,
            "checkpoints": [], "done_steps": 0,
            "tasks": [{"task_id": t["task_id"], "goal": t["goal"],
                       "steps": len(t["plan"])}
                      for t in TASKS.values()]}
