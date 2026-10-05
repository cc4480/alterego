"""Mock read/observe tools: fixed canned dicts with the declared result_keys.

No platform dependencies, no side effects.
"""
from seams.providers.mock import (
    CLIPBOARD, FS, PROFILE_ROOT, is_dir, list_children, norm_path,
)

# 1x1 red PNG (base64). Valid PNG bytes, tiny — enough for pipeline tests.
_RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")


def screenshot() -> dict:
    return {"png_base64": _RED_PNG_B64, "width": 1, "height": 1}


def list_windows() -> dict:
    return {"windows": [
        {"hwnd": 12345, "title": "Mock Window", "pid": 1234},
        {"hwnd": 12346, "title": "Mock Console", "pid": 512},
    ]}


def system_info() -> dict:
    return {"system": "MockOS", "release": "1.0", "version": "mock-build",
            "machine": "x86_64", "hostname": "mock-host",
            "user": "mock-user", "cpu_percent": 12.5, "mem_percent": 42.0}


def list_dir(path: str = "") -> dict:
    p = norm_path(path) if path else PROFILE_ROOT
    if not is_dir(p):
        raise FileNotFoundError(f"no such directory: {path!r}")
    return {"path": p, "items": list_children(p)}


def read_file(path: str) -> dict:
    p = norm_path(path)
    entry = FS.get(p)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {path!r}")
    return {"path": p, "content": entry.get("content") or ""}


def clipboard_get() -> dict:
    return {"text": CLIPBOARD["text"], "note": "mock: in-memory clipboard"}
