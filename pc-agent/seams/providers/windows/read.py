"""Read-only tools. File access is restricted to the user's own profile.

The profile root is %USERPROFILE% by default; set PC_BRIDGE_PROFILE_ROOT
to jail reads under a different root (e.g. $HOME for a future Linux
provider). Unset = Windows behavior, unchanged.
"""
import base64
import fnmatch
import getpass
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

import subproc

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


def _resolve_cwd(cwd: str | None) -> str | None:
    """Validate optional working dir vs profile root; None passes through."""
    p = _check_path(cwd) if cwd is not None else None
    if p is not None and not p.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return str(p) if p else None


# ---------------------------------------------------------------------------
# Screenshot pipeline (screenshot + screenshot_tiles).
#
# Q2: Pillow powers `scale`; when scale is omitted the long edge is fitted
# to ~1280px, while an explicit scale (including 1.0 = native) is honoured.
# S5: format="webp" (default, lossless) | "jpeg" (quality 95) | "png".
# S2: screenshot_tiles returns a blob-referenced tile manifest with
# optional delta mode against the previous frame kept in memory.
# ---------------------------------------------------------------------------
_SCREENSHOT_FORMATS = {
    "png": ("PNG", "image/png"),
    "jpeg": ("JPEG", "image/jpeg"),
    "webp": ("WEBP", "image/webp"),
}
_SCREENSHOT_LONG_EDGE = 1280
_JPEG_QUALITY = 95


def _grab_area(monitor: dict, region: dict | None) -> dict:
    """Resolve the mss grab rectangle for `region` (clamped to monitor)."""
    if region is None:
        return monitor
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
    return {"left": monitor["left"] + x, "top": monitor["top"] + y,
            "width": w, "height": h}


def _auto_scale(width: int, height: int) -> float:
    """Scale fitting the long edge to ~1280px; never upscales."""
    long_edge = max(width, height)
    if long_edge <= _SCREENSHOT_LONG_EDGE:
        return 1.0
    return _SCREENSHOT_LONG_EDGE / long_edge


def screenshot(region: dict | None = None, scale: float = None,
               format: str = "webp") -> dict:
    """Capture the virtual all-monitors screen, encoded per `format`.

    region: optional {x, y, width, height} to capture part of the screen.
    scale: optional 0.1-1.0 downscale factor (Pillow). When omitted
        (None), the image is downscaled so its long edge fits ~1280px;
        an explicit scale=1.0 returns the native resolution.
    format: "webp" (default, lossless), "jpeg" (quality 95) or "png".

    Result: image_base64 (the encoded bytes), mime, format, width,
    height, scale (the applied factor) and png_base64 — populated with
    the same base64 string when format="png", else None (legacy field;
    pre-S5 consumers read it only from PNG captures).
    """
    if not isinstance(format, str) or format.lower() not in _SCREENSHOT_FORMATS:
        raise ValueError("format must be 'webp', 'jpeg' or 'png'")
    fmt = format.lower()
    pil_format, mime = _SCREENSHOT_FORMATS[fmt]
    if scale is not None:
        scale = float(scale)
        if not 0.1 <= scale <= 1.0:
            raise ValueError("scale must be 0.1-1.0")

    import mss
    import mss.tools

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        grab_area = _grab_area(monitor, region)
        img = sct.grab(grab_area)
        rgb, (width, height) = img.rgb, img.size

    applied = _auto_scale(width, height) if scale is None else scale
    new_w = max(1, round(width * applied))
    new_h = max(1, round(height * applied))

    try:
        from PIL import Image
        import io
        pil_img = Image.frombytes("RGB", (width, height), rgb)
        if (new_w, new_h) != (width, height):
            pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        if fmt == "webp":
            pil_img.save(buf, format=pil_format, lossless=True)
        elif fmt == "jpeg":
            pil_img.save(buf, format=pil_format, quality=_JPEG_QUALITY)
        else:
            pil_img.save(buf, format=pil_format)
        encoded = buf.getvalue()
    except ImportError:
        if fmt == "png" and applied >= 1.0:
            # Native-resolution PNG needs no Pillow (mss encodes it).
            encoded = mss.tools.to_png(rgb, (width, height))
        elif applied < 1.0:
            raise RuntimeError("scale requires Pillow (pip install Pillow)")
        else:
            raise RuntimeError(
                f"format {fmt!r} requires Pillow (pip install Pillow)")

    image_b64 = base64.b64encode(encoded).decode("ascii")
    return {
        "image_base64": image_b64,
        "mime": mime,
        "format": fmt,
        "width": new_w,
        "height": new_h,
        "scale": applied,
        "png_base64": image_b64 if fmt == "png" else None,
    }


