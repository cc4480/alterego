"""Named macros: cached combinations of tool calls, replayed as one call.

A macro records the COMBINATION (the plan/sequence) for a frequent task,
not read results: every replay re-runs each step live through the batch
layer, so windows, screenshots, and UI state are always read fresh.
Replayed steps run through the Batch 3 parallel batch, so independent
calls inside a macro still run concurrently.

Persistence: one JSON file in the app dir (``auth.app_dir()``), read
through a module-level cache keyed to the resolved file path so tests
that redirect APPDATA get a fresh store per path. Writes are atomic
(tmp file + os.replace). The store is the single source of truth; both
providers delegate here.

A step may not name ``batch``, a portmanteau tool, or another macro:
nesting would hide approvals inside approvals. Steps are re-validated
on every replay, so a macro recorded against a tool that later changed
fails loud instead of running stale args.
"""

import json
import os
import re
import threading

_NAME_RX = re.compile(r"^[a-z0-9_-]{1,32}$")
_MAX_STEPS = 20

# Portmanteau tool names (Batch 6, parallel slice): a macro step may
# never name one — a portmanteau is itself a multi-tool dispatch.
_PORTMANTEAU_NAMES = frozenset((
    "mouse", "keyboard", "window", "uia", "file",
    "process", "browser", "cdp", "clipboard", "exec",
))

_LOCK = threading.Lock()
_CACHE = {}  # resolved store path (str) -> {name: {"name", "steps"}}


def _store_path():
    import auth
    return (auth.app_dir() / "macros.json").resolve()


def _load():
    """Store contents (cached per resolved path). Never mutates cache."""
    path = _store_path()
    key = str(path)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        data = {}
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"macro store is corrupt ({path}): {e}") from e
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"macro store is corrupt ({path}): top level "
                    "must be an object")
            for name, entry in parsed.items():
                if not isinstance(entry, dict) \
                        or not isinstance(entry.get("steps"), list):
                    raise ValueError(
                        f"macro store is corrupt ({path}): bad entry "
                        f"for {name!r}")
                data[name] = {"name": name,
                              "steps": [dict(s) for s in entry["steps"]]}
        _CACHE[key] = data
        return data


def _save(data):
    """Atomic write + cache update. Caller holds a private copy."""
    path = _store_path()
    key = str(path)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, sort_keys=True, indent=1)
    with _LOCK:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        _CACHE[key] = data


def _copy_steps(steps):
    return [{"tool": s["tool"], "args": dict(s["args"])} for s in steps]


def validate_steps(steps):
    """Validate a step list; returns a normalized deep copy.

    Fails loud on: non-list, empty, over 20 steps, malformed steps,
    nesting (batch / portmanteau / macro_*), unknown tools, and arg
    keys the tool's definition does not declare."""
    if not isinstance(steps, (list, tuple)) \
            or not 1 <= len(steps) <= _MAX_STEPS:
        raise ValueError(
            "steps must be a list of 1-20 {tool, args} items")
    from seams import definitions
    checked = []
    for step in steps:
        if not isinstance(step, dict) \
                or not isinstance(step.get("tool"), str):
            raise ValueError(
                "each step must look like {tool, args}")
        name = step["tool"]
        if name == "batch" or name.startswith("macro_") \
                or name in _PORTMANTEAU_NAMES:
            raise ValueError(
                f"macro steps may not call {name!r} (no nesting)")
        args = step.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        definition = definitions.by_name(name)
        if definition is None:
            raise ValueError(f"unknown tool: {name}")
        extra = sorted(set(args) - set(definition.args))
        if extra:
            raise ValueError(
                f"tool {name} has no argument(s): {', '.join(extra)}")
        checked.append({"tool": name, "args": dict(args)})
    return checked


def record_macro(name, steps):
    """Record (or overwrite) a macro. Returns the public record."""
    if not isinstance(name, str) or not _NAME_RX.match(name):
        raise ValueError("name must match ^[a-z0-9_-]{1,32}$")
    checked = validate_steps(steps)
    try:
        json.dumps(checked)
    except (TypeError, ValueError) as e:
        raise ValueError(f"macro steps must be JSON-serializable: {e}")
    data = dict(_load())
    data[name] = {"name": name, "steps": _copy_steps(checked)}
    _save(data)
    return {"name": name, "steps": _copy_steps(checked),
            "step_count": len(checked)}


def list_macros():
    """All macros, sorted by name."""
    data = _load()
    entries = [{"name": name, "steps": _copy_steps(entry["steps"]),
                "step_count": len(entry["steps"])}
               for name, entry in sorted(data.items())]
    return {"macros": entries, "count": len(entries)}


def delete_macro(name):
    """Delete a macro. Fails loud on unknown names."""
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    data = dict(_load())
    if name not in data:
        raise ValueError(f"unknown macro: {name}")
    del data[name]
    _save(data)
    return {"name": name, "deleted": True}


def get_macro_steps(name):
    """A macro's steps (deep copy). Fails loud on unknown names."""
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    entry = _load().get(name)
    if entry is None:
        raise ValueError(f"unknown macro: {name}")
    return _copy_steps(entry["steps"])


def replay_macro(name, batch_fn):
    """Replay a macro through the batch layer with fresh reads.

    batch_fn is the resolved ``batch`` provider function (the caller
    owns approval: the Windows provider approves once itself and mutes
    the batch layer's own gate; the mock provider passes it straight
    through). Steps are re-validated here, so a macro recorded
    against a changed tool fails loud instead of running stale args.
    """
    steps = validate_steps(get_macro_steps(name))
    calls = [{"tool": s["tool"], "args": s["args"]} for s in steps]
    result = batch_fn(calls)
    if not isinstance(result, dict) or "results" not in result:
        raise ValueError(f"batch returned an unexpected result: {result!r}")
    return {"macro": name, "calls": result.get("calls", len(steps)),
            "results": result["results"]}


def reset():
    """Test-only: drop the in-memory cache (the store file is re-read
    on next access)."""
    with _LOCK:
        _CACHE.clear()
