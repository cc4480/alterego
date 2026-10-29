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


def _control_type_matches(el_control_type: str, control_type: str) -> bool:
    """Shared control-type filter: the filter matches the element's
    full ProgrammaticName ("ControlType.Edit") OR its short segment
    after "ControlType." ("Edit"), case-insensitive."""
    if not control_type:
        return True
    full = (el_control_type or "").lower()
    short = full.rsplit(".", 1)[-1]
    want = control_type.lower()
    want_short = want.rsplit(".", 1)[-1]
    return full == want or short == want_short


def _name_tier_match(name: str, name_rx: str) -> bool:
    """True when `name` is in the winning name tier for `name_rx`:
    Tier A literal equality, else Tier B anchored regex full match
    (both case-insensitive). Used for the message-list entries, which
    are not part of UIA["elements"]."""
    import re as _re
    if name.lower() == name_rx.lower():
        return True
    return _re.fullmatch(name_rx, name, _re.IGNORECASE) is not None


def resolve_uia_elements(name_rx: str = "", control_type: str = "",
                         contains: str = "") -> list:
    """The shared UIA resolution: the winning candidate set, in
    document (fixture) order. Mirrors the Windows helper exactly.

    Candidates are UIA["elements"] filtered by control type. When
    `contains` is set there is no name filter: the set is elements
    whose name, value, or text contains the string (case-sensitive
    substring — the send_message confirmation probe). Otherwise name
    matching is tiered: Tier A = case-insensitive literal equality
    with name_rx; Tier B = case-insensitive regex FULL match
    (anchored). The first non-empty tier wins; an empty name_rx means
    no name filter. There is NO substring/unanchored fallback.
    """
    import re as _re
    cands = [el for el in UIA["elements"]
             if _control_type_matches(el["control_type"], control_type)]
    if contains:
        return [el for el in cands
                if contains in (el["name"] or "")
                or contains in (el.get("value") or "")
                or contains in (el.get("text") or el.get("value") or "")]
    if not name_rx:
        return cands
    tier_a = [el for el in cands
              if el["name"].lower() == name_rx.lower()]
    if tier_a:
        return tier_a
    rx = _re.compile(name_rx, _re.IGNORECASE)
    return [el for el in cands if rx.fullmatch(el["name"])]


def find_uia_element(name_rx: str = "", control_type: str = "",
                     index: int | None = None,
                     runtime_id: str = "") -> dict:
    """Address a mock UIA element like the Windows helper does.

    One enumeration (UIA["elements"], in fixture order — the same list
    uia_find projects): a runtime_id wins when given and resolves over
    the whole enumeration. Otherwise the element is picked from the
    winning set of resolve_uia_elements: an explicitly given index
    indexes into it ('element index out of range' outside it); with no
    index, exactly one candidate resolves, zero is 'element not
    found', and more than one raises a structured ambiguity error
    naming the candidates."""
    if runtime_id:
        for el in UIA["elements"]:
            if el["runtime_id"] == runtime_id:
                return el
        raise RuntimeError("element runtime_id not found")
    matches = resolve_uia_elements(name_rx, control_type)
    if index is not None:
        if index < 0 or index >= len(matches):
            raise RuntimeError("element index out of range")
        return matches[index]
    if not matches:
        raise RuntimeError("element not found")
    if len(matches) > 1:
        named = ", ".join(
            f"{el['name']!r} ({el['control_type']}, "
            f"automation_id={el['automation_id']!r}, "
            f"runtime_id={el['runtime_id']!r})"
            for el in matches[:10])
        raise RuntimeError(
            f"ambiguous target: {len(matches)} elements match "
            f"name_rx={name_rx!r} control_type={control_type!r}: {named}")
    return matches[0]


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
    global PROCS
    FS.clear()
    PROCS = [dict(p) for p in _PROCS_FIXTURE]
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
