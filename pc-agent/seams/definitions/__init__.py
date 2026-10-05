"""Aggregate of all Service Definitions."""
from seams.definitions.base import ToolDef
from seams.definitions.read import ALL_READ_DEFS
from seams.definitions.write import ALL_WRITE_DEFS
from seams.definitions.files import ALL_FILES_DEFS
from seams.definitions.pc import ALL_PC_DEFS
from seams.definitions.browser import ALL_BROWSER_DEFS
from seams.definitions.recon import ALL_RECON_DEFS
from seams.definitions.support import ALL_SUPPORT_DEFS
from seams.definitions.tasks import ALL_TASKS_DEFS
from seams.definitions.misc import ALL_MISC_DEFS

ALL_DEFS = (ALL_READ_DEFS + ALL_WRITE_DEFS + ALL_FILES_DEFS + ALL_PC_DEFS
            + ALL_BROWSER_DEFS + ALL_RECON_DEFS + ALL_SUPPORT_DEFS
            + ALL_TASKS_DEFS + ALL_MISC_DEFS)

_BY_GROUP = {}
for _d in ALL_DEFS:
    _BY_GROUP.setdefault(_d.group, []).append(_d)

_BY_NAME = {d.name: d for d in ALL_DEFS}
assert len(_BY_NAME) == len(ALL_DEFS), "duplicate tool definition names"


def for_group(group: str) -> list:
    """All definitions in a tool group. KeyError on unknown group."""
    return list(_BY_GROUP[group])


def by_name(name: str):
    """Definition for a tool name, or None."""
    return _BY_NAME.get(name)


__all__ = ["ToolDef", "ALL_DEFS", "for_group", "by_name"]
