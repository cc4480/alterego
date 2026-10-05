"""File write tools: create, modify, and delete files/directories.

EVERY call requires native approval (fail closed), like the other write
tools. Paths are absolute Windows paths anywhere on the machine — the
on-PC approval dialog showing the full path is the guardrail.
Text files only (UTF-8).
"""
from pathlib import Path
import hashlib

from approval import request_approval

MAX_WRITE_BYTES = 1024 * 1024  # 1 MB, mirrors read_file's cap


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stale_error(expected: str, actual: str) -> ValueError:
    return ValueError(
        f"file changed since read (expected sha256:{expected[:12]}..., "
        f"got {actual[:12]}...). Re-read the file and retry.")


def _require_fresh_bytes(data: bytes, expected: str | None) -> None:
    """Fail if expected sha256 doesn't match already-read bytes.

    expected=None skips the check (backward compat).
    """
    if expected is None:
        return
    actual = _sha256_hex(data)
    if actual != expected:
        raise _stale_error(expected, actual)


def _require_fresh_path(p: Path, expected: str | None) -> None:
    """Fail if the file on disk right now doesn't match expected sha256.

    expected=None skips the check (backward compat). A file that does
    not exist has nothing stale to protect — skip.
    """
    if expected is None or not p.is_file():
        return
    actual = _sha256_hex(p.read_bytes())
    if actual != expected:
        raise _stale_error(expected, actual)


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def _abs(path: str) -> Path:
    if not path or not isinstance(path, str):
        raise ValueError("path must be a non-empty string")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError(f"path must be absolute: {path!r}")
    return p


def _diff(old: str, new: str, path: str) -> str:
    """Unified diff, capped at 60 lines."""
    import difflib
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm="")
    return "\n".join(list(lines)[:60])


def write_file(path: str, content: str, dry_run: bool = False,
               expected_sha256: str | None = None) -> dict:
    """Create or overwrite a text file (creates parent dirs).

    Byte-exact: no newline translation, so write -> read roundtrips.
    dry_run=True: show what would happen (with diff) without writing.

    expected_sha256: fail if the file changed since the caller read it
    (staleness guard). None = no check (backward compat).
    """
    p = _abs(path)
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")
    if dry_run:
        if p.is_file():
            _require_fresh_path(p, expected_sha256)
            old = p.read_bytes().decode("utf-8", errors="replace")
            return {"dry_run": True, "would": "overwrite", "path": str(p),
                    "old_bytes": p.stat().st_size, "new_bytes": len(data),
                    "diff": _diff(old, content, str(p))}
        return {"dry_run": True, "would": "create", "path": str(p),
                "new_bytes": len(data)}
    preview = content if len(content) <= 300 else content[:300] + "..."
    _approved("write_file",
              f"Write {len(data)} bytes to:\n{p}\n---\n{preview}")
    # Staleness check right before the write (the approval dialog may
    # have sat open while the file changed underneath us).
    _require_fresh_path(p, expected_sha256)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"path": str(p), "bytes_written": len(data)}


