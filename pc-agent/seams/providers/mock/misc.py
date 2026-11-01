"""Mock misc tools: memory_recall (canned), doctor (all-ok + 1 warning),
arbitrate (delegates to the REAL pure-logic implementation).

arbitrate is pure Python ranking logic with no platform dependencies, so
the mock reuses it verbatim (imported lazily to avoid import cycles:
arbitrate.py imports toolcall, which imports the registry).
"""
from seams.providers.mock import record


def memory_recall(query: str, limit: int = 5) -> dict:
    return {"query": query, "results": [], "files_searched": 0,
            "files_matched": 0,
            "note": "mock: no memory files on this machine"}


def _check(name: str, status: str, message: str, fix=None) -> dict:
    return {"check": name, "status": status, "message": message,
            "fix": fix}


def doctor() -> dict:
    """All checks ok except defender_exclusions, which is a warning —
    the mock has no Windows Defender to inspect."""
    checks = [
        _check("check_python_version", "ok", "mock: python available"),
        _check("check_port_listener", "ok", "mock: port 8765 free"),
        _check("check_defender_exclusions", "warning",
               "mock provider: check skipped",
               fix="run doctor on the Windows provider for a real check"),
        _check("check_startup_entry", "ok", "mock: startup entry present"),
        _check("check_tunnel_config", "ok", "mock: tunnel configured"),
        _check("check_disk_space", "ok", "mock: disk space sufficient"),
        _check("check_supervisor", "ok", "mock: supervisor running"),
    ]
    counts = {"ok": 0, "warning": 0, "fail": 0}
    for c in checks:
        counts[c["status"]] += 1
    summary = ("all checks passed" if counts["fail"] == 0
               and counts["warning"] == 0 else
               f"{counts['fail']} failed, {counts['warning']} warnings")
    return {"checks": checks, "summary": summary, "counts": counts}


def arbitrate(trajectories: list) -> dict:
    from arbitrate import arbitrate as real_arbitrate  # lazy: cycle guard
    record("arbitrate", {"trajectories": trajectories})
    return real_arbitrate(trajectories)


# Event-log query tools (design §8; read-only, silent tier). The query
# engine is platform-independent — re-export the shared implementation
# so the mock behaves exactly like the Windows provider here.
from event_query import query_events, replay_session  # noqa: E402


# ---- tool discovery + argument completion (Batch 4) -----------------------
def search_tools(query="", group="", max_results=10, offset=0):
    """Keyword search over the tool catalog (metadata only)."""
    from seams import definitions
    return definitions.search_definitions(query, group, max_results, offset)


_WINDOW_ARGS = {"uia_find", "wait_for"}
_PATH_ARGS = {"read_file", "list_dir", "read_file_range", "search_files",
              "search_filenames"}


def _match_candidates(candidates, prefix, max_results):
    pre = (prefix or "").lower()
    matched = ([c for c in candidates if c.lower().startswith(pre)]
               if pre else list(candidates))
    total = len(matched)
    return matched[:max(1, min(int(max_results or 10), 50))], total


def _mock_path_candidates(prefix):
    from seams.providers.mock import (PROFILE_ROOT, is_dir,
                                      list_children, norm_path)
    pre = prefix or ""
    if "/" in pre:
        dir_part, _name = pre.rsplit("/", 1)
        base = norm_path(dir_part) if dir_part else PROFILE_ROOT
    else:
        dir_part = ""
        base = PROFILE_ROOT
    if not is_dir(base):
        return []
    out = []
    for child in list_children(base):
        rel = f"{dir_part}/{child['name']}" if dir_part else child["name"]
        if child["is_dir"]:
            rel += "/"
        out.append(rel)
    return out


def complete_arg(tool, argument, prefix="", max_results=10):
    """Complete an argument value from live state (fail loud)."""
    from seams import definitions
    if definitions.by_name(tool) is None:
        raise ValueError(f"unknown tool: {tool}")
    if tool in _WINDOW_ARGS and argument == "window":
        from seams.providers.mock.read import list_windows
        candidates = [w.get("title", "") for w in
                      list_windows().get("windows", []) if w.get("title")]
    elif tool in _PATH_ARGS and argument == "path":
        candidates = _mock_path_candidates(prefix)
    else:
        raise ValueError(f"no completions for {tool}.{argument}")
    values, total = _match_candidates(candidates, prefix, max_results)
    return {"tool": tool, "argument": argument, "prefix": prefix or "",
            "values": values, "total": total}
