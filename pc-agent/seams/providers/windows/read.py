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
import shutil
import struct
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


# ---------------------------------------------------------------------------
# camera_snapshot — ONE still frame from a PC camera. NOT a silent read:
# this provider carries its own approval gate (privacy MF-2), an
# audit-or-refuse pre-flight (MF-4), and explicit per-mode consent
# behaviour (MF-3), all BEFORE any capture device is opened.
#
# Backend chain (capture-backends research, 2026-10-26):
#   1. ffmpeg / DirectShow — best engine when ffmpeg is on PATH
#      (PnP-stable device names, loud non-zero exits, signalstats for
#      the blank-frame check, native scale filter for max_width).
#   2. Media Foundation via ctypes (seams.providers.windows.camera_mf)
#      — the only unconditional zero-new-dependency backend; mfplat
#      ships with Windows 10.
#   WinRT MediaCapture via PowerShell is deliberately NOT in the chain
#   (packaged-app consent expectations, non-agile threading, hang-prone
#   opaque async failures). If both backends fail, the error names
#   every backend tried — never a fake or blank success.
# ---------------------------------------------------------------------------

def _validate_camera_args(device=None, max_width=None):
    """Shared arg contract for camera_snapshot (mock mirrors this).
    Returns (device, max_width) normalised. Raises ValueError."""
    if isinstance(device, bool) or (
            device is not None and not isinstance(device, (int, str))):
        raise ValueError(
            "device must be a camera index (int) or name (str)")
    if isinstance(device, int) and device < 0:
        raise ValueError("device index must be >= 0")
    if isinstance(device, str) and not device.strip():
        raise ValueError("device name must be non-empty")
    if isinstance(device, str):
        device = device.strip()
    if max_width is not None:
        if isinstance(max_width, bool) or not isinstance(max_width, int):
            raise ValueError("max_width must be an int (pixels)")
        if not 16 <= max_width <= 7680:
            raise ValueError("max_width must be 16-7680")
    return device, max_width


def _camera_device_label(device) -> str:
    if device is None:
        return "default (first camera)"
    if isinstance(device, int):
        return f"index {device}"
    return str(device)


def _camera_gate(device) -> None:
    """Approval + audit gate. Runs BEFORE any device is opened.

    Behaviour by permission mode (explicit, per privacy MF-3 — the
    coordinator/Carlos decision: dontAsk is the owner's own confirmed
    startup choice, so it is accepted, never silently inherited):
    - default / acceptEdits / auto-approve: _approved() shows the
      per-capture dialog. camera_snapshot's tier is always_ask, so the
      dialog fires even under PC_BRIDGE_AUTO_APPROVE=1. camera_snapshot
      is NOT in ACCEPT_EDITS_AUTO (MF-10) and never may be.
    - dontAsk / PC_BRIDGE_FULL_ACCESS=1: no dialog exists to show;
      consent is session-level (confirmed once at startup by
      startup_confirm) and is recorded as such in the audit event.
    - plan posture never reaches here: toolcall stubs write=True
      calls before the provider runs.

    Audit-or-refuse (MF-4): EVENT_LOG_ENABLED=0 refuses outright, and
    the MediaCaptureConsent event must actually land in the event log
    before capture proceeds — if events.append returns None the
    capture is refused with a named error.
    """
    if os.environ.get("EVENT_LOG_ENABLED", "1") == "0":
        raise RuntimeError(
            "camera_snapshot refused: event log is disabled "
            "(EVENT_LOG_ENABLED=0); senses captures require an audit "
            "record (privacy MF-4)")
    import approval
    import events
    from seams.providers.windows.pc import _approved
    label = _camera_device_label(device)
    # MF-8: the dialog text is informed — which device, and that the
    # image leaves this PC to the requesting agent/operator.
    _approved("camera_snapshot",
              f"Capture ONE still photo from camera: {label}\n"
              "The image will be sent off this PC to the requesting "
              "agent/operator.")
    consent = ("session_level" if approval.is_full_access()
               else "per_capture_dialog")
    event_id = events.append("MediaCaptureConsent", {
        "tool": "camera_snapshot",
        "permission_mode": approval.get_permission_mode(),
        "consent": consent,
        "device": label,
    })
    if event_id is None:
        raise RuntimeError(
            "camera_snapshot refused: could not write the capture "
            "consent event to the event log (privacy MF-4: no audit, "
            "no capture)")