def edit_file(path: str, old_text: str, new_text: str,
              dry_run: bool = False, require_unique: bool = True,
              read_before: bool = True,
              expected_sha256: str | None = None) -> dict:
    """Replace old_text with new_text in a file.

    require_unique=True (default): fail if old_text matches more than
    once — the caller must be more specific instead of replacing all.
    require_unique=False: replace only the first occurrence (legacy).

    read_before=True (default): the result always includes the before/after
    context and a unified diff, so the caller sees what changed.

    expected_sha256: fail if the file changed since the caller read it
    (staleness guard). None = no check (backward compat).

    dry_run=True: show the diff without modifying.
    """
    p = _abs(path)
    for name, val in (("old_text", old_text), ("new_text", new_text)):
        if not isinstance(val, str) or not val:
            raise ValueError(f"{name} must be a non-empty string")
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    data = p.read_bytes()
    _require_fresh_bytes(data, expected_sha256)
    text = data.decode("utf-8")
    matches = text.count(old_text)
    if matches == 0:
        raise ValueError("old_text not found in file")
    if require_unique and matches > 1:
        raise ValueError(
            f"multiple matches found ({matches} occurrences of old_text), "
            "be more specific — include surrounding context to make the "
            "match unique, or pass require_unique=False to replace only "
            "the first occurrence")
    new = text.replace(old_text, new_text, 1)
    diff = _diff(text, new, str(p))
    before_ctx, after_ctx = _context_snippets(text, new, old_text, new_text)
    result = {"path": str(p), "replacements": 1, "matches_found": matches,
              "diff": diff, "before": before_ctx, "after": after_ctx}
    if dry_run:
        result.update({"dry_run": True, "would": "edit"})
        return result
    _approved("edit_file",
              f"Edit file:\n{p}\nreplace:\n{old_text[:300]}\nwith:\n{new_text[:300]}")
    # Staleness check right before the write (the approval dialog may
    # have sat open while the file changed underneath us).
    _require_fresh_path(p, expected_sha256)
    p.write_bytes(new.encode("utf-8"))
    return result


def _context_snippets(old: str, new: str, old_text: str, new_text: str,
                      radius: int = 3) -> tuple[str, str]:
    """Return (before, after) snippets around the edit, radius lines."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    idx = next((i for i, line in enumerate(old_lines) if old_text in line), 0)
    lo, hi = max(0, idx - radius), idx + radius + 1
    before = "\n".join(old_lines[lo:hi])
    after = "\n".join(new_lines[lo:hi + (new_text.count("\n")
                                         - old_text.count("\n"))])
    return before, after


def delete_file(path: str, dry_run: bool = False) -> dict:
    """Permanently delete a file (not a directory).

    dry_run=True: show what would be deleted without deleting.
    """
    p = _abs(path)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {p}")
    if dry_run:
        return {"dry_run": True, "would": "delete", "path": str(p),
                "bytes": p.stat().st_size}
    _approved("delete_file", f"PERMANENTLY delete file:\n{p}")
    p.unlink()
    return {"path": str(p), "deleted": True}


def create_dir(path: str, dry_run: bool = False) -> dict:
    """Create a directory (including parents); no-op if it exists.

    dry_run=True: show what would happen without creating.
    """
    p = _abs(path)
    if dry_run:
        return {"dry_run": True,
                "would": "no-op (exists)" if p.is_dir() else "create",
                "path": str(p)}
    _approved("create_dir", f"Create directory:\n{p}")
    p.mkdir(parents=True, exist_ok=True)
    return {"path": str(p), "created": True}


def copy_file(src: str, dst: str, dry_run: bool = False) -> dict:
    """Copy a file (metadata preserved); creates destination parents.

    dry_run=True: show what would happen without copying.
    """
    import shutil

    s, d = _abs(src), _abs(dst)
    if not s.is_file():
        raise FileNotFoundError(f"not a file: {s}")
    if dry_run:
        return {"dry_run": True, "would": "copy", "src": str(s), "dst": str(d),
                "bytes": s.stat().st_size,
                "overwrite": d.is_file()}
    _approved("copy_file", f"Copy file:\n{s}\nto:\n{d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    return {"src": str(s), "dst": str(d), "bytes": d.stat().st_size}


def move_file(src: str, dst: str, dry_run: bool = False) -> dict:
    """Move/rename a file; creates destination parents.

    dry_run=True: show what would happen without moving.
    """
    import shutil

    s, d = _abs(src), _abs(dst)
    if not s.is_file():
        raise FileNotFoundError(f"not a file: {s}")
    if dry_run:
        return {"dry_run": True, "would": "move", "src": str(s), "dst": str(d),
                "bytes": s.stat().st_size,
                "overwrite": d.is_file()}
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
