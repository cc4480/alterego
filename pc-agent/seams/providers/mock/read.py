"""Mock read/observe tools: fixed canned dicts with the declared result_keys.

No platform dependencies, no side effects.
"""
import fnmatch
import hashlib
import re

from seams.providers.mock import (
    CLIPBOARD, FS, PROFILE_ROOT, is_dir, list_children, norm_path,
)

# 1x1 red frames (base64). Valid encoded bytes per format, tiny — enough
# for pipeline tests. The WebP/JPEG constants are real Pillow encodings
# of the same 1x1 red pixel (WebP lossless; JPEG quality 95).
_RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")
_RED_WEBP_B64 = "UklGRhwAAABXRUJQVlA4TA8AAAAvAAAAAAcQ/Y/+ByKi/wEA"
_RED_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUE"
    "BAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQIC"
    "AgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoK"
    "CgoKCgoKCgoKCgoKCgr/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
    "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx"
    "8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD4"
    "vooor+Uz/fw//9k=")
_SCREENSHOT_BODIES = {
    "png": (_RED_PNG_B64, "image/png"),
    "webp": (_RED_WEBP_B64, "image/webp"),
    "jpeg": (_RED_JPEG_B64, "image/jpeg"),
}


def screenshot(region: dict | None = None, scale: float = None,
               format: str = "webp") -> dict:
    # Parity with the Windows provider's contract (Q2/S5): same
    # validation, same result keys. The canned frame is 1x1, so the
    # auto-scale for an omitted scale is 1.0 and dimensions stay 1x1;
    # an explicit scale is reported back as the applied scale.
    if not isinstance(format, str) or format.lower() not in _SCREENSHOT_BODIES:
        raise ValueError("format must be 'webp', 'jpeg' or 'png'")
    fmt = format.lower()
    if scale is not None and not 0.1 <= float(scale) <= 1.0:
        raise ValueError("scale must be 0.1-1.0")
    if region is not None and not isinstance(region, dict):
        raise ValueError("region must be {x, y, width, height}")
    body, mime = _SCREENSHOT_BODIES[fmt]
    applied = 1.0 if scale is None else float(scale)
    return {"image_base64": body, "mime": mime, "format": fmt,
            "width": 1, "height": 1, "scale": applied,
            "png_base64": body if fmt == "png" else None}


# --- screenshot_tiles parity (S2) -------------------------------------------
# Synthetic 16x8 RGB frame, sliced in memory (the mock never touches the
# disk or a real blob store: blob_refs are content hashes of the raw tile
# bytes, kept in _TILE_BLOBS so tests can resolve them).
_MOCK_FRAME_W, _MOCK_FRAME_H = 16, 8
_mock_frame = bytearray(_MOCK_FRAME_W * _MOCK_FRAME_H * 3)
for _y in range(_MOCK_FRAME_H):
    for _x in range(_MOCK_FRAME_W):
        _o = (_y * _MOCK_FRAME_W + _x) * 3
        _mock_frame[_o:_o + 3] = bytes(
            ((_x * 16) % 256, (_y * 32) % 256, ((_x + _y) * 8) % 256))
_TILE_BLOBS: dict = {}
_tiles_state: dict = {"key": None, "hashes": []}


def set_mock_frame(rgb: bytes, width: int, height: int) -> None:
    """Replace the synthetic tiles frame (test hook for delta tests)."""
    global _mock_frame, _MOCK_FRAME_W, _MOCK_FRAME_H
    if len(rgb) != width * height * 3:
        raise ValueError("rgb length must be width*height*3")
    _mock_frame = bytearray(rgb)
    _MOCK_FRAME_W, _MOCK_FRAME_H = width, height


def get_mock_tile(blob_ref: str) -> bytes | None:
    """Raw tile bytes for a mock blob_ref (test hook; no disk involved)."""
    hexpart = blob_ref.split(":", 1)[1] if ":" in blob_ref else blob_ref
    return _TILE_BLOBS.get(hexpart)


def _reset_tiles_state() -> None:
    _tiles_state["key"] = None
    _tiles_state["hashes"] = []