# ffmpeg's -list_devices prefix changed across versions: classic builds
# print [dshow @ 0x...], current builds (probed on Looko 2026-10-26,
# Gyan Essentials) print [in#0 @ 0x...]. Match any bracketed log prefix;
# the (video)/(audio) type tag is what actually classifies the entry.
_DSHOW_VIDEO_RE = re.compile(r'\[[^\]]*\]\s+"([^"]+)"\s+\(([^)]*)\)')
_DSHOW_ALT_RE = re.compile(r'\[[^\]]*\]\s+Alternative name\s+"([^"]+)"')


def _parse_dshow_devices(text: str) -> list:
    """Parse `ffmpeg -list_devices` stderr into [(name, alt_name)] for
    video devices, in enumeration order. Pure function (unit-tested)."""
    devices = []
    current = None
    for line in (text or "").splitlines():
        m = _DSHOW_VIDEO_RE.search(line)
        if m:
            current = None
            if "video" in m.group(2):
                current = [m.group(1), ""]
                devices.append(current)
            continue
        m = _DSHOW_ALT_RE.search(line)
        if m and current is not None:
            current[1] = m.group(1)
    return [(name, alt) for name, alt in devices]


def _list_dshow_devices(exe: str) -> list:
    """Enumerate DirectShow video devices. ffmpeg exits non-zero for
    -list_devices by design; the device list is on stderr."""
    _rc, _out, err, _timed = subproc.run_noinherit(
        [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow",
         "-i", "dummy"], 15)
    return _parse_dshow_devices(err)


def _resolve_dshow_device(devices: list, device) -> tuple:
    """(name, video_device_number|None) for the request. No silent
    substitution (MF-7): an unmatched request is a hard error naming
    the device looked for and the devices that exist."""
    if device is None:
        if not devices:
            raise RuntimeError(
                "camera_snapshot: no camera device found (ffmpeg "
                "-list_devices enumerated 0 video devices; requested "
                "device: default (first camera))")
        return devices[0][0], None
    if isinstance(device, int):
        if device >= len(devices):
            raise RuntimeError(
                f"camera_snapshot: camera device index {device} not "
                f"found (ffmpeg enumerated: "
                f"{[n for n, _a in devices]})")
        name = devices[device][0]
        # Duplicate friendly names are disambiguated by occurrence.
        occurrence = [n for n, _a in devices[:device + 1]].count(name) - 1
        return name, (occurrence if occurrence else None)
    needle = device.lower()
    for i, (name, alt) in enumerate(devices):
        if needle in name.lower() or (alt and needle in alt.lower()):
            occurrence = [n for n, _a in devices[:i + 1]].count(name) - 1
            return name, (occurrence if occurrence else None)
    if devices:
        raise RuntimeError(
            f"camera_snapshot: camera device {device!r} not found "
            f"(ffmpeg enumerated: {[n for n, _a in devices]})")
    # Enumeration found nothing readable; ffmpeg itself will fail loud
    # naming the device if the string is wrong.
    return device, None


def _png_dimensions(png: bytes) -> tuple:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("camera frame is not a PNG")
    width, height = struct.unpack(">II", png[16:24])
    if width <= 0 or height <= 0:
        raise RuntimeError("camera frame has no dimensions")
    return width, height


def _ffmpeg_frame_stats(exe: str, png_path: str) -> tuple:
    """(mean_luma, luma_range) via ffmpeg signalstats — the backend's
    own measurement, no image library needed."""
    rc, out, err, timed_out = subproc.run_noinherit(
        [exe, "-hide_banner", "-i", png_path, "-vf",
         "signalstats,metadata=print", "-f", "null", "-"], 15)
    text = (out or "") + (err or "")
    if timed_out:
        raise RuntimeError("ffmpeg signalstats timed out verifying frame")
    stats = {}
    for key in ("YAVG", "YMIN", "YMAX"):
        m = re.search(rf"lavfi\.signalstats\.{key}=([0-9.]+)", text)
        if m:
            stats[key] = float(m.group(1))
    if len(stats) != 3:
        raise RuntimeError(
            "camera_snapshot: could not verify frame is not blank "
            f"(ffmpeg signalstats incomplete: rc={rc})")
    return stats["YAVG"], stats["YMAX"] - stats["YMIN"]


