"""The Service Definition: machine-readable contract for one tool.

A definition must be complete enough to write a correct provider without
reading any provider's code: name, args, result shape, approval tier,
side-effect class, and dry-run support.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDef:
    name: str                      # pipeline tool name, e.g. "screenshot"
    group: str                     # tool group, e.g. "read"
    doc: str                       # human description (feeds the MCP schema)
    args: dict                     # {"path": {"type": "str", "required": True}}
    result_keys: list              # contract the provider's result dict honors
    approval_tier: str             # "silent" | "routine" | "ask" | "always_ask"
    side_effects: str              # "none" | "local-fs" | "session" | "system" | "network"
    supports_dry_run: bool = False

    def __post_init__(self):
        # Fail loud on malformed definitions: a bad contract is worse
        # than no contract.
        if self.approval_tier not in ("silent", "routine", "ask",
                                      "always_ask"):
            raise ValueError(f"bad approval_tier: {self.approval_tier!r}")
        if self.side_effects not in ("none", "local-fs", "session",
                                      "system", "network"):
            raise ValueError(f"bad side_effects: {self.side_effects!r}")
        for arg, spec in self.args.items():
            if not isinstance(spec, dict) or "type" not in spec \
                    or "required" not in spec:
                raise ValueError(
                    f"bad arg spec for {self.name}.{arg}: {spec!r}")
