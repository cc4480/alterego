"""Read-only MCP resources: live PC state as addressable text.

Pure functions over the provider registry (lazy resolve, like the
tool pipeline). Registered as ``@mcp.resource`` handlers in
``server.py``; tested here under the mock provider without importing
``mcp``. Resource reads are never cached: every read re-runs the
provider, so a client that re-reads after a change always sees fresh
state. File reads go through the same jailed, 1 MB-capped provider as
``read_file`` — a path outside the profile fails loud here too.
"""

import json

from dashboard_ui import DASHBOARD_URI


def _resolve(name):
    from seams.registry import resolve
    return resolve(name)


def _dumps(result):
    return json.dumps(result, sort_keys=True)


def health_text():
    """Doctor report as JSON text."""
    return _dumps(_resolve("doctor")())


def windows_text():
    """Visible window list as JSON text."""
    return _dumps(_resolve("list_windows")())


def state_text():
    """Desktop state (windows + focused window + UI elements) as JSON
    text. No screenshot: a resource carrying base64 PNGs would dwarf
    the state it describes; screenshots stay on the screenshot tool."""
    return _dumps(_resolve("desktop_state")(include_screenshot=False))


def file_text(path):
    """One profile-jailed file's UTF-8 text. Fails loud outside the
    profile, on missing files, and past the 1 MB cap."""
    return _resolve("read_file")(path)["content"]


def dashboard_state():
    """Dashboard payload: health summary, window counts/titles, and
    the Apps UI resource URI. Shared by the dashboard tool and the
    Apps widget so both always agree."""
    doctor = _resolve("doctor")()
    windows = _resolve("list_windows")()
    titles = [w.get("title", "") for w in windows.get("windows", [])]
    summary = doctor.get("summary", "")
    counts = doctor.get("counts", {})
    return {"title": "AlterEgo bridge",
            "summary": summary,
            "health": counts,
            "windows_count": windows.get("total", len(titles)),
            "window_titles": titles[:5],
            "ui_resource_uri": DASHBOARD_URI}


# ---- tool catalog + macro list (Batch 6) ---------------------------------
def tools_text():
    """Full tool catalog as JSON text, paged through search_definitions
    (50/page cap) so the catalog is complete at any tool count. Never
    cached: every read re-runs the search over the live definitions."""
    from seams import definitions
    tools = []
    offset = 0
    while True:
        page = definitions.search_definitions(
            "", max_results=50, offset=offset)
        tools.extend(page["tools"])
        offset += page["count"]
        if not page["count"] or offset >= page["total"]:
            break
    return _dumps({"query": "", "count": len(tools),
                   "total": len(tools), "tools": tools})


def macros_text():
    """Recorded macros as JSON text, read live from the macro store."""
    import macros
    return _dumps(macros.list_macros())