def _capture_ffmpeg(device, max_width) -> tuple:
    """(png_bytes, device_name, width, height, mean_luma, is_flat)."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg backend unavailable: ffmpeg not on PATH")
    devices = _list_dshow_devices(exe)
    name, devnum = _resolve_dshow_device(devices, device)
    fd, png_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        vf = "select='gte(n\\,10)'"  # skip ~10 warm-up frames (auto-exposure)
        if max_width is not None:
            vf += f",scale='min({max_width}\\,iw)':-2"  # backend-native scale
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "dshow"]
        if devnum:
            cmd += ["-video_device_number", str(devnum)]
        cmd += ["-i", f"video={name}", "-vf", vf, "-frames:v", "1",
                "-y", png_path]
        rc, _out, err, timed_out = subproc.run_noinherit(cmd, 30)
        if timed_out:
            raise RuntimeError(
                f"ffmpeg capture from {name!r} timed out after 30s")
        if rc != 0:
            raise RuntimeError(
                f"ffmpeg could not capture from camera {name!r}: "
                f"{(err or '').strip()[-400:]}")
        png = Path(png_path).read_bytes()
        if not png:
            raise RuntimeError(
                f"ffmpeg produced an empty frame from camera {name!r}")
        width, height = _png_dimensions(png)
        mean, luma_range = _ffmpeg_frame_stats(exe, png_path)
        return png, name, width, height, mean, luma_range <= 1.0
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass


def _finish_camera_frame(png, backend, device_name, width, height,
                         mean_luma, is_flat) -> dict:
    """MF-7: a black or near-uniform frame is an explicit NON-success
    (privacy shutter closed / lens covered), never a delivered image."""
    if mean_luma < 2.0 or is_flat:
        raise RuntimeError(
            "camera_snapshot: frame_blank_or_shutter_closed "
            f"(mean luma {mean_luma:.2f}, flat={is_flat}) — privacy "
            "shutter closed or lens covered; no image returned")
    # Device/backend/dims first so toolcall's 200-char result_summary
    # in the audit log names WHAT was captured (privacy §2.1); the
    # base64 payload rides last.
    return {
        "device": device_name,
        "backend": backend,
        "width": width,
        "height": height,
        "png_base64": base64.b64encode(png).decode("ascii"),
    }


def camera_snapshot(device=None, max_width=None) -> dict:
    """Capture ONE still frame from a PC camera as PNG (base64).

    device: camera index (int) or name substring (str); default first
    camera. max_width: downscale cap honored by the capture backend
    itself (ffmpeg scale filter / Media Foundation mode selection) —
    no Pillow. Approval-gated (always_ask) via _camera_gate BEFORE
    any device opens. One call = one frame (MF-9: no interval/stream
    parameters exist, by design)."""
    device, max_width = _validate_camera_args(device, max_width)
    _camera_gate(device)
    errors = []
    try:
        png, dev_name, w, h, mean, is_flat = _capture_ffmpeg(
            device, max_width)
        return _finish_camera_frame(png, "ffmpeg-dshow", dev_name,
                                    w, h, mean, is_flat)
    except Exception as e:  # noqa: BLE001 - try the next backend
        errors.append(f"ffmpeg-dshow: {e}")
    try:
        from seams.providers.windows import camera_mf
        png, dev_name, w, h, mean, var = camera_mf.capture(
            device, max_width)
        return _finish_camera_frame(png, camera_mf.BACKEND_NAME,
                                    dev_name, w, h, mean, var < 0.5)
    except Exception as e:  # noqa: BLE001 - aggregate into the loud error
        errors.append(f"media-foundation: {e}")
    raise RuntimeError(
        "camera_snapshot: no capture backend succeeded — "
        + " | ".join(errors))


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
  $elems += [pscustomobject]@{el=$el; name=$n; control_type=$ct; automation_id=$aid; x=[int]$r.X; y=[int]$r.Y; width=[int]$r.Width; height=[int]$r.Height}
  if ($elems.Count -ge $limit) { break }
}
$op = $req.op
if ($op -eq 'find') {
  $proj = @($elems | ForEach-Object { [pscustomobject]@{name=$_.name; control_type=$_.control_type; automation_id=$_.automation_id; x=$_.x; y=$_.y; width=$_.width; height=$_.height} })
  @{ok=$true; count=$proj.Count; elements=$proj} | ConvertTo-Json -Compress -Depth 4
  exit 0
}
$idx = [int]$req.index
if ($idx -lt 0 -or $idx -ge $elems.Count) { @{ok=$false; error='element index out of range'} | ConvertTo-Json -Compress; exit 0 }
$t = $elems[$idx]
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
    @{ok=$true; method='value'; name=$t.name} | ConvertTo-Json -Compress; exit 0
  } catch { }
  try { $t.el.SetFocus(); @{ok=$true; method='focus'; name=$t.name} | ConvertTo-Json -Compress }
  catch { @{ok=$false; error='set_text failed: no value pattern and focus failed'} | ConvertTo-Json -Compress }
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
