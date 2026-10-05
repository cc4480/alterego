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
