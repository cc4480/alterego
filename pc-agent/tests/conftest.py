"""Shared fixtures for the mock-provider pipeline suite.

Env is pinned BEFORE any pc-agent import (approval.py reads its env at
import time). APPDATA points at a per-test tmp dir so events never touch
the real machine. The mock provider + hooks are reset for every test.
"""
import json
import os
import sys
from pathlib import Path

# ---- env, before pc-agent imports ---------------------------------------
os.environ.setdefault("PC_BRIDGE_PROVIDER", "mock")
os.environ.setdefault("PC_BRIDGE_PERMISSION_MODE", "dontAsk")
os.environ.setdefault("PC_BRIDGE_MOCK_MODE", "strict")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import approval  # noqa: E402
import hooks  # noqa: E402
from seams.providers import mock as mock_state  # noqa: E402

assert approval.get_permission_mode() == "dontask", \
    "tests require PC_BRIDGE_PERMISSION_MODE=dontAsk"


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path, monkeypatch):
    """Events go to a per-test tmp dir (never the real machine)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@pytest.fixture(autouse=True)
def _fresh_mock():
    """Reset mock FS/procs/journal/tasks, then seed shared fixtures."""
    mock_state.reset()
    from seams.providers.mock import files as mock_files
    from seams.providers.mock import tasks as mock_tasks
    mock_files.write_file("seed.txt", "seed content seed")
    mock_files.write_file("todelete.txt", "delete me")
    mock_files.write_file("tomove.txt", "move me")
    mock_state.SEED_TASK = mock_tasks.task_create(
        "seed goal", ["step one", "step two"])["task_id"]
    yield
    mock_state.reset()


@pytest.fixture()
def _clean_hooks():
    """Save/clear/restore the global hook registry around a test."""
    saved = {k: list(v) for k, v in hooks._hooks.items()}
    for lst in hooks._hooks.values():
        lst.clear()
    yield
    for k, v in saved.items():
        hooks._hooks[k] = v


def read_events():
    """All event envelopes written during this test (UTC-day jsonl)."""
    from datetime import datetime, timezone
    import events
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = events.events_dir() / f"{day}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def events_for(tool, events):
    """Envelopes for one tool call, ordered by write time."""
    return [e for e in events if e["data"].get("tool") == tool]
