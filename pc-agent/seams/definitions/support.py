"""Service Definitions for support tools (group: support)."""
from seams.definitions.base import ToolDef

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
)

ALL_SUPPORT_DEFS = [SHELL_PWSH, BATCH]
