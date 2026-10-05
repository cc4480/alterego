"""Mock support tools: shell_pwsh (canned) and batch (in-process fan-out).

batch() executes sub-tools by resolving the active provider's function
directly (registry.resolve) — NOT through toolcall.call — so a batch does
not nest pipeline events. Sub-tool exceptions become {"tool","error"}
results, mirroring the Windows provider's behavior.
"""
from seams.providers.mock import record


def shell_pwsh(script: str, timeout_s: int = 60) -> dict:
    """Mock PowerShell: never executes anything. `echo <text>` returns the
    text; anything else returns canned output."""
    record("shell_pwsh", {"script": script, "timeout_s": timeout_s})
    stripped = script.strip()
    if stripped == "echo":
        stdout = ""
    elif stripped.startswith("echo "):
        stdout = stripped[5:]
    else:
        stdout = "mock pwsh output"
    return {"timed_out": False, "stdout": stdout, "stderr": "",
            "returncode": 0}


def batch(calls: list) -> dict:
    if not isinstance(calls, list) or not 1 <= len(calls) <= 20:
        raise ValueError("calls must be a list of 1-20 {tool, args} items")
    from seams import registry  # lazy: avoids import cycle
    plan = []
    for c in calls:
        if not isinstance(c, dict) or not isinstance(c.get("tool"), str):
            raise ValueError("each call must look like {tool, args}")
        name = c["tool"]
        if name == "batch":
            raise ValueError("nested batch is not allowed")
        args = c.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        plan.append((name, args))
    record("batch", {"calls": [n for n, _ in plan]})
    results = []
    for name, args in plan:
        # Unknown tool -> per-call error envelope (mirrors the Windows
        # provider); only malformed plans raise.
        try:
            fn = registry.resolve(name)
        except Exception as e:  # noqa: BLE001 - unknown tool -> error result
            results.append({"tool": name,
                            "error": f"unknown tool: {name}"})
            continue
        try:
            results.append({"tool": name, "result": fn(**args)})
        except Exception as e:  # noqa: BLE001 - per-call error envelope
            results.append({"tool": name,
                            "error": f"{type(e).__name__}: {e}"})
    return {"calls": len(plan), "results": results}
