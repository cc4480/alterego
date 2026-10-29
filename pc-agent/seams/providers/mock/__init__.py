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

# Mock UIA / desktop state for the input-reliability ops. Tests steer it
# directly: set UIA["focus_fail"] to make focus_window fail (the actual
# foreground stays on the competing window), move
# UIA["foreground_hwnd"] / UIA["focused_runtime_id"] to disturb the
# focus gate, flip an element's "corrupt_write" to make a set land
# wrong (read-back must then raise), set UIA["drop_sent_message"]
# to make send_message's Enter lose the message, or set
# UIA["paste_corrupt"] to make a paste land as different text
# (paste_text must then report verified=False).
def _uia_fixture() -> dict:
    return {
        "foreground_hwnd": 12345,
        "focus_fail": False,
        "window_titles": {12345: "Mock Window", 12346: "Mock Console",
                          99999: "Competing App"},
        "focused_runtime_id": "7,12345,1",
        "drop_sent_message": False,
        "paste_corrupt": False,
        "messages": [],
        "elements": [
            {"name": "Message", "control_type": "ControlType.Edit",
             "automation_id": "messageBox", "value": "",
             "has_keyboard_focus": True, "runtime_id": "7,12345,1",
             "patterns": ["ValuePattern", "TextPattern"],
             "x": 10, "y": 20, "width": 200, "height": 30,
             "corrupt_write": False},
            {"name": "Send", "control_type": "ControlType.Button",
             "automation_id": "sendBtn", "value": "",
             "has_keyboard_focus": False, "runtime_id": "7,12345,2",
             "patterns": ["InvokePattern"],
             "x": 220, "y": 20, "width": 60, "height": 30,
             "corrupt_write": False},
        ],
    }


UIA = _uia_fixture()


def resolve_uia_window(window) -> int:
    """Window spec (hwnd digits or title regex) -> mock hwnd."""
    import re as _re
    s = str(window).strip()
    if s.isdigit() and int(s) in UIA["window_titles"]:
        return int(s)
    for hwnd, title in UIA["window_titles"].items():
        if _re.search(s, title, _re.IGNORECASE):
            return hwnd
    raise RuntimeError("window not found")


def find_uia_element(name_rx: str = "", control_type: str = "",
                     index: int = 0, runtime_id: str = "") -> dict:
    """Address a mock UIA element like the Windows helper does.

    One enumeration (UIA["elements"], in fixture order — the same list
    uia_find projects): a runtime_id wins when given and resolves over
    the whole enumeration; otherwise name regex + optional control
    type, index-th match. Raises RuntimeError when the address does
    not resolve (mirrors the helper's errors)."""
    import re as _re
    if runtime_id:
        for el in UIA["elements"]:
            if el["runtime_id"] == runtime_id:
                return el
        raise RuntimeError("element runtime_id not found")
    matches = [el for el in UIA["elements"]
               if (not name_rx or _re.search(name_rx, el["name"], _re.IGNORECASE))
               and (not control_type
                    or el["control_type"] == control_type)]
    if index < 0 or index >= len(matches):
        raise RuntimeError("element index out of range")
    return matches[index]


def focused_uia_element() -> dict | None:
    """The mock element carrying keyboard focus, or None."""
    for el in UIA["elements"]:
        if el["runtime_id"] == UIA["focused_runtime_id"]:
            return el
    return None


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
    FS.clear()
    # Mutated in place, like every other structure here — NOT rebound:
    # provider modules hold PROCS by from-import, so rebinding strands
    # them on a stale list whenever a lazy provider import lands on
    # the far side of a reset (the merged-tree kill_process failure).
    PROCS[:] = [dict(p) for p in _PROCS_FIXTURE]
    CALLS.clear()
    TASKS.clear()
    _TASK_SEQ[0] = 0
    CLIPBOARD["text"] = "mock clipboard contents"
    UIA.clear()
    UIA.update(_uia_fixture())


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
