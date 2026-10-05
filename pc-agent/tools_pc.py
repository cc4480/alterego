"""PC awareness and control: what's happening, signaling the user, taking control.

Read-only (no approval): active_window, idle_seconds, list_processes.
Write (approval dialog): notify, speak, set_volume, power.
"""
import ctypes
import subprocess
from ctypes import wintypes

from approval import request_approval


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool)):
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
