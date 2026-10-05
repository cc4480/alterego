"""Thin re-export shim: implementation lives in
seams.providers.windows.tasks. Kept so existing importers keep working.

Exports both the MCP wrappers (task_create, ...) registered in
tool_wrappers.ALL_TOOLS and the underlying implementations
(create, checkpoint, status).
"""
from seams.providers.windows.tasks import (
    TASK_DIR,
    _append,
    _key,
    _read,
    _sign,
    _task_file,
    _verify,
    checkpoint,
    create,
    status,
    task_checkpoint,
    task_create,
    task_status,
)
