"""Service Definitions for support tools (group: support)."""
from seams.definitions.base import ToolDef

# Branch shapes (provider returns one or the other, never both):
#   success -> {returncode, stdout, stderr}
#   timeout -> {timed_out, stdout, stderr}
# result_keys is the union of the branches.
SHELL_PWSH = ToolDef(
    name="shell_pwsh",
    group="support",
    doc="Run a PowerShell script directly (no cmd.exe wrapping). "
        "Optional cwd (must be inside the user profile). "
        "Requires approval.",
    args={"script": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 60, "required": False},
          "cwd": {"type": "str", "required": False,
                  "default": None}},
    result_keys=["timed_out", "stdout", "stderr", "returncode"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="PowerShell is cross-platform via `pwsh` — but only if "
                   "installed. The Windows provider shells out to "
                   "powershell.exe. A Linux provider maps to `pwsh` when "
                   "present; when absent this tool is unimplemented "
                   "(honest gap — never fake it with bash).",
)

BATCH = ToolDef(
    name="batch",
    group="support",
    doc="Run up to 20 tool calls in one roundtrip. One approval covers "
        "all writes.",
    args={"calls": {"type": "list", "required": True}},
    result_keys=["calls", "results"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="Pure orchestration over the provider registry: fully "
                   "portable, zero platform coupling. Muting sub-call "
                   "approvals for the batch duration is a pipeline "
                   "concern, not a platform one.",
)

EXEC_BACKGROUND = ToolDef(
    name="exec_background",
    group="support",
    doc="Start a long-running shell command in the background. "
        "Optional cwd (must be inside the user profile). "
        "Returns a job_id for polling with exec_status or killing "
        "with exec_cancel. Requires approval.",
    args={"command": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 300, "required": False},
          "cwd": {"type": "str", "required": False,
                  "default": None}},
    result_keys=["job_id", "status", "pid"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="subprocess.Popen is cross-platform. Windows: runs via "
                   "cmd.exe. Timeout enforced by polling loop.",
)

EXEC_STATUS = ToolDef(
    name="exec_status",
    group="support",
    doc="Check on a background job started with exec_background. "
        "offset (bytes, default 0) enables incremental polling: "
        "output_from_offset holds bytes from offset onward, output_size "
        "the total bytes — pass it back as the next offset.",
    args={"job_id": {"type": "str", "required": True},
          "offset": {"type": "int", "default": 0, "required": False}},
    result_keys=["job_id", "status", "returncode", "output_tail",
                 "output_full_path", "output_from_offset", "output_size"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure bookkeeping over the in-memory job table. "
                   "Fully portable.",
)

EXEC_CANCEL = ToolDef(
    name="exec_cancel",
    group="support",
    doc="Kill a running background job started with exec_background, "
        "including its whole process tree.",
    args={"job_id": {"type": "str", "required": True}},
    result_keys=["job_id", "status"],
    approval_tier="routine",
    side_effects="system",
    platform_notes="Windows: taskkill /T /F kills the entire child tree "
                   "(Popen.kill alone leaves orphans). Falls back to "
                   "Popen.kill when taskkill fails.",
)

SERVER_RELOAD = ToolDef(
    name="server_reload",
    group="support",
    doc="Restart the bridge server through the supervisor (fresh code, "
        "same tunnel, same session tokens). Refuses to act when no "
        "supervisor is running, so the bridge is never darkened with "
        "nobody to bring it back.",
    args={},
    result_keys=["reloading", "supervisor_running", "reason", "error",
                 "fix"],
    approval_tier="ask",
    side_effects="system",
    platform_notes="Windows: the supervisor restarts python "
                   "pc-agent/server.py and reaps port zombies first. "
                   "No supervisor, no restart.",
)

SUPERVISOR_STATUS = ToolDef(
    name="supervisor_status",
    group="support",
    doc="Supervisor health: is it running, child pid, restarts, "
        "reloads, heartbeat age, storm-breaker state. Read-only.",
    args={},
    result_keys=["running", "present", "supervisor_pid", "child_pid",
                 "restarts", "reloads", "last_exit_code", "last_exit_ts",
                 "storm_breaker", "heartbeat_age_s", "updated_ts",
                 "port", "detail"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Reads the supervisor.json status file in the "
                   "bridge data dir. Never raises.",
)

HOT_RELOAD = ToolDef(
    name="hot_reload",
    group="support",
    doc="Hot reload control: status | enable | disable | trigger. "
        "When enabled, a debounced change to bridge code restarts the "
        "server through the supervisor. trigger restarts immediately.",
    args={"action": {"type": "str", "required": True}},
    result_keys=["action", "enabled", "watching", "last_change",
                 "reloading", "error", "fix"],
    approval_tier="routine",
    side_effects="session",
    platform_notes="Enable/disable/trigger change server behaviour and "
                   "need approval outside dontAsk mode; status is "
                   "read-only. The watcher itself runs in the "
                   "supervisor process.",
)

ALL_SUPPORT_DEFS = [SHELL_PWSH, BATCH, EXEC_BACKGROUND, EXEC_STATUS,
                    EXEC_CANCEL, SERVER_RELOAD, SUPERVISOR_STATUS,
                    HOT_RELOAD]
