"""Content/filename search (Windows provider). Split from read.py.

search_files uses ripgrep when installed, else a stdlib fallback with
identical semantics. rg runs with --hidden --no-ignore so results match
the fallback; .gitignore is NOT honored by either path.
"""
import fnmatch
import json
import os
import re
import shutil
from pathlib import Path

_RG_TIMEOUT_S = 120


def _checked_root(path: str) -> Path:
    # Lazy import: read.py delegates to this module (circular at top).
    from seams.providers.windows.read import _check_path
    root = _check_path(path)
    if not root.is_dir():
        raise ValueError("not a directory")
    return root


def _validate(max_results: int, offset: int, context: int = 0) -> None:
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if context < 0:
        raise ValueError("context must be >= 0")


def _compile(pattern: str, case_insensitive: bool):
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}")


def _paginate(entries: list, offset: int, max_results: int,
             sort_key=None) -> dict:
    # Deterministic order across backends (rg vs stdlib walk): sort
    # before slicing so offset pagination is stable.
    if sort_key is not None:
        entries = sorted(entries, key=sort_key)
    total = len(entries)
    return {"matches": entries[offset:offset + max_results],
            "truncated": total > offset + max_results}


_MATCH_KEY = lambda e: (e["file"], e["line_number"])  # noqa: E731
_FILE_KEY = lambda e: e["file"]  # noqa: E731


# ---- stdlib fallback ------------------------------------------------------

def _python_search(pattern: str, root: Path, file_pattern: str,
                   max_results: int, context: int,
                   case_insensitive: bool, output_mode: str,
                   offset: int) -> dict:
    from seams.providers.windows.read import MAX_SEARCH_FILE_BYTES
    rx = _compile(pattern, case_insensitive)
    max_bytes = MAX_SEARCH_FILE_BYTES
    entries = []
    if output_mode == "count":
        counts = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if not fnmatch.fnmatch(fname, file_pattern):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    if fpath.stat().st_size > max_bytes:
                        continue
                    text = fpath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue  # binary or unreadable: skip
                n = sum(1 for line in text.splitlines() if rx.search(line))
                if n:
                    counts.append((str(fpath), n))
        entries = [{"file": f, "count": n} for f, n in counts]
    else:
        seen_files = set()
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if not fnmatch.fnmatch(fname, file_pattern):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    if fpath.stat().st_size > max_bytes:
                        continue
                    text = fpath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                lines = text.splitlines()
                for lineno, line in enumerate(lines, start=1):
                    m = rx.search(line)
                    if not m:
                        continue
                    spath = str(fpath)
                    if output_mode == "files":
                        if spath not in seen_files:
                            seen_files.add(spath)
                            entries.append({"file": spath})
                        continue
                    entry = {
                        "file": spath,
                        "line_number": lineno,
                        "line_content": line[:500],
                        "match_text": m.group(0)[:200],
                    }
                    if context:
                        idx = lineno - 1
                        entry["context_before"] = \
                            lines[max(0, idx - context):idx]
                        entry["context_after"] = \
                            lines[idx + 1:idx + 1 + context]
                    entries.append(entry)
    key = _FILE_KEY if output_mode in ("files", "count") else _MATCH_KEY
    return _paginate(entries, offset, max_results, key)


# ---- ripgrep fast path ----------------------------------------------------

def _rg() -> str | None:
    return shutil.which("rg")


def _rg_run(extra: list, pattern: str, root: Path) -> str:
    """Run rg; return stdout. Fail loud on rg errors."""
    import subproc

    cmd = ([_rg(), "--hidden", "--no-ignore"] + extra
           + ["--", pattern, str(root)])
    rc, out, err, timed_out = subproc.run_noinherit(cmd, _RG_TIMEOUT_S)
    if timed_out:
        raise RuntimeError(f"rg timed out after {_RG_TIMEOUT_S}s on {root}")
    if rc == 2:
        first = (err or "").strip().splitlines()
        first = first[0] if first else "rg failed"
        if "regex parse error" in (err or ""):
            raise ValueError(f"invalid regex: {first}")
        raise RuntimeError(f"rg error: {first}")
    # rc 0 = matches, rc 1 = no matches
    return out


