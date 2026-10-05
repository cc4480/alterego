"""Windows provider for miscellaneous tools (group: misc).

- memory_recall: keyword search over the on-PC memory archive.
  Layout under %APPDATA%\\pc-mcp-bridge\\memory\\ (override:
  PC_BRIDGE_MEMORY_DIR).
- doctor: bridge health checks with specific fix commands. Each check
  returns ok/warning/fail + a fix command. Windows-only checks degrade
  gracefully on other platforms (warning, not fail).
- arbitrate: re-exported from the top-level arbitrate module (pure
  risk-scoring logic; no platform coupling).

Bodies moved verbatim from tools_memory.py / tools_doctor.py.
"""
import os
import re
import shutil
import socket
import sys
from pathlib import Path

from arbitrate import arbitrate  # the "arbitrate" tool implementation

# tools_doctor.py lived at pc-agent/ (repo root = parent.parent).
# This module lives at pc-agent/seams/providers/windows/ — three levels
# deeper — so climb five to reach the same repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


# ---- memory_recall (from tools_memory.py) ---------------------------------

def _memory_dir() -> Path:
    override = os.environ.get("PC_BRIDGE_MEMORY_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "pc-mcp-bridge" / "memory"


def _score(text: str, terms: list) -> tuple:
    low = text.lower()
    term_hits = sum(low.count(t) for t in terms)
    heading_hits = sum(
        1 for line in text.splitlines() if line.startswith("#")
        for t in terms if t in line.lower()
    )
    return heading_hits, term_hits


def memory_recall(query: str, limit: int = 5) -> dict:
    """Keyword search over the memory archive. No approval (read-only)."""
    if not query or not str(query).strip():
        raise ValueError("query must be a non-empty string")
    limit = max(1, min(int(limit), 20))
    terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1]
    if not terms:
        raise ValueError("query has no searchable terms")
    root = _memory_dir()
    if not root.is_dir():
        return {"query": query, "results": [],
                "note": f"memory archive not found at {root}"}
    files = sorted(root.rglob("*.md"))
    scored = []
    for md in files:
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hh, th = _score(text, terms)
        if th == 0:
            continue
        low = text.lower()
        idx = min(low.find(t) for t in terms if t in low)
        snippet = text[max(0, idx - 300):idx + 400].strip()
        scored.append((hh, th, str(md.relative_to(root)), snippet))
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    results = [{"file": f, "subject_hits": hh, "term_hits": th, "snippet": s}
               for hh, th, f, s in scored[:limit]]
    return {"query": query, "results": results,
            "files_searched": len(files), "files_matched": len(scored)}


# ---- doctor (from tools_doctor.py) ----------------------------------------

MIN_PYTHON = (3, 11)
MIN_DISK_GB = 1.0
BRIDGE_PORT = 8765

WIN = os.name == "nt"


def _ok(name, message):
    return {"check": name, "status": "ok", "message": message, "fix": None}


def _warn(name, message, fix=None):
    return {"check": name, "status": "warning", "message": message,
            "fix": fix}


def _fail(name, message, fix=None):
    return {"check": name, "status": "fail", "message": message, "fix": fix}


def check_python_version():
    v = sys.version_info
    if v >= MIN_PYTHON:
        return _ok("python_version",
                   f"Python {v.major}.{v.minor}.{v.micro} "
                   f"(minimum {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")
    return _fail("python_version",
                 f"Python {v.major}.{v.minor} below minimum "
                 f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
                 "Install Python 3.11+ from python.org or via "
                 "winget install Python.Python.3.12")


def check_port_listener():
    """Port 8765 should be held by this server, not a stranger."""
    try:
        import psutil
    except ImportError:
        return _warn("port_listener", "psutil not installed, cannot verify",
                     "pip install psutil")
    holders = []
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr and conn.laddr.port == BRIDGE_PORT \
                and conn.status == "LISTEN":
            try:
                holders.append(psutil.Process(conn.pid).name())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                holders.append(f"pid={conn.pid}")
    if not holders:
        return _fail("port_listener",
                     f"nothing listening on {BRIDGE_PORT} — server not running",
                     "Start the server: python pc-agent\\server.py "
                     "(from the repo root)")
    me = os.path.basename(sys.executable)
    if any(h.lower().startswith(me.lower().split(".")[0]) for h in holders):
        return _ok("port_listener",
                   f"port {BRIDGE_PORT} held by {', '.join(holders)}")
    return _warn("port_listener",
                 f"port {BRIDGE_PORT} held by unexpected process: "
                 f"{', '.join(holders)}",
                 f"netstat -ano | findstr :{BRIDGE_PORT}  then "
                 "taskkill /PID <pid> /F if it is a stale server")


