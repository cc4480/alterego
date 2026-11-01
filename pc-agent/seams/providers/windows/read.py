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


def list_windows(offset: int = 0, limit: int = 0) -> dict:
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
    out.sort(key=lambda w: w["title"].lower())
    total = len(out)
    offset = max(0, int(offset or 0))
    page = out[offset:] if not limit else out[offset:offset + max(0, int(limit))]
    return {"windows": page, "total": total, "offset": offset}


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


def list_dir(path: str = "", offset: int = 0, limit: int = 0) -> dict:
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
    total = len(items)
    offset = max(0, int(offset or 0))
    page = items[offset:] if not limit else items[offset:offset + max(0, int(limit))]
    return {"path": str(p), "items": page, "total": total, "offset": offset}


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
#
# Resolution discipline (2026-10-31, plan F2): candidates are tiered --
# tier 1 literal name equality (case-insensitive), tier 2 anchored regex
# full match, tier 3 partial regex match. The best non-empty tier wins.
# Single-element ops (click/set/get) resolve inside the winning tier:
# an explicit index picks within the tier; NO index requires exactly one
# candidate, otherwise the helper returns a structured ambiguous_target
# (or ambiguous_window) error listing the candidates -- it never silently
# acts on the first match. (2026-10-29 battery: an unanchored substring
# match resolved 'Message' to an 'Add imessage' button instead of the
# chat composer.) All coordinate casts go through Safe-Int: offscreen /
# virtualized elements can report NaN or out-of-range BoundingRectangle
# doubles, and a raw [int] cast throws and kills the whole query.
# ---------------------------------------------------------------------------
_UIA_PS = r'''
param([string]$ReqPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
$req = Get-Content -LiteralPath $ReqPath -Raw -Encoding UTF8 | ConvertFrom-Json
$AE = [System.Windows.Automation.AutomationElement]
$root = $AE::RootElement
function Safe-Int($v) {
  try {
    $d = [double]$v
    if ([double]::IsNaN($d) -or [double]::IsInfinity($d)) { return 0 }
    if ($d -gt 2147483647) { return 2147483647 }
    if ($d -lt -2147483648) { return -2147483648 }
    return [int]$d
  } catch { return 0 }
}
function Name-Tier($name, $rx) {
  if ($rx -eq '') { return 3 }
  if ($name -ieq $rx) { return 1 }
  if ($name -match ('^(?:' + $rx + ')$')) { return 2 }
  if ($name -match $rx) { return 3 }
  return 0
}
function Find-Window($spec) {
  $hwnd = 0
  if ([int]::TryParse($spec, [ref]$hwnd) -and $hwnd -ne 0) {
    try { return $AE::FromHandle((New-Object IntPtr $hwnd)) } catch { return $null }
  }
  $wc = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)
  $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $wc)
  $cands = @()
  foreach ($w in $wins) {
    try { $n = $w.Current.Name } catch { continue }
    $t = Name-Tier $n $spec
    if ($t -gt 0) { $cands += [pscustomobject]@{el=$w; name=$n; tier=$t} }
  }
  if ($cands.Count -eq 0) { return $null }
  $best = 4
  foreach ($c in $cands) { if ($c.tier -lt $best) { $best = $c.tier } }
  $top = @($cands | Where-Object { $_.tier -eq $best })
  if ($top.Count -gt 1) {
    $list = @($top | Select-Object -First 10 | ForEach-Object {
      $h = 0
      try { $h = $_.el.Current.NativeWindowHandle } catch { }
      [pscustomobject]@{name=$_.name; hwnd=(Safe-Int $h)} })
    @{ok=$false; error_code='ambiguous_window'; error=('ambiguous window: ' + $top.Count + ' windows match the spec at the best match tier; pass an hwnd from list_windows to disambiguate'); candidates=$list} | ConvertTo-Json -Compress -Depth 4
    exit 0
  }
  return $top[0].el
}
$win = Find-Window $req.window
if (-not $win) { @{ok=$false; error='window not found'} | ConvertTo-Json -Compress; exit 0 }
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$nameRx = [string]$req.name_rx
$ctReq = [string]$req.control_type
$elems = @()
$limit = [int]$req.max_results
foreach ($el in $all) {
  try { $n = $el.Current.Name } catch { continue }
  $tier = Name-Tier $n $nameRx
  if ($nameRx -ne '' -and $tier -eq 0) { continue }
  try { $ct = $el.Current.ControlType.ProgrammaticName } catch { $ct = '' }
  if ($ctReq -ne '') {
    $ctAlt = $ctReq
    if ($ctAlt -notlike 'ControlType.*') { $ctAlt = 'ControlType.' + $ctAlt }
    if (($ct -ne $ctReq) -and ($ct -ne $ctAlt)) { continue }
  }
  try { $r = $el.Current.BoundingRectangle } catch { continue }
  try { $aid = $el.Current.AutomationId } catch { $aid = '' }
  $elems += [pscustomobject]@{el=$el; name=$n; control_type=$ct; automation_id=$aid; tier=$tier; x=(Safe-Int $r.X); y=(Safe-Int $r.Y); width=(Safe-Int $r.Width); height=(Safe-Int $r.Height)}
  if ($elems.Count -ge $limit) { break }
}
$op = $req.op
if ($op -eq 'find') {
  $sorted = @()
  foreach ($tno in 1,2,3) { $sorted += @($elems | Where-Object { $_.tier -eq $tno }) }
  $proj = @($sorted | ForEach-Object { [pscustomobject]@{name=$_.name; control_type=$_.control_type; automation_id=$_.automation_id; match_tier=$_.tier; x=$_.x; y=$_.y; width=$_.width; height=$_.height} })
  @{ok=$true; count=$proj.Count; elements=$proj} | ConvertTo-Json -Compress -Depth 4
  exit 0
}
$best = 4
foreach ($e in $elems) { if ($e.tier -lt $best) { $best = $e.tier } }
$sel = @($elems | Where-Object { $_.tier -eq $best })
$idx = [int]$req.index
if ($idx -lt 0) {
  if ($sel.Count -eq 0) { @{ok=$false; error_code='not_found'; error=('no element matches name_rx "' + $nameRx + '" control_type "' + $ctReq + '"')} | ConvertTo-Json -Compress; exit 0 }
  if ($sel.Count -gt 1) {
    $cands = @($sel | Select-Object -First 10 | ForEach-Object { [pscustomobject]@{name=$_.name; control_type=$_.control_type; automation_id=$_.automation_id; x=$_.x; y=$_.y; width=$_.width; height=$_.height} })
    @{ok=$false; error_code='ambiguous_target'; error=('ambiguous target: ' + $sel.Count + ' elements match name_rx "' + $nameRx + '" control_type "' + $ctReq + '" at the best match tier; pass an index or a narrower name_rx'); candidates=$cands} | ConvertTo-Json -Compress -Depth 4; exit 0
  }
  $t = $sel[0]
} else {
  if ($idx -ge $sel.Count) { @{ok=$false; error='element index out of range'} | ConvertTo-Json -Compress; exit 0 }
  $t = $sel[$idx]
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
if ($op -eq 'set') {
  try {
    $vp = $t.el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($vp.Current.IsReadOnly) { throw 'readonly' }
    $vp.SetValue($req.text)
    $after = $null
    try { $after = $t.el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { }
    if ($null -eq $after) {
      @{ok=$false; error_code='readback_mismatch'; error='set_text readback failed: the field value could not be read back after SetValue'} | ConvertTo-Json -Compress; exit 0
    }
    $normA = ($after -replace "`r`n", "`n").TrimEnd()
    $normE = ([string]$req.text -replace "`r`n", "`n").TrimEnd()
    if ($normA -cne $normE) {
      @{ok=$false; error_code='readback_mismatch'; error=('set_text readback mismatch on "' + $t.name + '": the field does not hold the requested text')} | ConvertTo-Json -Compress; exit 0
    }
    @{ok=$true; method='value'; name=$t.name} | ConvertTo-Json -Compress; exit 0
  } catch { }
  try { $t.el.SetFocus(); @{ok=$true; method='focus'; name=$t.name} | ConvertTo-Json -Compress }
  catch { @{ok=$false; error='set_text failed: no value pattern and focus failed'} | ConvertTo-Json -Compress }
  exit 0
}
if ($op -eq 'get') {
  $val = $null; $chan = ''
  try { $val = $t.el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value; $chan = 'value' } catch { }
  if ($null -eq $val) {
    try { $tp = $t.el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern); $val = $tp.DocumentRange.GetText(-1); $chan = 'text' } catch { }
  }
  if ($null -eq $val) {
    try { $val = $t.el.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern).Current.Value; $chan = 'legacy' } catch { }
  }
  if ($null -eq $val) { @{ok=$false; error_code='readback_unavailable'; error=('cannot read back the value of "' + $t.name + '": no value, text, or legacy pattern')} | ConvertTo-Json -Compress; exit 0 }
  @{ok=$true; name=$t.name; value=$val; channel=$chan} | ConvertTo-Json -Compress; exit 0
}
@{ok=$false; error='unknown op'} | ConvertTo-Json -Compress
'''


