"""Service Definitions for file tools (group: files).

Writes need approval unless dry_run=True (plan posture runs dry).
"""
from seams.definitions.base import ToolDef

_DRY = {"dry_run": {"type": "bool", "default": False, "required": False}}
_STALE = {"expected_sha256": {"type": "str", "default": None,
                              "required": False}}

# All file tools are pathlib-based: fully portable. Windows drive-letter
# roots vs POSIX roots are a provider detail, not a gap. dry_run shapes
# are per-branch (see each provider); result_keys is the branch union.
_PORTABLE = ("Fully portable: pathlib + os/shutil. No known gaps; drive "
             "roots and permission models are provider details.")

WRITE_FILE = ToolDef(
    name="write_file",
    group="files",
    doc="Create/overwrite a UTF-8 text file. Approval unless dry_run. "
        "expected_sha256 (from a read) fails the write if the file "
        "changed since it was read.",
    args={"path": {"type": "str", "required": True},
          "content": {"type": "str", "required": True},
          **_DRY, **_STALE},
    result_keys=["path", "bytes_written", "dry_run", "would", "old_bytes",
                 "new_bytes", "diff"],
    approval_tier="always_ask",
    side_effects="local-fs",
    supports_dry_run=True,
    platform_notes=_PORTABLE,
)

EDIT_FILE = ToolDef(
    name="edit_file",
    group="files",
    doc="Replace old_text with new_text. Fails on multiple matches unless "
        "require_unique=False. Always returns a diff preview. "
        "expected_sha256 (from a read) fails the edit if the file "
        "changed since it was read. Approval unless dry_run.",
    args={"path": {"type": "str", "required": True},
          "old_text": {"type": "str", "required": True},
          "new_text": {"type": "str", "required": True},
          "require_unique": {"type": "bool", "required": False,
                             "default": True},
          "read_before": {"type": "bool", "required": False,
                          "default": True}, **_DRY, **_STALE},
    result_keys=["path", "replacements", "matches_found", "dry_run", "would",
                 "diff", "before", "after"],
    approval_tier="ask",
    side_effects="local-fs",
    supports_dry_run=True,
    platform_notes=_PORTABLE,
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
    platform_notes=_PORTABLE,
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
    platform_notes=_PORTABLE,
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
    platform_notes=_PORTABLE,
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
    platform_notes=_PORTABLE,
)

FILE_INFO = ToolDef(
    name="file_info",
    group="files",
    doc="Size and timestamps for a file or directory (no approval).",
    args={"path": {"type": "str", "required": True}},
    result_keys=["path", "is_file", "is_dir", "size", "modified", "created"],
    approval_tier="silent",
    side_effects="none",
    platform_notes=_PORTABLE,
)

ALL_FILES_DEFS = [WRITE_FILE, EDIT_FILE, DELETE_FILE, CREATE_DIR,
                  COPY_FILE, MOVE_FILE, FILE_INFO]

# ---- Batch 6 (portmanteau slice) ----------------------------------------
FILE = ToolDef(
    name="file",
    group="files",
    doc="Files as one tool: operation read | read_range | write | edit | "
        "delete | list | copy | move | create_dir | info | search | "
        "search_names, args are the member tool's args. Write operations "
        "honor dry_run in args, like the member tools.",
    args={"operation": {"type": "str", "required": True},
          "args": {"type": "dict", "required": False, "default": None}},
    result_keys=["operation", "tool", "result"],
    approval_tier="always_ask",
    side_effects="local-fs",
    supports_dry_run=True,
    platform_notes="Dispatches to read_file / read_file_range / write_file "
                   "/ edit_file / delete_file / list_dir / copy_file / "
                   "move_file / create_dir / file_info / search_files / "
                   "search_filenames; same platform story as the members.",
)

ALL_FILES_DEFS.extend([FILE])
