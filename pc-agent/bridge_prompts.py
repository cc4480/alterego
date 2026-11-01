"""MCP prompts: repeat-job starters as pure text.

A prompt is one user message that tells the client which tools to run
for a recurring job — no tool calls happen here, and nothing is read
from live state at registration time. Registered in server.py via
@mcp.prompt; a str-returning function yields one user PromptMessage.
"""

def pc_health_check() -> str:
    """One-call health summary: doctor, desktop state, supervisor."""
    return (
        "Run the bridge health check and summarize it in a few lines: "
        "call doctor, then desktop_state with a screenshot, then "
        "supervisor_status. Report each doctor check's status, the "
        "focused window's title, and whether the supervisor is running. "
        "If anything is not ok, quote its fix command verbatim."
    )


def find_window(name: str) -> str:
    """Find a window by name, focus it, and report what it is."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    return (
        f"Find the window named {name!r}: call list_windows and pick the "
        "entry whose title matches, or use complete_arg with tool "
        "'uia_find' and argument 'window' to complete the title. Then "
        "call focus_window with its hwnd and report the hwnd, title, "
        "and process. If no window matches, say so and list the "
        "closest titles instead of guessing."
    )


def summarize_file(path: str) -> str:
    """Read a profile file and summarize it."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    return (
        f"Read the file at {path!r} with read_file (use read_file_range "
        "for a long file, a few hundred lines at a time) and summarize "
        "it: what it is, the key facts or decisions in it, and anything "
        "that looks stale or contradictory. Quote exact lines for any "
        "claim that matters."
    )