class UiaAmbiguousError(RuntimeError):
    """Two or more equally-good UIA candidates; the helper refused to
    guess. Carries the candidate list so the caller can disambiguate
    (narrower name_rx, an explicit index, or an hwnd for windows)."""

    def __init__(self, message: str, candidates: list | None = None):
        self.candidates = list(candidates or [])
        lines = [message or "ambiguous target"]
        for c in self.candidates:
            if isinstance(c, dict):
                label = c.get("name", "?")
                if c.get("control_type"):
                    label += " [%s]" % c["control_type"]
                if c.get("hwnd"):
                    label += " (hwnd %s)" % c["hwnd"]
                lines.append("  candidate: %s" % label)
        super().__init__("\n".join(lines))


class UiaReadbackError(RuntimeError):
    """A set/type action could not be verified by reading the field
    back (value mismatch, or no readable pattern). Never a success."""


# Enumeration cap for single-element ops: resolution tiers are computed
# over the collected set, so acting ops must enumerate generously --
# stopping at the first match is exactly the failure being fixed.
UIA_ENUM_MAX = 2000


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
        res = json.loads(out.strip())
    except json.JSONDecodeError:
        raise RuntimeError("UIA helper returned bad JSON: %s" % out[-300:])
    if isinstance(res, dict):
        code = res.get("error_code")
        if code in ("ambiguous_target", "ambiguous_window"):
            raise UiaAmbiguousError(res.get("error", "ambiguous target"),
                                    res.get("candidates") or [])
        if code in ("readback_mismatch", "readback_unavailable"):
            raise UiaReadbackError(res.get("error", "readback failed"))
    return res


