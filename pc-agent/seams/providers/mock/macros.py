"""Mock macros: thin delegation to the shared macros module.

No approval gate on the mock (like mock batch): replay resolves the
active provider's batch and runs the steps live, so mock replays
exercise the same fresh-read behavior as Windows ones.
"""
from seams.providers.mock import record


def macro_record(name, steps):
    import macros
    record("macro_record", {"name": name})
    return macros.record_macro(name, steps)


def macro_list():
    import macros
    record("macro_list", {})
    return macros.list_macros()


def macro_delete(name):
    import macros
    record("macro_delete", {"name": name})
    return macros.delete_macro(name)


def macro_replay(name):
    from seams import registry
    import macros
    record("macro_replay", {"name": name})
    return macros.replay_macro(name, registry.resolve("batch"))
