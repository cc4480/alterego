"""Windows provider for macros (group: macros).

Thin delegation to the shared macros module, except macro_replay,
which approves once itself (always_ask tier) and mutes the batch
layer's own approval gate for the dispatch, so exactly one dialog
fires for the whole replay — the same single-approval shape batch
itself uses for its sub-calls.
"""
from approval import request_approval


def _approved(tool: str, summary: str) -> None:
    from tool_profiles import approval_tier
    if not request_approval(f"[{tool}]\n{summary}", tier=approval_tier(tool),
                            tool_name=tool):
        raise PermissionError("denied by local approval (or timed out)")


def macro_record(name, steps):
    """Record (or overwrite) a macro. Requires approval."""
    import macros
    return macros.record_macro(name, steps)


def macro_list():
    """Recorded macros, sorted by name. No approval."""
    import macros
    return macros.list_macros()


def macro_delete(name):
    """Delete a macro. Fails loud on unknown names. Requires approval."""
    import macros
    return macros.delete_macro(name)


def macro_replay(name):
    """Replay a macro through the batch layer. One approval covers all
    writes; the batch layer's own gate is muted for the dispatch."""
    import macros
    from seams import registry
    steps = macros.get_macro_steps(name)
    _approved("macro_replay",
              f"Replay macro {name!r} ({len(steps)} calls):\n"
              + "\n".join(f"- {s['tool']}" for s in steps[:12]))
    from seams.providers.windows import support as _support_mod
    batch_fn = registry.resolve("batch")
    original = _support_mod._approved
    _support_mod._approved = lambda tool, summary: None
    try:
        return macros.replay_macro(name, batch_fn)
    finally:
        _support_mod._approved = original
