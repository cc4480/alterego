"""Maps tool names to provider functions. Selected by PC_BRIDGE_PROVIDER.

Resolution is lazy: provider modules are imported on the first resolve()
call, then cached for the life of the process. No hot-swapping providers
mid-run — that would be a correctness hazard with in-flight approvals.

Fail loud (never silently degrade):
- PC_BRIDGE_PROVIDER set to an unknown value -> RuntimeError.
- A tool the active provider does not implement -> RuntimeError.
"""
import os

from seams import definitions

_GROUPS = ("read", "write", "files", "pc", "browser",
           "recon", "support", "tasks", "misc")

_VALID_PROVIDERS = ("windows", "linux", "mock")

# MCP tool name -> pipeline tool name, for the one historical quirk where
# they differ: the MCP surface registers `arbitrate_tool`, but the
# pipeline, risk profiles, and audit log know it as `arbitrate`.
_ALIASES = {"arbitrate_tool": "arbitrate"}

_PROVIDERS = {}   # tool name -> implementation function
_ACTIVE = None


def _load_provider(kind: str):
    """Import seams.providers.<kind>.* and register every tool function."""
    global _ACTIVE
    for group in _GROUPS:
        try:
            mod = __import__(f"seams.providers.{kind}.{group}",
                             fromlist=["*"])
        except ImportError:
            continue  # provider does not implement this group (honest gap)
        tool_map = getattr(mod, "TOOL_MAP", None)
        for defn in definitions.for_group(group):
            if tool_map is not None:
                fn = tool_map.get(defn.name)
            else:
                fn = getattr(mod, defn.name, None)
            if fn is not None:
                _PROVIDERS[defn.name] = fn
    _ACTIVE = kind


def _mock_mode() -> str:
    """PC_BRIDGE_MOCK_MODE: 'strict' (default) or 'lenient'. Fail loud on
    anything else — never silently pick a behavior."""
    mode = os.environ.get("PC_BRIDGE_MOCK_MODE", "strict").strip().lower()
    if mode not in ("strict", "lenient"):
        raise RuntimeError(
            f"PC_BRIDGE_MOCK_MODE={mode!r} is invalid; "
            "expected 'strict' or 'lenient'")
    return mode


def _lenient_fn(name: str):
    """Generic mock result for tools the mock provider does not define.
    Only reachable when PC_BRIDGE_MOCK_MODE=lenient."""
    def fn(**kwargs):
        return {"mock": True, "tool": name, "args": kwargs}
    fn.__name__ = name
    return fn


def resolve(name: str):
    """Return the active provider's implementation for tool `name`."""
    if _ACTIVE is None:
        kind = os.environ.get("PC_BRIDGE_PROVIDER", "windows")
        if kind not in _VALID_PROVIDERS:
            raise RuntimeError(
                f"PC_BRIDGE_PROVIDER={kind!r} is invalid; "
                f"expected one of {list(_VALID_PROVIDERS)}")
        _load_provider(kind)
    fn = _PROVIDERS.get(_ALIASES.get(name, name))
    if fn is None:
        if _ACTIVE == "mock" and _mock_mode() == "lenient":
            return _lenient_fn(name)
        raise RuntimeError(
            f"tool {name!r} is not implemented by the "
            f"{_ACTIVE!r} provider")
    return fn


def active_provider() -> str:
    """Name of the loaded provider (or the configured-but-unloaded one)."""
    if _ACTIVE is None:
        return os.environ.get("PC_BRIDGE_PROVIDER", "windows")
    return _ACTIVE