def _rg_search(pattern: str, root: Path, file_pattern: str,
               max_results: int, context: int,
               case_insensitive: bool, output_mode: str,
               offset: int) -> dict:
    # Validate the regex early so a bad pattern raises ValueError even
    # when rg would also reject it (message parity with the fallback).
    _compile(pattern, case_insensitive)
    base = []
    if case_insensitive:
        base.append("-i")
    if file_pattern and file_pattern != "*":
        base += ["-g", file_pattern]

    if output_mode == "files":
        out = _rg_run(base + ["-l"], pattern, root)
        files = sorted(ln for ln in out.splitlines() if ln)
        total = len(files)
        return {"matches": [{"file": f} for f in
                            files[offset:offset + max_results]],
                "truncated": total > offset + max_results}
    if output_mode == "count":
        out = _rg_run(base + ["--count"], pattern, root)
        entries = []
        for ln in out.splitlines():
            if not ln:
                continue
            # rpartition: Windows paths contain ':' (C:\...); the
            # count is always the last segment.
            f, _, c = ln.rpartition(":")
            try:
                entries.append({"file": f, "count": int(c)})
            except ValueError:
                continue
        return _paginate(entries, offset, max_results, _FILE_KEY)

    out = _rg_run(base + ["--json", "--no-heading", "-C", str(context)],
                  pattern, root)
    bufs = {}   # path -> {"matches": [(lineno, text, sub)], "lines": {}}
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        otype = obj.get("type")
        data = obj.get("data", {})
        if otype not in ("begin", "match", "context"):
            continue
        p = (data.get("path") or {}).get("text", "")
        if not isinstance(p, str) or not p:
            continue
        if otype == "begin":
            if p not in bufs:
                bufs[p] = {"matches": [], "lines": {}}
            continue
        buf = bufs.get(p)
        lineno = data.get("line_number")
        if buf is None or not isinstance(lineno, int):
            continue
        text = (data.get("lines") or {}).get("text", "")
        text = text.rstrip("\r\n") if isinstance(text, str) else ""
        buf["lines"][lineno] = text
        if otype == "match":
            subs = data.get("submatches") or []
            sub = subs[0].get("match", {}).get("text", "") if subs else ""
            sub = sub.rstrip("\r\n") if isinstance(sub, str) else ""
            buf["matches"].append((lineno, text, sub))
    entries = []
    for p, buf in bufs.items():
        lines = buf["lines"]
        for lineno, text, sub in buf["matches"]:
            entry = {
                "file": p,
                "line_number": lineno,
                "line_content": text[:500],
                "match_text": sub[:200],
            }
            if context:
                entry["context_before"] = [
                    lines[n] for n in range(lineno - context, lineno)
                    if n in lines]
                entry["context_after"] = [
                    lines[n] for n in range(lineno + 1,
                                           lineno + 1 + context)
                    if n in lines]
            entries.append(entry)
    return _paginate(entries, offset, max_results, _MATCH_KEY)


# ---- public provider functions --------------------------------------------

def search_files(pattern: str, path: str, file_pattern: str = "*",
                 max_results: int = 50, context: int = 0,
                 case_insensitive: bool = False,
                 output_mode: str = "matches",
                 offset: int = 0) -> dict:
    """Search file contents for a regex pattern under a directory.

    output_mode: 'matches' (default, per-line dicts, optional
    context_before/context_after), 'files' (unique {'file'} dicts),
    'count' ({'file', 'count'} dicts). offset paginates the result
    entries. Uses rg when installed, else the stdlib fallback.
    """
    root = _checked_root(path)
    if output_mode not in ("matches", "files", "count"):
        raise ValueError(f"bad output_mode: {output_mode!r} "
                         "(matches|files|count)")
    _validate(max_results, offset, context)
    if _rg():
        return _rg_search(pattern, root, file_pattern, max_results,
                          context, case_insensitive, output_mode, offset)
    return _python_search(pattern, root, file_pattern, max_results,
                          context, case_insensitive, output_mode, offset)


def search_filenames(pattern: str, path: str,
                     max_results: int = 50, sort_by: str = "name",
                     offset: int = 0) -> dict:
    """Find files by name glob under a directory.

    sort_by: 'name' (default, path order) or 'mtime' (newest first).
    offset paginates alongside max_results.
    """
    root = _checked_root(path)
    if sort_by not in ("name", "mtime"):
        raise ValueError(f"bad sort_by: {sort_by!r} (name|mtime)")
    _validate(max_results, offset)
    found = []  # (sort_key, result_dict)
    for fpath in root.rglob(pattern):
        if not fpath.is_file():
            continue
        try:
            st = fpath.stat()
        except OSError:
            continue
        found.append((st.st_mtime, {
            "path": str(fpath),
            "size": st.st_size,
            "modified": st.st_mtime,
        }))
    if sort_by == "mtime":
        found.sort(key=lambda r: r[0], reverse=True)
    else:
        found.sort(key=lambda r: r[1]["path"])
    files = [r[1] for r in found]
    total = len(files)
    return {"files": files[offset:offset + max_results],
            "truncated": total > offset + max_results}