def check_defender_exclusions():
    """Defender must exclude the bridge dir or it may quarantine tools."""
    if not WIN:
        return _warn("defender_exclusions",
                     "not Windows: Defender check not applicable")
    repo = REPO_ROOT
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-MpPreference).ExclusionPath -join \"`n\""],
            capture_output=True, text=True, timeout=30)
        excluded = out.stdout.lower()
        if str(repo).lower() in excluded:
            return _ok("defender_exclusions",
                       f"Defender excludes {repo}")
        return _fail("defender_exclusions",
                     f"Defender does NOT exclude {repo}",
                     f'Add-MpPreference -ExclusionPath "{repo}"  '
                     "(run PowerShell as Administrator)")
    except Exception as e:  # noqa: BLE001 - report, don't crash
        return _warn("defender_exclusions",
                     f"could not query Defender exclusions: {e}",
                     "Run PowerShell as Administrator and check "
                     "(Get-MpPreference).ExclusionPath")


def check_startup_entry():
    """The Startup .bat must exist so the server survives reboot."""
    if not WIN:
        return _warn("startup_entry",
                     "not Windows: Startup folder check not applicable")
    startup = Path(os.environ.get(
        "APPDATA", os.path.expanduser("~"))) / "Microsoft" / "Windows" \
        / "Start Menu" / "Programs" / "Startup" / "start-bridge.bat"
    if not startup.exists():
        return _fail("startup_entry",
                     f"missing {startup}",
                     "Recreate it: cd to the repo and run the setup "
                     "instructions in README (Startup section)")
    text = startup.read_text(encoding="utf-8", errors="replace")
    if "server.py" not in text:
        return _fail("startup_entry",
                     f"{startup} exists but does not launch server.py",
                     "Fix the .bat to run: python pc-agent\\server.py "
                     "from the repo root")
    return _ok("startup_entry", f"{startup.name} present and valid")


def check_tunnel_config():
    """Cloudflare tunnel config must exist for the public endpoint."""
    if not WIN:
        return _warn("tunnel_config",
                     "not Windows: cloudflared config check not applicable")
    cfg = Path(os.path.expanduser("~")) / ".cloudflared" / "config.yml"
    if not cfg.exists():
        return _fail("tunnel_config", f"missing {cfg}",
                     "Re-run: cloudflared tunnel login, then "
                     "cloudflared tunnel create pc-bridge, then route "
                     "pc.secscan.info and install the service")
    text = cfg.read_text(encoding="utf-8", errors="replace")
    if "8765" not in text:
        return _warn("tunnel_config",
                     f"{cfg} exists but does not reference port 8765",
                     "Check the ingress rules in config.yml point at "
                     "http://127.0.0.1:8765")
    return _ok("tunnel_config", f"{cfg.name} present, routes to 8765")


def check_disk_space():
    drive = "C:\\" if WIN else "/"
    try:
        free_gb = shutil.disk_usage(drive).free / (1024 ** 3)
    except OSError as e:
        return _warn("disk_space", f"could not measure {drive}: {e}")
    if free_gb >= MIN_DISK_GB:
        return _ok("disk_space", f"{free_gb:.1f} GB free on {drive}")
    return _fail("disk_space",
                 f"only {free_gb:.1f} GB free on {drive} "
                 f"(minimum {MIN_DISK_GB:.0f} GB)",
                 "Free disk space: empty Recycle Bin, run Disk Cleanup")


CHECKS = [check_python_version, check_port_listener,
          check_defender_exclusions, check_startup_entry,
          check_tunnel_config, check_disk_space]


def doctor() -> dict:
    """Run all health checks. Returns per-check results plus a summary."""
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as e:  # noqa: BLE001 - a check must never crash doctor
            results.append(_fail(check.__name__, f"check crashed: {e}"))
    counts = {"ok": 0, "warning": 0, "fail": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = ("all checks passed" if counts["fail"] == 0
               and counts["warning"] == 0 else
               f"{counts['fail']} failed, {counts['warning']} warnings")
    return {"checks": results, "summary": summary, "counts": counts}
