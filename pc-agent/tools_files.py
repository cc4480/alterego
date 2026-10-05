"""File write tools: create, modify, and delete files/directories.

EVERY call requires native approval (fail closed), like the other write
tools. Paths are absolute Windows paths anywhere on the machine — the
on-PC approval dialog showing the full path is the guardrail.
Text files only (UTF-8).
"""
from pathlib import Path

from approval import request_approval

MAX_WRITE_BYTES = 1024 * 1024  # 1 MB, mirrors read_file's cap


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool)):
        raise PermissionError("denied by local approval (or timed out)")


def _abs(path: str) -> Path:
    if not path or not isinstance(path, str):
        raise ValueError("path must be a non-empty string")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(f"path must be absolute: {path!r}")
    return p


def write_file(path: str, content: str) -> dict:
    """Create or overwrite a text file (creates parent dirs).

    Byte-exact: no newline translation, so write -> read roundtrips.
    """
    p = _abs(path)
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")
    preview = content if len(content) <= 300 else content[:300] + "..."
    _approved("write_file",
              f"Write {len(data)} bytes to:\n{p}\n---\n{preview}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"path": str(p), "bytes_written": len(data)}


def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Replace the first occurrence of old_text with new_text in a file."""
    p = _abs(path)
    for name, val in (("old_text", old_text), ("new_text", new_text)):
        if not isinstance(val, str) or not val:
            raise ValueError(f"{name} must be a non-empty string")
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    _approved("edit_file",
              f"Edit file:\n{p}\nreplace:\n{old_text[:300]}\nwith:\n{new_text[:300]}")
    text = p.read_bytes().decode("utf-8")
    if old_text not in text:
        raise ValueError("old_text not found in file")
    p.write_bytes(text.replace(old_text, new_text, 1).encode("utf-8"))
    return {"path": str(p), "replacements": 1}


def delete_file(path: str) -> dict:
    """Permanently delete a file (not a directory)."""
    p = _abs(path)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    _approved("delete_file", f"PERMANENTLY delete file:\n{p}")
    p.unlink()
    return {"path": str(p), "deleted": True}


def create_dir(path: str) -> dict:
    """Create a directory (including parents); no-op if it exists."""
    p = _abs(path)
    _approved("create_dir", f"Create directory:\n{p}")
    p.mkdir(parents=True, exist_ok=True)
    return {"path": str(p), "created": True}


def copy_file(src: str, dst: str) -> dict:
    """Copy a file (metadata preserved); creates destination parents."""
    import shutil

    s, d = _abs(src), _abs(dst)
    if not s.is_file():
        raise FileNotFoundError(f"not a file: {s}")
    _approved("copy_file", f"Copy file:\n{s}\nto:\n{d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    return {"src": str(s), "dst": str(d), "bytes": d.stat().st_size}


def move_file(src: str, dst: str) -> dict:
    """Move/rename a file; creates destination parents."""
    import shutil

    s, d = _abs(src), _abs(dst)
    if not s.is_file():
        raise FileNotFoundError(f"not a file: {s}")
    _approved("move_file", f"Move file:\n{s}\nto:\n{d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"src": str(s), "dst": str(d), "moved": True}


def file_info(path: str) -> dict:
    """Size and timestamps for a file or directory (no approval; read-only)."""
    from datetime import datetime, timezone

    p = _abs(path)
    if not p.exists():
        raise FileNotFoundError(f"no such path: {p}")
    st = p.stat()
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return {"path": str(p), "is_file": p.is_file(), "is_dir": p.is_dir(),
            "size": st.st_size, "modified": iso(st.st_mtime),
            "created": iso(st.st_ctime)}
