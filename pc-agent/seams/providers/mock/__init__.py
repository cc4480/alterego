"""Shared mock state: virtual FS, process list, UI call journal, task store.

The mock provider keeps everything in-process and in-memory:
- virtual filesystem (dict) jailed under PROFILE_ROOT — never touches disk
- mock process list — kill_process only edits this list, never real PIDs
- CALLS journal — window/mouse/keyboard calls record here for assertions
- TASKS — in-memory task store (no hash chain; the mock is a test double)

reset() restores fixtures; tests call it in an autouse fixture.
"""
import difflib
import os

PROFILE_ROOT = "/mockhome"

# FS: normalized posix path -> {"content": str | None, "is_dir": bool}
# Directories are implicit: any path with children counts as a dir, but
# create_dir also plants explicit dir entries so empty dirs list correctly.
FS = {}

# Mock process table. kill_process removes entries here only.
_PROCS_FIXTURE = [
    {"pid": 4, "name": "System"},
    {"pid": 512, "name": "mock_explorer.exe"},
    {"pid": 1234, "name": "mock_process.exe"},
]
PROCS = [dict(p) for p in _PROCS_FIXTURE]

# Journal of UI/input actions (focus_window, mouse_*, type_text, ...).
CALLS = []

# task_id -> {"task_id", "goal", "plan", "created_ts", "checkpoints"}
TASKS = {}
_TASK_SEQ = [0]

CLIPBOARD = {"text": "mock clipboard contents"}


def mock_mode() -> str:
    """'strict' (default) or 'lenient', from PC_BRIDGE_MOCK_MODE."""
    mode = os.environ.get("PC_BRIDGE_MOCK_MODE", "strict").strip().lower()
    if mode not in ("strict", "lenient"):
        raise RuntimeError(
            f"PC_BRIDGE_MOCK_MODE={mode!r} invalid; expected 'strict' or "
            "'lenient'")
    return mode


def record(tool: str, args: dict) -> None:
    """Journal a UI/input action for test assertions."""
    CALLS.append({"tool": tool, "args": dict(args)})


def reset() -> None:
    """Restore all mock state to fixtures (tests call this per-test)."""
    global PROCS
    FS.clear()
    PROCS = [dict(p) for p in _PROCS_FIXTURE]
    CALLS.clear()
    TASKS.clear()
    _TASK_SEQ[0] = 0
    CLIPBOARD["text"] = "mock clipboard contents"


def norm_path(raw: str) -> str:
    """Jail a path under PROFILE_ROOT. Raises ValueError on escape."""
    if not isinstance(raw, str) or not raw:
        raise ValueError("path must be a non-empty string")
    p = raw.replace("\\", "/")
    if not p.startswith("/"):
        p = PROFILE_ROOT + "/" + p
    # Collapse "." / ".." lexically; reject anything escaping the jail.
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    norm = "/" + "/".join(parts)
    if norm != PROFILE_ROOT and not norm.startswith(PROFILE_ROOT + "/"):
        raise ValueError(f"denied: outside mock profile root ({raw!r})")
    return norm


def parent(path: str) -> str:
    """Parent dir of a normalized path."""
    return "/" + "/".join(path.strip("/").split("/")[:-1]).strip("/")


def ensure_parents(path: str) -> None:
    """Plant implicit parent dirs as explicit dir entries."""
    p = parent(path)
    while p and p != "/" and p.startswith(PROFILE_ROOT):
        FS.setdefault(p, {"content": None, "is_dir": True})
        p = parent(p)


def is_dir(path: str) -> bool:
    if FS.get(path, {}).get("is_dir"):
        return True
    prefix = path.rstrip("/") + "/"
    return any(k.startswith(prefix) for k in FS)


def list_children(path: str) -> list:
    """Immediate children of a dir: [{"name", "is_dir", "size"}]."""
    prefix = path.rstrip("/") + "/"
    seen = {}
    for k in FS:
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        name = rest.split("/", 1)[0]
        if name not in seen:
            child = prefix + name
            child_is_dir = is_dir(child)
            entry = FS.get(child, {})
            seen[name] = {"name": name,
                          "is_dir": child_is_dir,
                          "size": 0 if child_is_dir
                          else len((entry.get("content") or "")
                                   .encode("utf-8"))}
    return sorted(seen.values(), key=lambda e: e["name"])


def diff_text(old: str, new: str, path: str) -> str:
    """Unified diff, capped at 60 lines (mirrors the Windows provider)."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm="")
    return "\n".join(list(lines)[:60])


def next_task_id() -> str:
    _TASK_SEQ[0] += 1
    return f"mock-task-{_TASK_SEQ[0]:04d}"
