"""Mock read/observe tools: fixed canned dicts with the declared result_keys.

No platform dependencies, no side effects.
"""
import fnmatch
import hashlib
import re

from seams.providers.mock import (
    CLIPBOARD, FS, PROFILE_ROOT, is_dir, list_children, norm_path,
)

# 1x1 red PNG (base64). Valid PNG bytes, tiny — enough for pipeline tests.
_RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")


def screenshot(region: dict | None = None, scale: float = 1.0) -> dict:
    # Mock ignores region/scale for the image bytes but reflects scale
    # in the reported dimensions (canned 1x1 PNG stays 1x1).
    if scale is not None and not 0.1 <= float(scale) <= 1.0:
        raise ValueError("scale must be 0.1-1.0")
    if region is not None and not isinstance(region, dict):
        raise ValueError("region must be {x, y, width, height}")
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
    content = entry.get("content") or ""
    return {"path": p, "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}


def clipboard_get() -> dict:
    return {"text": CLIPBOARD["text"], "note": "mock: in-memory clipboard"}


def _mock_files_under(root: str) -> list:
    """All file paths in the virtual FS under root (sorted)."""
    prefix = root.rstrip("/") + "/"
    return sorted(k for k, v in FS.items()
                  if k.startswith(prefix) and not v.get("is_dir"))


def search_files(pattern: str, path: str, file_pattern: str = "*",
                 max_results: int = 50) -> dict:
    root = norm_path(path) if path else PROFILE_ROOT
    if not is_dir(root):
        raise FileNotFoundError(f"no such directory: {path!r}")
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}")
    matches = []
    truncated = False
    for fpath in _mock_files_under(root):
        fname = fpath.rsplit("/", 1)[-1]
        if not fnmatch.fnmatch(fname, file_pattern):
            continue
        content = FS[fpath].get("content") or ""
        for lineno, line in enumerate(content.splitlines(), start=1):
            m = rx.search(line)
            if m:
                matches.append({
                    "file": fpath,
                    "line_number": lineno,
                    "line_content": line[:500],
                    "match_text": m.group(0)[:200],
                })
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break
    return {"matches": matches, "truncated": truncated}


def search_filenames(pattern: str, path: str,
                     max_results: int = 50) -> dict:
    root = norm_path(path) if path else PROFILE_ROOT
    if not is_dir(root):
        raise FileNotFoundError(f"no such directory: {path!r}")
    files = []
    truncated = False
    for fpath in _mock_files_under(root):
        fname = fpath.rsplit("/", 1)[-1]
        if not fnmatch.fnmatch(fname, pattern):
            continue
        content = FS[fpath].get("content") or ""
        files.append({
            "path": fpath,
            "size": len(content.encode("utf-8")),
            "modified": 0.0,
        })
        if len(files) >= max_results:
            truncated = True
            break
    return {"files": files, "truncated": truncated}


def read_file_range(path: str, start_line: int,
                    end_line: int = 0) -> dict:
    p = norm_path(path)
    entry = FS.get(p)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {path!r}")
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line and end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if not end_line:
        end_line = start_line + 50
    lines = (entry.get("content") or "").splitlines()
    total = len(lines)
    chunk = lines[start_line - 1:end_line] if start_line <= total else []
    content = entry.get("content") or ""
    return {
        "path": p,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total,
        "content": "\n".join(chunk),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
