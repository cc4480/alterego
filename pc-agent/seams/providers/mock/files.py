"""Mock file tools: in-memory virtual filesystem, profile-root jail.

Every function returns EXACTLY its ToolDef's result_keys (mock contract:
fixed key sets, so the result_keys suite can assert equality).

dry_run=True never mutates the FS. Deletes happen only inside the virtual
FS — no host file is ever touched.
"""
from datetime import datetime, timezone

from seams.providers.mock import (
    FS, diff_text, ensure_parents, is_dir, norm_path,
)

MAX_WRITE_BYTES = 1024 * 1024


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snippets(old: str, new: str, old_text: str,
              radius: int = 3) -> tuple[str, str]:
    """(before, after) context snippets around the edit."""
    lines = old.splitlines()
    idx = next((i for i, line in enumerate(lines) if old_text in line), 0)
    lo, hi = max(0, idx - radius), idx + radius + 1
    return "\n".join(lines[lo:hi]), "\n".join(new.splitlines()[lo:hi])


def write_file(path: str, content: str, dry_run: bool = False) -> dict:
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")
    p = norm_path(path)
    old_entry = FS.get(p)
    old_bytes = (len((old_entry.get("content") or "").encode("utf-8"))
                 if old_entry and not old_entry.get("is_dir") else 0)
    if dry_run:
        would = "overwrite" if old_entry else "create"
        old_text = (old_entry.get("content") or "") if old_entry else ""
        return {"path": p, "bytes_written": 0, "dry_run": True,
                "would": would, "old_bytes": old_bytes,
                "new_bytes": len(data),
                "diff": diff_text(old_text, content, p)}
    ensure_parents(p)
    FS[p] = {"content": content, "is_dir": False, "modified": _iso_now()}
    return {"path": p, "bytes_written": len(data), "dry_run": False,
            "would": None, "old_bytes": old_bytes,
            "new_bytes": len(data), "diff": None}


def edit_file(path: str, old_text: str, new_text: str,
              dry_run: bool = False, require_unique: bool = True,
              read_before: bool = True) -> dict:
    for name, val in (("old_text", old_text), ("new_text", new_text)):
        if not isinstance(val, str) or not val:
            raise ValueError(f"{name} must be a non-empty string")
    p = norm_path(path)
    entry = FS.get(p)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {path!r}")
    text = entry.get("content") or ""
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
    diff = diff_text(text, new, p)
    before, after = _snippets(text, new, old_text)
    result = {"path": p, "replacements": 1, "matches_found": matches,
              "diff": diff, "before": before, "after": after,
              "dry_run": False, "would": None}
    if dry_run:
        result.update({"dry_run": True, "would": "edit"})
        return result
    entry["content"] = new
    entry["modified"] = _iso_now()
    return result


def delete_file(path: str, dry_run: bool = False) -> dict:
    p = norm_path(path)
    entry = FS.get(p)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {path!r}")
    size = len((entry.get("content") or "").encode("utf-8"))
    if dry_run:
        return {"path": p, "deleted": False, "dry_run": True,
                "would": "delete", "bytes": size}
    del FS[p]  # virtual-FS delete only; the host disk is never touched
    return {"path": p, "deleted": True, "dry_run": False,
            "would": None, "bytes": size}


def create_dir(path: str, dry_run: bool = False) -> dict:
    p = norm_path(path)
    exists = is_dir(p)
    if dry_run:
        return {"path": p, "created": False, "dry_run": True,
                "would": "no-op (exists)" if exists else "create"}
    if not exists:
        ensure_parents(p)
        FS[p] = {"content": None, "is_dir": True}
    return {"path": p, "created": True, "dry_run": False, "would": None}


def copy_file(src: str, dst: str, dry_run: bool = False) -> dict:
    s, d = norm_path(src), norm_path(dst)
    entry = FS.get(s)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {src!r}")
    size = len((entry.get("content") or "").encode("utf-8"))
    overwrite = FS.get(d) is not None and not FS.get(d, {}).get("is_dir")
    if dry_run:
        return {"src": s, "dst": d, "bytes": 0, "dry_run": True,
                "would": "copy", "overwrite": overwrite}
    ensure_parents(d)
    FS[d] = {"content": entry.get("content"), "is_dir": False,
             "modified": _iso_now()}
    return {"src": s, "dst": d, "bytes": size, "dry_run": False,
            "would": None, "overwrite": overwrite}


def move_file(src: str, dst: str, dry_run: bool = False) -> dict:
    s, d = norm_path(src), norm_path(dst)
    entry = FS.get(s)
    if entry is None or entry.get("is_dir"):
        raise FileNotFoundError(f"not a file: {src!r}")
    size = len((entry.get("content") or "").encode("utf-8"))
    overwrite = FS.get(d) is not None and not FS.get(d, {}).get("is_dir")
    if dry_run:
        return {"src": s, "dst": d, "moved": False, "dry_run": True,
                "would": "move", "bytes": size, "overwrite": overwrite}
    ensure_parents(d)
    FS[d] = {"content": entry.get("content"), "is_dir": False,
             "modified": _iso_now()}
    del FS[s]
    return {"src": s, "dst": d, "moved": True, "dry_run": False,
            "would": None, "bytes": size, "overwrite": overwrite}


def file_info(path: str) -> dict:
    p = norm_path(path)
    entry = FS.get(p)
    if entry is None and not is_dir(p):
        raise FileNotFoundError(f"no such path: {path!r}")
    file = entry is not None and not entry.get("is_dir")
    size = len((entry.get("content") or "").encode("utf-8")) if file else 0
    ts = (entry or {}).get("modified") or _iso_now()
    return {"path": p, "is_file": file, "is_dir": not file,
            "size": size, "modified": ts, "created": ts}
