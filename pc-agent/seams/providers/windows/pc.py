"""PC awareness and control: what's happening, signaling the user, taking control.

Read-only (no approval): active_window, idle_seconds, list_processes.
Write (approval dialog): notify, speak, set_volume, power.
"""
import base64
import ctypes
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave as _wave
from ctypes import wintypes

from approval import request_approval


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def _proc_name(pid: int) -> str:
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        if ok:
            return buf.value.rsplit("\\", 1)[-1]
    except Exception:
        pass
    return ""


def active_window() -> dict:
    """Foreground window: hwnd, title, pid, process. No approval."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {"hwnd": hwnd, "title": buf.value, "pid": pid.value,
            "process": _proc_name(pid.value)}


def idle_seconds() -> dict:
    """Seconds since the last keyboard/mouse input. No approval."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    lii = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        raise OSError("GetLastInputInfo failed")
    now = ctypes.windll.kernel32.GetTickCount64()
    return {"idle_seconds": max(0, (now - lii.dwTime) // 1000)}


def list_processes() -> dict:
    """Running processes: pid + exe name. No approval."""
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        raise OSError("process snapshot failed")
    procs = []
    try:
        pe = PROCESSENTRY32(ctypes.sizeof(PROCESSENTRY32))
        if k32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                procs.append({"pid": pe.th32ProcessID, "exe": pe.szExeFile,
                              "parent": pe.th32ParentProcessID})
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        k32.CloseHandle(snap)
    return {"count": len(procs), "processes": procs}


def _ps_quote(s: str) -> str:
    return s.replace("'", "''")


def notify(title: str, message: str, timeout_s: int = 5) -> dict:
    """Windows tray balloon notification. Requires approval."""
    if not isinstance(title, str) or not isinstance(message, str):
        raise ValueError("title and message must be strings")
    if not title.strip() or not message.strip():
        raise ValueError("title and message must be non-empty")
    timeout_s = max(1, min(int(timeout_s), 30))
    _approved("notify", f"Show notification:\n{title}\n{message}")
    ps = ("Add-Type -AssemblyName System.Windows.Forms; "
          "$n = New-Object System.Windows.Forms.NotifyIcon; "
          "$n.Icon = [System.Drawing.SystemIcons]::Information; "
          "$n.Visible = $true; "
          f"$n.ShowBalloonTip({timeout_s * 1000}, '{_ps_quote(title[:120])}', "
          f"'{_ps_quote(message[:250])}', 'Info')")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, timeout=30)
    return {"notified": True, "title": title}


def speak(text: str) -> dict:
    """Speak text aloud through the PC speakers (SAPI). Requires approval."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if len(text) > 500:
        raise ValueError("text must be 1-500 chars")
    _approved("speak", f"Speak aloud:\n{text}")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Add-Type -AssemblyName System.Speech; "
         f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{_ps_quote(text)}')"],
        capture_output=True, timeout=180)
    return {"spoken_chars": len(text)}


# ---- record_audio -------------------------------------------------------
# Backend chain (research/CAPTURE-BACKENDS.md, zero new deps):
#   1. winmm waveIn via ctypes — per-device selection, raw PCM, named
#      MMSYSERR_* failures.
#   2. ffmpeg dshow — only when ffmpeg is already on PATH.
#   3. winmm MCI (mciSendString) — LAST RESORT: system default device
#      only, cannot select a device; never used when a device hint is
#      given.
# Privacy gates (SENSES-PRIVACY-REVIEW MF-1..MF-9) are enforced in
# record_audio() itself, before any microphone handle opens.

RECORD_MAX_S = 30
NEAR_SILENT_PEAK = 0.01       # peak below this (of full scale) = silent
_RATE_GUARD_MIN_INTERVAL_S = 10.0   # MF-12: no de-facto streaming
_RECORD_RATE = 44100
_RECORD_CHANNELS = 1
_last_capture_end = [0.0]     # epoch of last completed capture (MF-12)


def _validate_record_args(duration_s, device):
    """Provider-side arg validation (never trust the caller, MF-9)."""
    if isinstance(duration_s, bool) or duration_s is None:
        raise ValueError("duration_s must be an int number of seconds")
    if isinstance(duration_s, float) and duration_s.is_integer():
        duration_s = int(duration_s)
    if not isinstance(duration_s, int):
        raise ValueError("duration_s must be an int number of seconds")
    if duration_s < 1 or duration_s > RECORD_MAX_S:
        raise ValueError(
            f"duration_s must be 1-{RECORD_MAX_S} (hard cap), "
            f"got {duration_s}")
    if device is not None:
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device must be a non-empty string or None")
        device = device.strip()
    return duration_s, device


def _available_backends(is_windows, ffmpeg_path):
    """Ordered capture backends for this machine. Pure/deterministic so
    the priority order is unit-testable off-Windows."""
    backends = []
    if is_windows:
        backends.append("wavein")
    if ffmpeg_path:
        backends.append("ffmpeg")
    if is_windows:
        backends.append("mci")  # last resort, see module notes above
    return backends


_MMSYSERR_NAMES = {
    0: "MMSYSERR_NOERROR", 1: "MMSYSERR_ERROR", 2: "MMSYSERR_BADDEVICEID",
    3: "MMSYSERR_NOTENABLED", 4: "MMSYSERR_ALLOCATED",
    5: "MMSYSERR_INVALHANDLE", 6: "MMSYSERR_NODRIVER", 7: "MMSYSERR_NOMEM",
    8: "MMSYSERR_NOTSUPPORTED", 10: "MMSYSERR_BADERRNUM",
    11: "MMSYSERR_INVALFLAG", 12: "MMSYSERR_INVALPARAM",
    32: "WAVERR_BADFORMAT", 33: "WAVERR_STILLPLAYING",
    34: "WAVERR_UNPREPARED",
}


def _mmsyserr_name(code):
    return _MMSYSERR_NAMES.get(code, f"MMSYSERR_{code}")


def _mm_check(rc, fn_name):
    """Raise a named, loud error for a non-zero winmm result code."""
    if rc == 0:
        return
    text = ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.waveInGetErrorTextW(
            rc, buf, 256)
        text = buf.value
    except Exception:
        pass
    raise RuntimeError(
        f"{fn_name} failed: {_mmsyserr_name(rc)} ({rc})"
        + (f" — {text}" if text else ""))


def _match_device_index(names, hint):
    """Index of the enumerated device matching `hint` (case-insensitive
    substring; WAVEINCAPS names truncate at 31 chars, so hints match
    prefixes too). None when nothing matches — the caller fails loud
    rather than recording a different mic."""
    h = hint.lower()
    for i, n in enumerate(names):
        if h in n.lower() or n.lower() in h:
            return i
    return None


def _pcm_stats(pcm, sampwidth):
    """(peak, rms) as 0.0-1.0 of full scale, from raw PCM. Stdlib only."""
    import array
    import math
    if sampwidth == 2:
        a = array.array("h")
        a.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
        if sys.byteorder == "big":
            a.byteswap()
        full = 32768.0
        vals = a
    elif sampwidth == 1:
        vals = [b - 128 for b in pcm]
        full = 128.0
    else:
        raise ValueError(f"unsupported sample width: {sampwidth}")
    if not vals:
        return 0.0, 0.0
    peak = max(abs(v) for v in vals) / full
    rms = math.sqrt(sum(v * v for v in vals) / len(vals)) / full
    return round(peak, 4), round(rms, 4)


def _wav_from_pcm(pcm, rate=_RECORD_RATE, channels=_RECORD_CHANNELS):
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _wav_info(wav_bytes):
    """Parse a WAV with the stdlib wave module: rate/channels/duration
    plus exact peak/rms from its frames."""
    with _wave.open(io.BytesIO(wav_bytes), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    peak, rms = _pcm_stats(frames, sampwidth)
    n_frames = len(frames) // (sampwidth * channels)
    return {"sample_rate": rate, "channels": channels,
            "duration_s": round(n_frames / rate, 3) if rate else 0.0,
            "peak": peak, "rms": rms}


def _finalize_capture(wav_bytes, backend, device_label):
    """Build the result, or fail loud. MF-7: a near-silent capture is a
    NON-success (silent_capture) naming the measured peak — never a
    'successful' silent WAV."""
    info = _wav_info(wav_bytes)
    if info["peak"] < NEAR_SILENT_PEAK:
        raise RuntimeError(
            f"silent_capture: peak {info['peak']:.4f} is below the "
            f"{NEAR_SILENT_PEAK} floor — the mic ({device_label}) "
            "delivered near-silence (muted, disabled, wrong device, or "
            "blocked by the Windows microphone privacy toggle)")
    return {"wav_base64": base64.b64encode(wav_bytes).decode("ascii"),
            "duration_s": info["duration_s"],
            "sample_rate": info["sample_rate"],
            "channels": info["channels"],
            "peak": info["peak"], "rms": info["rms"],
            "near_silent": False,
            "backend": backend, "device": device_label}


def _require_audit_log():
    """MF-4: a capture that cannot be audited must not happen. Refuse
    when the event log is disabled or today's file is not writable."""
    if os.environ.get("EVENT_LOG_ENABLED", "1") == "0":
        raise RuntimeError(
            "record_audio refused: the event log is disabled "
            "(EVENT_LOG_ENABLED=0) — media capture requires an audit "
            "trail (privacy MF-4)")
    import events
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = events.events_dir() / f"{day}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as e:
        raise RuntimeError(
            f"record_audio refused: event log not writable ({path}): "
            f"{e} (privacy MF-4)")


def _check_rate_guard(now=None):
    """MF-12: minimum interval between captures, so repeated calls
    cannot become a de-facto stream."""
    now = time.time() if now is None else now
    wait = _RATE_GUARD_MIN_INTERVAL_S - (now - _last_capture_end[0])
    if _last_capture_end[0] and wait > 0:
        raise RuntimeError(
            f"record_audio refused: rate guard — last capture ended "
            f"{now - _last_capture_end[0]:.1f}s ago; minimum interval "
            f"is {_RATE_GUARD_MIN_INTERVAL_S:.0f}s (privacy MF-12)")


def _capture_consent(device_label, duration_s):
    """MF-3 + MF-8: explicit per-mode consent, then an audit event.

    Behavior by permission mode (approval.py):
    - default / acceptEdits: record_audio is always_ask and is NOT in
      ACCEPT_EDITS_AUTO, so _approved() shows the per-capture dialog;
      denial/timeout raises PermissionError before the mic opens.
    - PC_BRIDGE_AUTO_APPROVE=1: always_ask still dialogs (approval.py).
    - dontAsk / PC_BRIDGE_FULL_ACCESS=1: the dialog is skipped BY
      CARLOS'S OWN session-level choice (confirmed once at server
      startup, startup_confirm.py). The capture proceeds, and the
      MediaCaptureConsent event records consent="session_dontask"
      honestly — the bypass is never silently inherited.
    - plan: unreachable — toolcall short-circuits write tools first.
    """
    import approval as _approval_mod
    import events as _events
    mode = _approval_mod.get_permission_mode()
    consent = ("session_dontask"
               if (mode == "dontask" or _approval_mod.is_full_access())
               else "per_capture_dialog")
    _approved(
        "record_audio",
        f"Record {duration_s}s of microphone audio from "
        f"'{device_label}'.\nThe recorded audio will LEAVE this PC "
        "(returned to the remote agent).")
    _events.append("MediaCaptureConsent",
                   {"tool": "record_audio", "permission_mode": mode,
                    "consent": consent, "device": device_label,
                    "duration_s": duration_s})
    return mode, consent


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_char_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_void_p), ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p)]


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.DWORD),
                ("szPname", wintypes.WCHAR * 32),
                ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                ("wReserved1", wintypes.WORD)]


