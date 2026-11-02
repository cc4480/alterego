"""Stack-safety tests for approval dialogs (2026-11-02 fix).

The bug: concurrent tool calls each opened their own MessageBoxW, and
the timeout path closed "the" dialog by a title shared by all of them —
unanswered dialogs stacked a dozen deep on the PC desktop.

The fix under test:
- dialogs are serialized (never two windows at once),
- a request identical to one that just timed out unanswered is denied
  from cache without opening a new window,
- human decisions (Yes or No) are never cached — they re-show.

The Win32 layer (_show_dialog_once) is stubbed; these tests pin the
policy around it. conftest pins dontAsk mode, so each test flips the
module back to default mode explicitly.
"""
import threading
import time

import pytest

import approval


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "default")
    monkeypatch.setattr(approval, "AUTO_APPROVE", False)
    monkeypatch.setattr(approval, "FULL_ACCESS", False)
    approval._RECENT_TIMEOUTS.clear()
    yield
    approval._RECENT_TIMEOUTS.clear()


def stub_show(monkeypatch, outcomes):
    """Replace _show_dialog_once with a fake returning queued
    (approved, timed_out) outcomes; returns the list of calls made."""
    calls = []

    def fake(summary, timeout_s, destructive):
        calls.append((summary, destructive))
        return outcomes[min(len(calls) - 1, len(outcomes) - 1)]

    monkeypatch.setattr(approval, "_show_dialog_once", fake)
    return calls


def test_timeout_denies_and_identical_retry_opens_no_new_window(
        monkeypatch):
    calls = stub_show(monkeypatch, [(False, True)])
    assert approval.request_approval("run X") is False
    assert approval.request_approval("run X") is False
    assert len(calls) == 1  # second request denied from cache


def test_different_request_still_shows_after_timeout(monkeypatch):
    calls = stub_show(monkeypatch, [(False, True), (True, False)])
    assert approval.request_approval("run X") is False
    assert approval.request_approval("run Y") is True
    assert len(calls) == 2


def test_yes_decision_is_never_cached(monkeypatch):
    calls = stub_show(monkeypatch, [(True, False)])
    assert approval.request_approval("run X") is True
    assert approval.request_approval("run X") is True
    assert len(calls) == 2


def test_explicit_no_is_never_cached(monkeypatch):
    calls = stub_show(monkeypatch, [(False, False)])
    assert approval.request_approval("run X") is False
    assert approval.request_approval("run X") is False
    assert len(calls) == 2


def test_timeout_cache_expires(monkeypatch):
    calls = stub_show(monkeypatch, [(False, True)])
    assert approval.request_approval("run X") is False
    # Age the cache entry past the dedupe window.
    approval._RECENT_TIMEOUTS["run X"] = (
        time.monotonic() - approval._TIMEOUT_DEDUPE_S - 1)
    assert approval.request_approval("run X") is False
    assert len(calls) == 2


def test_destructive_tier_uses_same_serialized_path(monkeypatch):
    calls = stub_show(monkeypatch, [(False, True)])
    assert approval.request_approval(
        "run X", tier="always_ask", tool_name="shell_pwsh") is False
    assert approval.request_approval(
        "run X", tier="always_ask", tool_name="shell_pwsh") is False
    assert len(calls) == 1
    assert calls[0][1] is True  # destructive flag reached the dialog


def test_override_to_default_revokes_legacy_full_access(monkeypatch):
    # PC_BRIDGE_FULL_ACCESS=1 at import freezes FULL_ACCESS=True and
    # resolves the mode to dontask. The declined-confirmation fallback
    # (server.py) overrides the mode to "default"; that must actually
    # revoke full access, not leave is_full_access() True via the
    # frozen global.
    monkeypatch.setattr(approval, "FULL_ACCESS", True)
    monkeypatch.setattr(approval, "PERMISSION_MODE", "dontask")
    assert approval.is_full_access() is True
    approval.override_permission_mode("default")
    assert approval.get_permission_mode() == "default"
    assert approval.is_full_access() is False


def test_override_to_other_modes_keeps_full_access(monkeypatch):
    monkeypatch.setattr(approval, "FULL_ACCESS", True)
    monkeypatch.setattr(approval, "PERMISSION_MODE", "dontask")
    approval.override_permission_mode("plan")
    assert approval.get_permission_mode() == "plan"
    assert approval.is_full_access() is True


def test_dialogs_never_overlap(monkeypatch):
    active = 0
    max_active = 0
    guard = threading.Lock()

    def fake(summary, timeout_s, destructive):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return True, False

    monkeypatch.setattr(approval, "_show_dialog_once", fake)
    threads = [threading.Thread(
        target=lambda i=i: approval.request_approval(f"run {i}"))
        for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert max_active == 1