def uia_find(window: str, name_rx: str = "", control_type: str = "",
             max_results: int = 50) -> dict:
    """Find UI elements in a window via UI Automation (read-only).

    window: window title regex, or hwnd digits. Returns matching elements
    with name, control type, automation id, bounding rect, and match_tier
    (1 = exact name, 2 = anchored regex full match, 3 = partial match),
    best tier first. The window itself is resolved by the same tiers;
    equally-good window ties raise UiaAmbiguousError.
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


def _focused_window() -> dict | None:
    """Foreground window via win32gui, or None when unavailable."""
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return {"hwnd": hwnd, "pid": pid, "title": title}
    except Exception:
        return None


def desktop_state(include_screenshot: bool = True, scale: float = 0.5,
                  max_elements: int = 25) -> dict:
    """Windows + focused window + its UI elements + screenshot, one call.

    Every section is best-effort: a section that cannot be read reports
    its own error in ``errors`` instead of failing the whole call.
    """
    errors = {}
    try:
        windows = list_windows().get("windows", [])
    except Exception as e:  # noqa: BLE001 - per-section best effort
        windows = []
        errors["windows"] = f"{type(e).__name__}: {e}"
    focused = _focused_window()
    if focused is None:
        errors["focused"] = "could not determine the foreground window"
    elements = []
    if focused is not None:
        try:
            found = uia_find(window=str(focused["hwnd"]),
                             max_results=max(1, min(int(max_elements), 200)))
            elements = found.get("elements", [])
        except Exception as e:  # noqa: BLE001 - per-section best effort
            errors["elements"] = f"{type(e).__name__}: {e}"
    shot = None
    if include_screenshot:
        try:
            shot = screenshot(scale=float(scale))
        except Exception as e:  # noqa: BLE001 - per-section best effort
            errors["screenshot"] = f"{type(e).__name__}: {e}"
    return {"windows": windows, "focused": focused,
            "elements": elements, "screenshot": shot, "errors": errors}


def _wait_probe(kind, target, window, control_type):
    """One probe. Returns (found, detail dict)."""
    import re
    if kind == "window":
        wins = list_windows().get("windows", [])
        for w in wins:
            if re.search(target, w.get("title", "")):
                return True, {"window": w}
        return False, {"windows_seen": len(wins)}
    if kind in ("element", "text"):
        win = window
        if not win:
            focused = _focused_window()
            if focused is None:
                return False, {"error": "no window given and no "
                                        "foreground window"}
            win = str(focused["hwnd"])
        res = uia_find(window=win, name_rx=target,
                       control_type=control_type or "")
        elements = res.get("elements", [])
        if elements:
            return True, {"count": res.get("count", len(elements)),
                          "elements": elements[:5]}
        return False, {"count": 0}
    raise ValueError(f"unknown kind: {kind!r}")


def wait_for(kind: str, target: str, window: str = "",
             control_type: str = "", timeout_s: float = 10.0,
             interval_s: float = 0.5) -> dict:
    """Poll until target appears. Returns found=false on timeout."""
    import re
    import time
    if kind not in ("window", "element", "text"):
        raise ValueError("kind must be window | element | text")
    if not isinstance(target, str) or not target:
        raise ValueError("target must be a non-empty string")
    try:
        re.compile(target)
    except re.error as e:
        raise ValueError(f"target is not a valid regex: {e}")
    timeout_s = float(timeout_s)
    interval_s = float(interval_s)
    if not 0 < timeout_s <= 120:
        raise ValueError("timeout_s must be > 0 and <= 120")
    if not 0.1 <= interval_s <= 10:
        raise ValueError("interval_s must be 0.1-10")
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            found, detail = _wait_probe(kind, target, window, control_type)
        except Exception as e:  # noqa: BLE001 - transient probe failure
            found, detail = False, {"error": f"{type(e).__name__}: {e}"}
        if found:
            return {"found": True, "kind": kind, "target": target,
                    "waited_s": round(time.monotonic() - started, 3),
                    "attempts": attempts, "detail": detail}
        if time.monotonic() - started >= timeout_s:
            return {"found": False, "kind": kind, "target": target,
                    "waited_s": round(time.monotonic() - started, 3),
                    "attempts": attempts, "detail": detail}
        time.sleep(interval_s)