def _wavein_device_names():
    """Enumerate waveIn input device names (no handle opened)."""
    winmm = ctypes.windll.winmm
    names = []
    for i in range(winmm.waveInGetNumDevs()):
        caps = _WAVEINCAPSW()
        rc = winmm.waveInGetDevCapsW(i, ctypes.byref(caps),
                                     ctypes.sizeof(caps))
        if rc == 0:
            names.append(caps.szPname)
    return names


def _record_wavein(duration_s, device_idx):
    """Capture PCM16 mono via waveIn, polling WHDR_DONE with a hard
    deadline. Returns raw PCM bytes."""
    winmm = ctypes.windll.winmm
    k32 = ctypes.windll.kernel32
    fmt = _WAVEFORMATEX(1, _RECORD_CHANNELS, _RECORD_RATE,
                        _RECORD_RATE * _RECORD_CHANNELS * 2,
                        _RECORD_CHANNELS * 2, 16, 0)
    event = k32.CreateEventW(None, True, False, None)
    if not event:
        raise RuntimeError("record_audio: CreateEventW failed")
    hwi = ctypes.c_void_p()
    CALLBACK_EVENT = 0x00050000
    try:
        rc = winmm.waveInOpen(ctypes.byref(hwi), device_idx,
                              ctypes.byref(fmt), event, 0, CALLBACK_EVENT)
        _mm_check(rc, "waveInOpen")
        chunk_frames = _RECORD_RATE // 4
        n_chunks = duration_s * 4
        bufs, hdrs = [], []
        for _ in range(n_chunks):
            b = ctypes.create_string_buffer(chunk_frames * 2)
            h = _WAVEHDR(ctypes.cast(b, ctypes.c_char_p), chunk_frames * 2,
                         0, None, 0, 0, None, None)
            _mm_check(winmm.waveInPrepareHeader(
                hwi, ctypes.byref(h), ctypes.sizeof(h)),
                "waveInPrepareHeader")
            _mm_check(winmm.waveInAddBuffer(
                hwi, ctypes.byref(h), ctypes.sizeof(h)), "waveInAddBuffer")
            bufs.append(b)
            hdrs.append(h)
        _mm_check(winmm.waveInStart(hwi), "waveInStart")
        deadline = time.time() + duration_s + 5
        done = 0
        while done < n_chunks:
            if time.time() > deadline:
                raise RuntimeError(
                    "record_audio: waveIn capture timed out — device "
                    "busy or wedged (try restarting the Windows audio "
                    "service)")
            k32.WaitForSingleObject(event, 100)
            k32.ResetEvent(event)
            while done < n_chunks and (hdrs[done].dwFlags & 0x1):  # DONE
                done += 1
        pcm = b"".join(hdrs[i].lpData[:hdrs[i].dwBytesRecorded]
                       for i in range(n_chunks))
    finally:
        if hwi:
            winmm.waveInReset(hwi)
            winmm.waveInClose(hwi)
        k32.CloseHandle(event)
    return pcm[:_RECORD_RATE * duration_s * 2]


