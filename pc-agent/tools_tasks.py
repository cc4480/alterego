"""Signed task records with checkpoints.

An agent's context window is ephemeral; task state lives here instead.
Each task is an append-only JSONL file; every checkpoint is HMAC-signed
and hash-chained to the previous one, so tampering with any checkpoint
invalidates the chain. Key is machine-local (never leaves the PC).
"""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from pathlib import Path

import toolcall
from auth import app_dir

TASK_DIR = "tasks"
_KEY_FILE = "task_key"


def _key() -> bytes:
    p = app_dir() / _KEY_FILE
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
        try:
            import os
            os.chmod(p, 0o600)
        except Exception:
            pass
    return p.read_bytes()


def _task_file(task_id: str) -> Path:
    d = app_dir() / TASK_DIR
    d.mkdir(parents=True, exist_ok=True)
    if not task_id.replace("-", "").isalnum():
        raise ValueError("bad task_id")
    return d / f"{task_id}.jsonl"


def _sign(key: bytes, prev: str, payload: str) -> str:
    return hmac.new(key, (prev + payload).encode(), hashlib.sha256).hexdigest()


def _read(task_id: str):
    p = _task_file(task_id)
    if not p.exists():
        raise ValueError(f"unknown task: {task_id}")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _verify(entries) -> dict:
    """Verify the hash chain; returns the task header + checkpoint list."""
    key = _key()
    prev = "GENESIS"
    header, checkpoints = None, []
    for e in entries:
        payload = json.dumps(e["data"], sort_keys=True)
        if not hmac.compare_digest(e["sig"], _sign(key, prev, payload)):
            raise ValueError("task record tampered: signature mismatch")
        prev = e["sig"]
        if e["kind"] == "header":
            header = e["data"]
        else:
            checkpoints.append(e["data"])
    if header is None:
        raise ValueError("task record corrupt: no header")
    return {"task_id": header["task_id"], "goal": header["goal"],
            "plan": header["plan"], "created_ts": header["created_ts"],
            "checkpoints": checkpoints,
            "done_steps": [c["step"] for c in checkpoints if c.get("done")]}


def _append(task_id: str, kind: str, data: dict):
    entries = _read(task_id) if kind != "header" else []
    prev = entries[-1]["sig"] if entries else "GENESIS"
    payload = json.dumps(data, sort_keys=True)
    rec = {"kind": kind, "data": data, "sig": _sign(_key(), prev, payload)}
    p = _task_file(task_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def create(goal: str, plan: list) -> dict:
    """Create a task record. No approval (local bookkeeping only)."""
    if not goal or not isinstance(plan, list) or not plan:
        raise ValueError("goal and a non-empty plan list are required")
    task_id = uuid.uuid4().hex[:12]
    _task_file(task_id).write_text("", encoding="utf-8")
    _append(task_id, "header", {"task_id": task_id, "goal": goal,
                                "plan": [str(s) for s in plan],
                                "created_ts": int(time.time())})
    return {"task_id": task_id, "goal": goal, "steps": len(plan)}


def checkpoint(task_id: str, step: int, result: str, done: bool = True) -> dict:
    """Append a signed checkpoint. No approval."""
    t = _verify(_read(task_id))
    if not isinstance(step, int) or not 0 <= step < len(t["plan"]):
        raise ValueError(f"step must be 0..{len(t['plan']) - 1}")
    _append(task_id, "checkpoint",
            {"step": step, "plan_step": t["plan"][step],
             "result": str(result)[:2000], "done": bool(done),
             "ts": int(time.time())})
    return {"task_id": task_id, "step": step, "done": bool(done)}


def status(task_id: str = "") -> dict:
    """Task status (verified chain), or list all tasks if no id. No approval."""
    if not task_id:
        d = app_dir() / TASK_DIR
        out = []
        if d.exists():
            for p in sorted(d.glob("*.jsonl")):
                try:
                    t = _verify(_read(p.stem))
                    out.append({"task_id": t["task_id"], "goal": t["goal"],
                                "done": len(t["done_steps"]),
                                "total": len(t["plan"])})
                except ValueError:
                    out.append({"task_id": p.stem, "error": "tampered"})
        return {"tasks": out}
    return _verify(_read(task_id))


# ---- MCP wrappers (registered via tool_wrappers.ALL_TOOLS) ----
def task_create(goal: str, plan: list) -> dict:
    """Create a signed task record with a step plan. No approval."""
    return toolcall.call("task_create", create, {"goal": goal, "plan": plan})


def task_checkpoint(task_id: str, step: int, result: str,
                    done: bool = True) -> dict:
    """Append a signed checkpoint to a task. No approval."""
    return toolcall.call("task_checkpoint", checkpoint,
                         {"task_id": task_id, "step": step,
                          "result": result, "done": done})


def task_status(task_id: str = "") -> dict:
    """Get a task's verified status, or list all tasks. No approval."""
    return toolcall.call("task_status", status, {"task_id": task_id})