# --- screenshot_tiles (S2): tiled capture + delta --------------------------

_TILES_DEFAULT_SIZE = 512
# Previous frame for delta mode: {"key": frame identity, "hashes": [...]}.
# Kept in memory only; a resolution/grid/region change invalidates it.
_tiles_state: dict = {"key": None, "hashes": []}


def _reset_tiles_state() -> None:
    """Forget the delta baseline (tests and provider resets)."""
    _tiles_state["key"] = None
    _tiles_state["hashes"] = []


def _tile_grid(width: int, height: int, tile_size: int, grid) -> tuple:
    """Resolve (cols, rows, tile_w, tile_h) from tile_size and/or grid.

    grid: None -> derive from tile_size; {"cols","rows"} -> explicit
    grid; {"tile_size": n} -> derive from that size; int -> column
    count, rows derived to keep tiles roughly square.
    """
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
    if cols < 1 or rows < 1:
        raise ValueError("grid cols/rows must be >= 1")
    tile_w = -(-width // cols)
    tile_h = -(-height // rows)
    return cols, rows, tile_w, tile_h


def screenshot_tiles(region: dict | None = None, tile_size: int = 512,
                     grid=None, delta: bool = False) -> dict:
    """Capture the screen as raw-RGB tiles stored in the blob store.

    Each tile is grabbed via mss, stored with blob_store.put (the tile
    blob IS its raw RGB bytes, so its sha256 is the blob ref), and the
    returned manifest describes the grid plus per-tile hashes. The
    assembled full frame's sha256 is included so a client can verify
    its stitch and detect desync.

    delta=True: tiles whose raw bytes are unchanged since the previous
    screenshot_tiles frame are listed by index with their sha256 only
    (no blob_ref); changed tiles carry blob_ref/size/sha256. A change
    of resolution, grid or region invalidates the baseline and a full
    manifest is returned with delta=False.
    """
    import mss

    import blob_store

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        grab_area = _grab_area(monitor, region)
        width, height = grab_area["width"], grab_area["height"]
        cols, rows, tile_w, tile_h = _tile_grid(
            width, height, tile_size, grid)
        tiles_raw = []  # (index, x, y, w, h, raw_rgb)
        for r in range(rows):
            for c in range(cols):
                x, y = c * tile_w, r * tile_h
                tw = min(tile_w, width - x)
                th = min(tile_h, height - y)
                area = {"left": grab_area["left"] + x,
                        "top": grab_area["top"] + y,
                        "width": tw, "height": th}
                timg = sct.grab(area)
                tiles_raw.append((r * cols + c, x, y, tw, th, timg.rgb))

    # Assemble the raw full frame (row-interleaved tile slices).
    frame = bytearray(width * height * 3)
    for _idx, x, y, tw, th, raw in tiles_raw:
        for ly in range(th):
            src = ly * tw * 3
            dst = ((y + ly) * width + x) * 3
            frame[dst:dst + tw * 3] = raw[src:src + tw * 3]
    full_sha = hashlib.sha256(bytes(frame)).hexdigest()

    key = (grab_area["left"], grab_area["top"], width, height,
           cols, rows)
    hashes = [hashlib.sha256(raw).hexdigest()
              for _i, _x, _y, _w, _h, raw in tiles_raw]
    is_delta = bool(delta) and _tiles_state["key"] == key \
        and len(_tiles_state["hashes"]) == len(hashes)
    prev_hashes = _tiles_state["hashes"] if is_delta else []

    entries = []
    changed, unchanged = [], []
    for (idx, x, y, tw, th, raw), digest in zip(tiles_raw, hashes):
        if is_delta and prev_hashes[idx] == digest:
            entries.append({"index": idx, "sha256": digest})
            unchanged.append(idx)
        else:
            entries.append({
                "index": idx, "x": x, "y": y, "width": tw, "height": th,
                "blob_ref": blob_store.put(raw), "size": len(raw),
                "sha256": digest,
            })
            changed.append(idx)
    _tiles_state["key"] = key
    _tiles_state["hashes"] = hashes

    manifest = {
        "grid": {"cols": cols, "rows": rows},
        "tile_size": int(tile_size) if tile_size is not None else None,
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
    return {"path": str(p), "content": text,
            "sha256": hashlib.sha256(data).hexdigest()}


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
        "path": str(p), "start_line": start_line, "end_line": end_line,
        "total_lines": total, "content": "\n".join(chunk),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Windows UI Automation core (shared by uia_find / uia_click / uia_set_text).
# One PowerShell script, JSON in/out via temp files (no quoting pitfalls).
# Pure ASCII for PowerShell 5.1.
# ---------------------------------------------------------------------------
_UIA_PS = r'''
param([string]$ReqPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
$req = Get-Content -LiteralPath $ReqPath -Raw -Encoding UTF8 | ConvertFrom-Json
$AE = [System.Windows.Automation.AutomationElement]
$root = $AE::RootElement
function Find-Window($spec) {
  $hwnd = 0
  if ([int]::TryParse($spec, [ref]$hwnd) -and $hwnd -ne 0) {
    try { return $AE::FromHandle((New-Object IntPtr $hwnd)) } catch { return $null }
  }
  $wc = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)
  $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $wc)
  foreach ($w in $wins) {
    try { $n = $w.Current.Name } catch { continue }
    if ($n -match $spec) { return $w }
  }
  return $null
}
function El-Value($el) {
  try { $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); return $vp.Current.Value } catch { return $null }
}
function El-Text($el) {
  $v = El-Value $el
  if ($null -ne $v) { return $v }
  try { $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern); return $tp.DocumentRange.GetText(-1) } catch { return '' }
}
function El-Patterns($el) {
  try { return @($el.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName -replace 'Identifiers$','' }) } catch { return @() }
}
function El-Rid($el) { try { return ($el.GetRuntimeId() -join ',') } catch { return '' } }
function El-Focus($el) { try { return [bool]$el.Current.HasKeyboardFocus } catch { return $false } }
if ($req.op -eq 'focused') {
  # Focus is global: no window lookup (paste_text read-back has no
  # window spec). value/text are included so callers can verify what
  # actually landed in the focused element.
  try {
    $fe = $AE::FocusedElement
    $fh = 0
    try { $fh = [int]$fe.Current.NativeWindowHandle } catch { }
    @{ok=$true; runtime_id=(El-Rid $fe); name=$fe.Current.Name; control_type=$fe.Current.ControlType.ProgrammaticName; has_keyboard_focus=(El-Focus $fe); value=(El-Value $fe); text=(El-Text $fe); window_hwnd=$fh} | ConvertTo-Json -Compress
  } catch { @{ok=$false; error='focused element unavailable'} | ConvertTo-Json -Compress }
  exit 0
}
$win = Find-Window $req.window
if (-not $win) { @{ok=$false; error='window not found'} | ConvertTo-Json -Compress; exit 0 }
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$elems = @()
$limit = [int]$req.max_results
foreach ($el in $all) {
  try { $n = $el.Current.Name } catch { continue }
  if ($req.name_rx -ne '' -and ($n -notmatch $req.name_rx)) { continue }
  try { $ct = $el.Current.ControlType.ProgrammaticName } catch { $ct = '' }
  if ($req.control_type -ne '' -and ($ct -ne $req.control_type)) { continue }
  try { $r = $el.Current.BoundingRectangle } catch { continue }
  try { $aid = $el.Current.AutomationId } catch { $aid = '' }
  $elems += [pscustomobject]@{el=$el; name=$n; control_type=$ct; automation_id=$aid; x=[int]$r.X; y=[int]$r.Y; width=[int]$r.Width; height=[int]$r.Height; value=(El-Value $el); text=(El-Text $el); has_keyboard_focus=(El-Focus $el); runtime_id=(El-Rid $el); patterns=@(El-Patterns $el)}
  if ($elems.Count -ge $limit) { break }
}
$op = $req.op
if ($op -eq 'find') {
  $proj = @($elems | ForEach-Object { [pscustomobject]@{name=$_.name; control_type=$_.control_type; automation_id=$_.automation_id; x=$_.x; y=$_.y; width=$_.width; height=$_.height; value=$_.value; has_keyboard_focus=$_.has_keyboard_focus; runtime_id=$_.runtime_id} })
  @{ok=$true; count=$proj.Count; elements=$proj} | ConvertTo-Json -Compress -Depth 4
  exit 0
}
# One enumeration, two addresses: a RuntimeId (stable across
# re-enumeration, returned by 'find') wins when supplied; otherwise the
# index into THIS enumeration -- collected with the same query, filters
# and ordering 'find' uses, so a find index always names the same
# element here.
$ridWanted = ''
try { $ridWanted = [string]$req.runtime_id } catch { $ridWanted = '' }
if ($ridWanted -ne '') {
  $t = $null
  foreach ($cand in $elems) { if ($cand.runtime_id -eq $ridWanted) { $t = $cand; break } }
  if (-not $t) { @{ok=$false; error='element runtime_id not found'} | ConvertTo-Json -Compress; exit 0 }
} else {
  $idx = [int]$req.index
  if ($idx -lt 0 -or $idx -ge $elems.Count) { @{ok=$false; error='element index out of range'} | ConvertTo-Json -Compress; exit 0 }
  $t = $elems[$idx]
}
$cx = $t.x + [int]($t.width / 2)
$cy = $t.y + [int]($t.height / 2)
if ($op -eq 'click') {
  try {
    $ip = $t.el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $ip.Invoke()
    @{ok=$true; method='invoke'; name=$t.name; x=$cx; y=$cy} | ConvertTo-Json -Compress; exit 0
  } catch { }
  @{ok=$true; method='coord'; name=$t.name; x=$cx; y=$cy} | ConvertTo-Json -Compress; exit 0
}
if ($op -eq 'get') {
  @{ok=$true; name=$t.name; control_type=$t.control_type; automation_id=$t.automation_id; x=$t.x; y=$t.y; width=$t.width; height=$t.height; value=(El-Value $t.el); text=(El-Text $t.el); has_keyboard_focus=(El-Focus $t.el); runtime_id=(El-Rid $t.el); patterns=@(El-Patterns $t.el); window_hwnd=[int]$win.Current.NativeWindowHandle} | ConvertTo-Json -Compress -Depth 4
  exit 0
}
if ($op -eq 'set') {
  try {
    $vp = $t.el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($vp.Current.IsReadOnly) { throw 'readonly' }
    $vp.SetValue($req.text)
    $after = $vp.Current.Value
    if ($after -ne $req.text) { @{ok=$false; error=('value mismatch after SetValue: read back "' + $after + '"'); value_after=$after} | ConvertTo-Json -Compress; exit 0 }
    @{ok=$true; method='value'; name=$t.name; value_after=$after} | ConvertTo-Json -Compress; exit 0
  } catch { }
  try { $t.el.SetFocus() }
  catch { @{ok=$false; error='set_text failed: no value pattern and focus failed'} | ConvertTo-Json -Compress; exit 0 }
  $frid = ''
  try { $frid = El-Rid $AE::FocusedElement } catch { }
  if ($frid -ne (El-Rid $t.el)) { @{ok=$false; error='focus fallback aborted: focused element is not the target element'} | ConvertTo-Json -Compress; exit 0 }
  @{ok=$true; method='focus'; name=$t.name; focused_runtime_id=$frid} | ConvertTo-Json -Compress
  exit 0
}
@{ok=$false; error='unknown op'} | ConvertTo-Json -Compress
'''


def _uia_invoke(payload: dict, timeout_s: int = 30) -> dict:
    """Run the UIA PowerShell helper; returns its parsed JSON response."""
    req_fd, req_path = tempfile.mkstemp(suffix=".json")
    ps_fd, ps_path = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(req_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True)
        with os.fdopen(ps_fd, "w", encoding="ascii", errors="strict") as f:
            f.write(_UIA_PS)
        rc, out, err, timed_out = subproc.run_noinherit(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", ps_path,
             "-ReqPath", req_path], timeout_s)
    finally:
        for p in (req_path, ps_path):
            try:
                os.unlink(p)
            except OSError:
                pass
    if timed_out:
        raise RuntimeError("UIA query timed out")
    if rc != 0:
        raise RuntimeError("UIA helper failed: %s" % err[-500:])
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError:
        raise RuntimeError("UIA helper returned bad JSON: %s" % out[-300:])


# Addressing ops (click/get/set) enumerate with this cap — the same
# cap uia_find clamps to — never index+1. The old index+1 truncation
# made click/set re-enumerate a different (shorter) list than the one
# find reported, so an index taken from a find result could fall out
# of range or name a different element. With one cap, one query, one
# filter set and one ordering, the collected list's prefix is exactly
# find's list, and any find index resolves to the same element.
UIA_ENUM_MAX_RESULTS = 200


def _uia_payload(op: str, window, name_rx: str = "",
                 control_type: str = "", index: int = 0,
                 runtime_id: str = "", **extra) -> dict:
    """Build a UIA helper request with the shared enumeration.

    When runtime_id is given it is the sole address: filters are
    cleared so the element is resolved by RuntimeId over the full
    enumeration (RuntimeIds are stable across re-enumeration).
    """
    if runtime_id:
        payload = {"op": op, "window": window, "name_rx": "",
                   "control_type": "", "index": 0,
                   "max_results": UIA_ENUM_MAX_RESULTS,
                   "runtime_id": str(runtime_id)}
    else:
        payload = {"op": op, "window": window, "name_rx": name_rx or "",
                   "control_type": control_type or "",
                   "index": int(index),
                   "max_results": UIA_ENUM_MAX_RESULTS,
                   "runtime_id": ""}
    payload.update(extra)
    return payload


def uia_find(window: str, name_rx: str = "", control_type: str = "",
             max_results: int = 50) -> dict:
    """Find UI elements in a window via UI Automation (read-only).

    window: window title regex, or hwnd digits. Returns matching elements
    with name, control type, automation id, and bounding rect.
    """
    if not isinstance(window, str) or not window.strip():
        raise ValueError("window must be a non-empty title regex or hwnd")
    max_results = max(1, min(int(max_results), 200))
    res = _uia_invoke({"op": "find", "window": window,
                       "name_rx": name_rx or "",
                       "control_type": control_type or "",
                       "max_results": max_results})
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "uia_find failed"))
    return {"ok": True, "count": int(res.get("count", 0)),
            "elements": res.get("elements") or []}


def uia_get(window: str, name_rx: str = "", control_type: str = "",
            index: int = 0) -> dict:
    """Read one UI element via UI Automation (read-only).

    Same addressing as uia_find/uia_set_text. Returns the element's
    Value/Text, HasKeyboardFocus, RuntimeId, and available patterns —
    the read-back half of the verified-write contract (cf. CDP fill()).
    """
    if not isinstance(window, str) or not window.strip():
        raise ValueError("window must be a non-empty title regex or hwnd")
    res = _uia_invoke(_uia_payload("get", window, name_rx, control_type,
                                   index))
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "uia_get failed"))
    return {"ok": True, "name": res.get("name", ""),
            "control_type": res.get("control_type", ""),
            "automation_id": res.get("automation_id", ""),
            "value": res.get("value"),
            "text": res.get("text") or "",
            "has_keyboard_focus": bool(res.get("has_keyboard_focus")),
            "runtime_id": res.get("runtime_id", ""),
            "patterns": res.get("patterns") or [],
            "x": int(res.get("x", 0)), "y": int(res.get("y", 0)),
            "width": int(res.get("width", 0)),
            "height": int(res.get("height", 0))}