def _ffmpeg_audio_devices(ffmpeg):
    """Audio device friendly names from ffmpeg's dshow enumeration."""
    import re
    r = subprocess.run([ffmpeg, "-list_devices", "true", "-f", "dshow",
                        "-i", "dummy"], capture_output=True, text=True,
                       timeout=20)
    names = []
    for m in re.finditer(r'"([^"]+)"\s*\(audio\)', r.stderr or ""):
        names.append(m.group(1))
    return names


def _record_ffmpeg(duration_s, device, ffmpeg):
    """Capture via ffmpeg dshow. Returns (wav_bytes, device_name)."""
    names = _ffmpeg_audio_devices(ffmpeg)
    if device:
        idx = _match_device_index(names, device)
        if idx is None:
            raise RuntimeError(
                f"record_audio: no ffmpeg dshow audio device matching "
                f"{device!r}; enumerated: {names}")
        name = names[idx]
    elif names:
        name = names[0]
    else:
        raise RuntimeError(
            "record_audio: ffmpeg found no dshow audio devices")
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "dshow", "-i", f"audio={name}",
             "-t", str(duration_s), "-ar", str(_RECORD_RATE),
             "-ac", str(_RECORD_CHANNELS), path],
            capture_output=True, text=True, timeout=duration_s + 15)
        if r.returncode != 0:
            raise RuntimeError(
                f"record_audio: ffmpeg exited {r.returncode}: "
                f"{(r.stderr or '')[-300:]}")
        with open(path, "rb") as f:
            return f.read(), name
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _record_mci(duration_s):
    """LAST RESORT: MCI records the system default device only. The
    full WAV format parameter set is issued BEFORE recording (the
    documented MCI header bug writes a wrong bytes-per-second value
    otherwise). Returns wav bytes."""
    winmm = ctypes.windll.winmm
    ret = ctypes.create_unicode_buffer(256)

    def mci(cmd):
        rc = winmm.mciSendStringW(cmd, ret, 256, None)
        if rc != 0:
            buf = ctypes.create_unicode_buffer(256)
            winmm.mciGetErrorStringW(rc, buf, 256)
            raise RuntimeError(
                f"record_audio: MCI command {cmd.split()[0]!r} failed "
                f"({rc}): {buf.value}")

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        mci("open new type waveaudio alias alterego_rec")
        try:
            mci("set alterego_rec bitspersample 16 channels 1 "
                "samplespersec 44100 bytespersec 88200 alignment 2 "
                "time format milliseconds")
            mci("record alterego_rec")
            time.sleep(duration_s)
            mci("stop alterego_rec")
            mci(f'save alterego_rec "{path}"')
        finally:
            try:
                mci("close alterego_rec")
            except RuntimeError:
                pass
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def record_audio(duration_s: int = 5, device: str = None) -> dict:
    """Record microphone audio to a WAV clip. Always requires approval
    (always_ask). Fails loud: no backend, unknown device, denied
    approval, disabled audit log, or near-silent capture are all
    errors — never a fake or silent success."""
    duration_s, device = _validate_record_args(duration_s, device)
    _require_audit_log()          # MF-4: audit or refuse
    _check_rate_guard()           # MF-12
    backends = _available_backends(os.name == "nt", shutil.which("ffmpeg"))
    if not backends:
        raise RuntimeError(
            "record_audio: no audio capture backend available (need "
            "Windows winmm, or ffmpeg on PATH) — refusing to fake a "
            "recording")
    # Resolve the device label BEFORE consent, so the dialog can name
    # the actual device (MF-8) and an unknown device fails before any
    # dialog or mic handle (MF-7).
    if "wavein" in backends:
        names = _wavein_device_names()
        if not names and "ffmpeg" not in backends:
            raise RuntimeError(
                "record_audio: no input devices enumerated by waveIn "
                "(no microphone present)")
        if device:
            idx = _match_device_index(names, device)
            if idx is None and "ffmpeg" not in backends:
                raise RuntimeError(
                    f"record_audio: no input device matching "
                    f"{device!r}; enumerated: {names}")
            device_label = names[idx] if idx is not None else device
            wavein_idx = idx  # None -> ffmpeg may still match it
        else:
            device_label = "system default input device"
            wavein_idx = -1  # WAVE_MAPPER
    elif "ffmpeg" in backends:
        device_label = device or "ffmpeg dshow audio device"
        wavein_idx = None
    else:  # mci only
        device_label = "system default input device"
        wavein_idx = None
    _capture_consent(device_label, duration_s)   # MF-2/MF-3/MF-8 gate
    errors = []
    for backend in backends:
        try:
            if backend == "wavein":
                if wavein_idx is None:
                    raise RuntimeError(
                        f"device {device!r} not in waveIn enumeration")
                wav = _wav_from_pcm(_record_wavein(duration_s, wavein_idx))
                used_label = (device_label if wavein_idx >= 0
                              else "system default input device")
            elif backend == "ffmpeg":
                wav, used_label = _record_ffmpeg(
                    duration_s, device, shutil.which("ffmpeg"))
            else:  # mci — last resort, default device only
                if device:
                    continue  # cannot honor a device hint (MF-7)
                wav = _record_mci(duration_s)
                used_label = "system default input device (MCI)"
            result = _finalize_capture(wav, backend, used_label)
            _last_capture_end[0] = time.time()
            return result
        except Exception as e:  # try the next backend, loudly logged
            errors.append(f"{backend}: {e}")
    raise RuntimeError(
        "record_audio: all capture backends failed — "
        + "; ".join(errors))


def set_volume(level: int) -> dict:
    """Set master wave volume 0-100. Requires approval."""
    level = max(0, min(int(level), 100))
    v = int(level * 65535 / 100)
    fn = ctypes.windll.winmm.waveOutSetVolume
    fn.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    fn.restype = wintypes.UINT
    rc = fn(0, v | (v << 16))
    if rc != 0:
        raise OSError(f"waveOutSetVolume failed (mmresult={rc})")
    return {"volume": level}


def power(action: str) -> dict:
    """lock | sleep | restart | shutdown the PC. Requires approval."""
    actions = ("lock", "sleep", "restart", "shutdown")
    if action not in actions:
        raise ValueError(f"action must be one of {actions}")
    _approved("power", f"{action.upper()} this PC now.")
    if action == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif action == "sleep":
        ctypes.windll.powrprof.SetSuspendState(False, False, False)
    elif action == "restart":
        subprocess.run(["shutdown", "/r", "/t", "5"], capture_output=True)
    else:
        subprocess.run(["shutdown", "/s", "/t", "5"], capture_output=True)
    return {"power": action}