def screenshot_tiles(region: dict | None = None, tile_size: int = 512,
                     grid=None, delta: bool = False) -> dict:
    """Manifest parity with the Windows provider (same keys/shape).

    region is validated but the synthetic frame has no screen geometry;
    region changes still invalidate the delta baseline via the key.
    """
    if region is not None and not isinstance(region, dict):
        raise ValueError("region must be {x, y, width, height}")
    width, height = _MOCK_FRAME_W, _MOCK_FRAME_H
    cols = rows = None
    if isinstance(grid, dict):
        if "tile_size" in grid:
            tile_size = int(grid["tile_size"])
        cols = int(grid["cols"]) if grid.get("cols") else None
        rows = int(grid["rows"]) if grid.get("rows") else None
    elif grid is not None:
        cols = int(grid)
    if tile_size is None or int(tile_size) < 1:
        raise ValueError("tile_size must be a positive int")
    tile_size = int(tile_size)
    if cols is None:
        cols = max(1, -(-width // tile_size))
    if rows is None:
        if isinstance(grid, int):
            tile_w0 = -(-width // cols)
            rows = max(1, -(-height // tile_w0))
        else:
            rows = max(1, -(-height // tile_size))
    tile_w = -(-width // cols)
    tile_h = -(-height // rows)
    frame = bytes(_mock_frame)
    tiles_raw = []
    for r in range(rows):
        for c in range(cols):
            x, y = c * tile_w, r * tile_h
            tw, th = min(tile_w, width - x), min(tile_h, height - y)
            raw = bytearray(tw * th * 3)
            for ly in range(th):
                src = ((y + ly) * width + x) * 3
                raw[ly * tw * 3:(ly + 1) * tw * 3] = frame[src:src + tw * 3]
            tiles_raw.append((r * cols + c, x, y, tw, th, bytes(raw)))
    full_sha = hashlib.sha256(frame).hexdigest()
    key = (repr(sorted(region.items())) if region else None,
           width, height, cols, rows)
    hashes = [hashlib.sha256(raw).hexdigest()
              for _i, _x, _y, _w, _h, raw in tiles_raw]
    is_delta = bool(delta) and _tiles_state["key"] == key \
        and len(_tiles_state["hashes"]) == len(hashes)
    prev_hashes = _tiles_state["hashes"] if is_delta else []
    entries, changed, unchanged = [], [], []
    for (idx, x, y, tw, th, raw), digest in zip(tiles_raw, hashes):
        if is_delta and prev_hashes[idx] == digest:
            entries.append({"index": idx, "sha256": digest})
            unchanged.append(idx)
        else:
            _TILE_BLOBS[digest] = raw
            entries.append({
                "index": idx, "x": x, "y": y, "width": tw, "height": th,
                "blob_ref": f"sha256:{digest}", "size": len(raw),
                "sha256": digest})
            changed.append(idx)
    _tiles_state["key"] = key
    _tiles_state["hashes"] = hashes
    manifest = {
        "grid": {"cols": cols, "rows": rows},
        "tile_size": tile_size,
        "tile_width": tile_w,
        "tile_height": tile_h,
        "width": width,
        "height": height,
        "full_frame_sha256": full_sha,
        "delta": is_delta,
        "tiles": entries,
    }
    if is_delta:
        manifest["changed"] = changed
        manifest["unchanged"] = unchanged
    return manifest


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
                 max_results: int = 50, context: int = 0,
                 case_insensitive: bool = False,
                 output_mode: str = "matches",
                 offset: int = 0) -> dict:
    """Content search over the virtual FS.

    output_mode: 'matches' (default) -> per-line match dicts, with
    optional context_before/context_after when context > 0;
    'files' -> unique {'file'} dicts in first-seen order;
    'count' -> {'file', 'count'} dicts with full per-file counts.
    offset skips the first N result entries (pagination).
    """
    root = norm_path(path) if path else PROFILE_ROOT
    if not is_dir(root):
        raise FileNotFoundError(f"no such directory: {path!r}")
    if output_mode not in ("matches", "files", "count"):
        raise ValueError(f"bad output_mode: {output_mode!r} "
                         "(matches|files|count)")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if context < 0:
        raise ValueError("context must be >= 0")
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"invalid regex: {e}")
    entries = []
    if output_mode == "count":
        counts = []  # (file, count) in first-seen order
        for fpath in _mock_files_under(root):
            if not fnmatch.fnmatch(fpath.rsplit("/", 1)[-1], file_pattern):
                continue
            content = FS[fpath].get("content") or ""
            n = sum(1 for line in content.splitlines() if rx.search(line))
            if n:
                counts.append((fpath, n))
        entries = [{"file": f, "count": n} for f, n in counts]
    else:
        seen_files = set()
        for fpath in _mock_files_under(root):
            if not fnmatch.fnmatch(fpath.rsplit("/", 1)[-1], file_pattern):
                continue
            content = FS[fpath].get("content") or ""
            lines = content.splitlines()
            for lineno, line in enumerate(lines, start=1):
                m = rx.search(line)
                if not m:
                    continue
                if output_mode == "files":
                    if fpath not in seen_files:
                        seen_files.add(fpath)
                        entries.append({"file": fpath})
                    continue
                entry = {
                    "file": fpath,
                    "line_number": lineno,
                    "line_content": line[:500],
                    "match_text": m.group(0)[:200],
                }
                if context:
                    idx = lineno - 1
                    entry["context_before"] = lines[max(0, idx - context):idx]
                    entry["context_after"] = lines[idx + 1:idx + 1 + context]
                entries.append(entry)
    total = len(entries)
    return {"matches": entries[offset:offset + max_results],
            "truncated": total > offset + max_results}


def search_filenames(pattern: str, path: str,
                     max_results: int = 50, sort_by: str = "name",
                     offset: int = 0) -> dict:
    root = norm_path(path) if path else PROFILE_ROOT
    if not is_dir(root):
        raise FileNotFoundError(f"no such directory: {path!r}")
    if sort_by not in ("name", "mtime"):
        raise ValueError(f"bad sort_by: {sort_by!r} (name|mtime)")
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    rows = []  # (mtime_key, result_dict); name order is the walk order
    for fpath in _mock_files_under(root):
        fname = fpath.rsplit("/", 1)[-1]
        if not fnmatch.fnmatch(fname, pattern):
            continue
        entry = FS[fpath]
        content = entry.get("content") or ""
        rows.append((entry.get("modified") or "", {
            "path": fpath,
            "size": len(content.encode("utf-8")),
            "modified": 0.0,
        }))
    if sort_by == "mtime":
        # ISO-8601 strings sort chronologically; newest first. The sort
        # is stable, so ties keep name order.
        rows.sort(key=lambda r: r[0], reverse=True)
    files = [r[1] for r in rows]
    total = len(files)
    return {"files": files[offset:offset + max_results],
            "truncated": total > offset + max_results}


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


def uia_find(window: str, name_rx: str = "", control_type: str = "",
             max_results: int = 50) -> dict:
    return {"ok": True, "count": 1, "elements": [
        {"name": "mock", "control_type": "ControlType.Button",
         "automation_id": "mock1", "x": 1, "y": 2,
         "width": 3, "height": 4}]}
