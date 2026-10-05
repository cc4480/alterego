"""Service Definitions for file tools (group: files).

Writes need approval unless dry_run=True (plan posture runs dry).
"""
from seams.definitions.base import ToolDef

_DRY = {"dry_run": {"type": "bool", "default": False, "required": False}}

WRITE_FILE = ToolDef(
    name="write_file",
    group="files",
    doc="Create/overwrite a UTF-8 text file. Approval unless dry_run.",
    args={"path": {"type": "str", "required": True},
          "content": {"type": "str", "required": True}, **_DRY},
    result_keys=["path", "bytes_written", "dry_run", "would", "old_bytes",
                 "new_bytes", "diff"],
    approval_tier="always_ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

EDIT_FILE = ToolDef(
    name="edit_file",
    group="files",
    doc="Replace first old_text with new_text. Approval unless dry_run.",
    args={"path": {"type": "str", "required": True},
          "old_text": {"type": "str", "required": True},
          "new_text": {"type": "str", "required": True}, **_DRY},
    result_keys=["path", "replacements", "dry_run", "would", "diff"],
    approval_tier="ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

DELETE_FILE = ToolDef(
    name="delete_file",
    group="files",
    doc="Permanently delete a file. Approval unless dry_run.",
    args={"path": {"type": "str", "required": True}, **_DRY},
    result_keys=["path", "deleted", "dry_run", "would", "bytes"],
    approval_tier="always_ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

CREATE_DIR = ToolDef(
    name="create_dir",
    group="files",
    doc="Create a directory (parents too). Approval unless dry_run.",
    args={"path": {"type": "str", "required": True}, **_DRY},
    result_keys=["path", "created", "dry_run", "would"],
    approval_tier="ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

COPY_FILE = ToolDef(
    name="copy_file",
    group="files",
    doc="Copy a file (metadata preserved). Approval unless dry_run.",
    args={"src": {"type": "str", "required": True},
          "dst": {"type": "str", "required": True}, **_DRY},
    result_keys=["src", "dst", "bytes", "dry_run", "would", "overwrite"],
    approval_tier="ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

MOVE_FILE = ToolDef(
    name="move_file",
    group="files",
    doc="Move/rename a file. Approval unless dry_run.",
    args={"src": {"type": "str", "required": True},
          "dst": {"type": "str", "required": True}, **_DRY},
    result_keys=["src", "dst", "moved", "dry_run", "would", "bytes",
                 "overwrite"],
    approval_tier="ask",
    side_effects="local-fs",
    supports_dry_run=True,
)

FILE_INFO = ToolDef(
    name="file_info",
    group="files",
    doc="Size and timestamps for a file or directory (no approval).",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "is_file", "is_dir", "size", "modified", "created"],
    approval_tier="silent",
    side_effects="none",
)

ALL_FILES_DEFS = [WRITE_FILE, EDIT_FILE, DELETE_FILE, CREATE_DIR,
                  COPY_FILE, MOVE_FILE, FILE_INFO]
