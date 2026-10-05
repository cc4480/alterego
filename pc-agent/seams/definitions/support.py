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
        "Requires approval.",
    args={"script": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 60, "required": False}},
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
        "Returns a job_id for polling with exec_status or killing "
        "with exec_cancel. Requires approval.",
    args={"command": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 300, "required": False}},
    result_keys=["job_id", "status", "pid"],
    approval_tier="always_ask",
    side_effects="system",
    platform_notes="subprocess.Popen is cross-platform. Windows: runs via "
                   "cmd.exe. Timeout enforced by polling loop.",
)

EXEC_STATUS = ToolDef(
    name="exec_status",
    group="support",
    doc="Check on a background job started with exec_background.",
    args={"job_id": {"type": "str", "required": True}},
    result_keys=["job_id", "status", "returncode", "output_tail",
                 "output_full_path"],
    approval_tier="silent",
    side_effects="none",
    platform_notes="Pure bookkeeping over the in-memory job table. "
                   "Fully portable.",
)

EXEC_CANCEL = ToolDef(
    name="exec_cancel",
    group="support",
    doc="Kill a running background job started with exec_background.",
    args={"job_id": {"type": "str", "required": True}},
    result_keys=["job_id", "status"],
    approval_tier="routine",
    side_effects="system",
    platform_notes="Process termination is cross-platform "
                   "(Popen.kill). Fully portable.",
)

ALL_SUPPORT_DEFS = [SHELL_PWSH, BATCH, EXEC_BACKGROUND, EXEC_STATUS,
                    EXEC_CANCEL]
