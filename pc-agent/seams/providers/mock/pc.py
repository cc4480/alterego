"""Mock PC tools: canned observation, journaled notifications, safe power.

power() returns its result dict WITHOUT performing any action — there is
no reboot/shutdown code path in the mock, by construction.
"""
from seams.providers.mock import PROCS, record


def active_window() -> dict:
    return {"hwnd": 12345, "title": "Mock Window", "pid": 1234,
            "process": "mock_process.exe"}


def idle_seconds() -> dict:
    return {"idle_seconds": 42}


def list_processes(offset: int = 0, limit: int = 0) -> dict:
    procs = [dict(p) for p in PROCS]
    total = len(procs)
    offset = max(0, int(offset or 0))
    page = procs[offset:] if not limit else procs[offset:offset + max(0, int(limit))]
    return {"count": len(page), "processes": page,
            "total": total, "offset": offset}


def notify(title: str, message: str, timeout_s: int = 5) -> dict:
    record("notify", {"title": title, "message": message,
                      "timeout_s": timeout_s})
    return {"notified": True, "title": title}


def speak(text: str) -> dict:
    record("speak", {"text": text})
    return {"spoken_chars": len(text)}


def set_volume(level: int) -> dict:
    record("set_volume", {"level": level})
    return {"volume": level}


def power(action: str) -> dict:
    """NEVER reboots/shuts down. The mock power tool is a pure stub: it
    records the requested action and returns, performing nothing.

    Note: returns exactly the ToolDef result_keys (["power"]). The design
    doc's {"ok": True, "note": ...} sketch predates result_keys; the
    contract wins."""
    record("power", {"action": action})
    return {"power": action}
