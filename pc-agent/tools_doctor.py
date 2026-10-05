"""Thin re-export shim: implementation lives in
seams.providers.windows.misc. Kept so existing importers keep working.
"""
from seams.providers.windows.misc import (
    BRIDGE_PORT,
    CHECKS,
    MIN_DISK_GB,
    MIN_PYTHON,
    REPO_ROOT,
    WIN,
    check_defender_exclusions,
    check_disk_space,
    check_event_log,
    check_port_listener,
    check_python_version,
    check_startup_entry,
    check_tunnel_config,
    doctor,
)
