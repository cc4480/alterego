"""Read-only tools. File access is restricted to the user's own profile.

The profile root is %USERPROFILE% by default; set PC_BRIDGE_PROFILE_ROOT
to jail reads under a different root (e.g. $HOME for a future Linux
provider). Unset = Windows behavior, unchanged.
"""
import base64
import fnmatch
import getpass
import os
import platform
import re
from pathlib import Path

MAX_READ_BYTES = 1_000_000  # 1 MB cap on read_file
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_FILE_BYTES = 1_000_000  # skip files larger than this in search


def _user_profile() -> Path:
    # Provider-level constant with an env override: a future non-Windows
    # provider overrides this root instead of touching _check_path.
    override = os.environ.get("PC_BRIDGE_PROFILE_ROOT")
    if override:
        return Path(override).resolve()
    return Path(os.environ.get("USERPROFILE", str(Path.home()))).resolve()


def _system_root() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()


def _check_path(raw: str) -> Path:
    """Resolve and enforce the allowlist. Raises ValueError on denial."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _user_profile() / p
    p = p.resolve()
    profile = _user_profile()
    if p != profile and profile not in p.parents:
        raise ValueError(f"denied: outside your user profile ({profile})")
    sysroot = _system_root()
    if p == sysroot or sysroot in p.parents:
        raise ValueError("denied: Windows system directory")
    return p


def screenshot(region: dict | None = None, scale: float = 1.0) -> dict:
    """Capture the primary monitor as PNG (base64).

    region: optional {x, y, width, height} to capture part of the screen.
    scale: optional 0.1-1.0 to downscale (saves bandwidth). Requires PIL.
    Backward compatible: no args = full screenshot as before.
    """
    import mss
    import mss.tools

    if scale is not None:
        scale = float(scale)
        if not 0.1 <= scale <= 1.0:
            raise ValueError("scale must be 0.1-1.0")
    else:
        scale = 1.0

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        if region is not None:
            if not isinstance(region, dict):
                raise ValueError("region must be {x, y, width, height}")
            try:
                x = int(region["x"])
                y = int(region["y"])
                w = int(region["width"])
                h = int(region["height"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("region must be {x, y, width, height}")
            if w <= 0 or h <= 0:
                raise ValueError("region width/height must be positive")
            # Clamp to monitor bounds
            x = max(0, x)
            y = max(0, y)
            w = min(w, monitor["width"] - x)
            h = min(h, monitor["height"] - y)
            if w <= 0 or h <= 0:
                raise ValueError("region is outside the screen")
            grab_area = {"left": monitor["left"] + x,
                         "top": monitor["top"] + y,
                         "width": w, "height": h}
        else:
            grab_area = monitor
        img = sct.grab(grab_area)
        png = mss.tools.to_png(img.rgb, img.size)
        width, height = img.width, img.height

    if scale < 1.0:
        try:
            from PIL import Image
            import io
            pil_img = Image.open(io.BytesIO(png))
            new_w = max(1, int(width * scale))
            new_h = max(1, int(height * scale))
            pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            png = buf.getvalue()
            width, height = new_w, new_h
        except ImportError:
            raise RuntimeError("scale requires Pillow (pip install Pillow)")

    return {
        "png_base64": base64.b64encode(png).decode("ascii"),
        "width": width,
        "height": height,
    }


def list_windows() -> dict:
    import win32gui
    import win32process

    out = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        out.append({"hwnd": hwnd, "pid": pid, "title": title})

    win32gui.EnumWindows(_cb, None)
    return {"windows": out}


def system_info() -> dict:
    import psutil

    u = platform.uname()
    return {
        "system": u.system,
        "release": u.release,
        "version": u.version,
        "machine": u.machine,
        "hostname": u.node,
        "user": getpass.getuser(),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "mem_percent": psutil.virtual_memory().percent,
    }


def list_dir(path: str = "") -> dict:
    p = _check_path(path or ".")
    if not p.is_dir():
        raise ValueError("not a directory")
    items = []
    for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
        try:
            items.append(
                {
                    "name": child.name,
                    "dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                }
            )
        except OSError:
            continue
    return {"path": str(p), "items": items}


def read_file(path: str) -> dict:
    p = _check_path(path)
    if not p.is_file():
        raise ValueError("not a file")
    if p.stat().st_size > MAX_READ_BYTES:
        raise ValueError("file too large (>1MB)")
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not a UTF-8 text file")
    return {"path": str(p), "content": text}


def clipboard_get() -> dict:
    """Read text from the Windows clipboard (no approval; it's a read)."""
    import clipboard
    try:
        return {"text": clipboard.get_text()}
    except OSError:
        return {"text": "", "note": "clipboard unavailable"}


def search_files(pattern: str, path: str, file_pattern: str = "*",
                 max_results: int = MAX_SEARCH_RESULTS, context: int = 0,
                 case_insensitive: bool = False,
                 output_mode: str = "matches",
                 offset: int = 0) -> dict:
    """Delegate to seams.providers.windows.search (rg fast path with
    stdlib fallback). Kept here so the registry resolves the tool name
    against the read module."""
    from seams.providers.windows import search as _search
    return _search.search_files(pattern, path, file_pattern, max_results,
                                context, case_insensitive, output_mode,
                                offset)


def search_filenames(pattern: str, path: str,
                     max_results: int = MAX_SEARCH_RESULTS,
                     sort_by: str = "name", offset: int = 0) -> dict:
    """Delegate to seams.providers.windows.search. See search_files."""
    from seams.providers.windows import search as _search
    return _search.search_filenames(pattern, path, max_results, sort_by,
                                    offset)


def read_file_range(path: str, start_line: int,
                    end_line: int = 0) -> dict:
    """Read 1-indexed line ranges from a UTF-8 text file. end_line=0
    (default) means start_line+50. Respects the profile-root restriction
    and the 1MB cap, like read_file."""
    p = _check_path(path)
    if not p.is_file():
        raise ValueError("not a file")
    if p.stat().st_size > MAX_READ_BYTES:
        raise ValueError("file too large (>1MB)")
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line and end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if not end_line:
        end_line = start_line + 50
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not a UTF-8 text file")
    lines = text.splitlines()
    total = len(lines)
    # Clamp: start past EOF yields empty content, not an error.
    chunk = lines[start_line - 1:end_line] if start_line <= total else []
    return {
        "path": str(p),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total,
        "content": "\n".join(chunk),
    }
