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

ALL_SUPPORT_DEFS = [SHELL_PWSH, BATCH]
