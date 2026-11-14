"""Admission gate + dialog-queue bounds (2026-11-14 hang fix).

The wedge being fixed: unbounded invisible queues — sync tool calls
piling onto a 40-thread pool, and shell calls piling behind a single
approval-dialog lock — each presenting as a multi-minute silent hang
while /health stayed green. These tests pin the new behavior: bounded
waits, fast explicit failures, live counters.
"""
import threading
import time

import admission
import approval


def test_slot_admits_and_counts():
    before = admission.stats()
    with admission.slot("echo"):
        mid = admission.stats()
        assert mid["in_flight"] == before["in_flight"] + 1
    after = admission.stats()
    assert after["in_flight"] == before["in_flight"]
    assert after["completed"] == before["completed"] + 1


def test_slot_fails_fast_when_saturated(monkeypatch):
    monkeypatch.setattr(admission, "_gate", threading.Semaphore(1))
    monkeypatch.setattr(admission, "MAX_INFLIGHT", 1)
    monkeypatch.setattr(admission, "QUEUE_WAIT_S", 0.2)
    with admission.slot("first"):
        t0 = time.monotonic()
        try:
            with admission.slot("second"):
                raise AssertionError("second slot must not be granted")
        except admission.BusyError:
            pass
        assert time.monotonic() - t0 < 5  # failed fast, did not hang
    assert admission.stats()["busy_rejected"] >= 1


def test_dialog_lock_busy_denies_fast(monkeypatch):
    monkeypatch.setattr(approval, "_DIALOG_QUEUE_S", 0.2)
    approval._DIALOG_LOCK.acquire()
    try:
        t0 = time.monotonic()
        approved, timed_out, shown = approval._show_dialog(
            "test summary", 1, False)
        assert time.monotonic() - t0 < 5
        assert (approved, timed_out, shown) == (False, False, False)
    finally:
        approval._DIALOG_LOCK.release()
    assert approval.dialog_stats()["busy_denied"] >= 1
