"""Integration tests for the merged senses build (camera_snapshot +
record_audio on the jobs branch) — the MERGE-MAP §5 items that only
exist once both slices and the jobs layer share one tree:

- both ops' effective profiles compute to always_ask (tier decision)
- mock-strict registry resolution for every definition
- exact result-keys for both new ops through the pipeline
- consent/approval event chains for both ops
- RESOURCE_MAP desktop serialization for the new ops
- >2048-byte job-result blob round-trip for both ops, with the MF-5
  senses registration + watchdog purge cadence wired in jobs.py
"""
import threading
import time

import pytest

import blob_store
import jobs
import tool_profiles
import toolcall
from conftest import read_events
from seams import definitions, registry

SENSES = ("camera_snapshot", "record_audio")
ARGS = {"camera_snapshot": {}, "record_audio": {"duration_s": 2,
                                                 "device": None}}


# ---------------------------------------------------------------- tiers

def test_both_senses_profiles_compute_always_ask():
    for name in SENSES:
        assert tool_profiles.approval_tier(name) == "always_ask", name
        assert definitions.by_name(name).approval_tier == "always_ask", name
        profile = tool_profiles.get_profile(name)
        assert "media_capture" in profile["sensitivity_tags"], name


# ------------------------------------------------------------- registry

def test_mock_strict_resolves_every_definition():
    for defn in definitions.ALL_DEFS:
        fn = registry.resolve(defn.name)
        assert callable(fn), defn.name
    assert len(definitions.ALL_DEFS) == 65  # counted on the merged tree


def test_result_keys_exact_for_both_ops():
    for name in SENSES:
        result = toolcall.call(name, dict(ARGS[name]), write=True)
        assert "error" not in result, (name, result)
        assert set(result.keys()) == set(
            definitions.by_name(name).result_keys), name


def test_consent_and_approval_event_chains():
    for name in SENSES:
        toolcall.call(name, dict(ARGS[name]), write=True)
    evts = read_events()
    types = [e["type"] for e in evts]
    assert types.count("MediaCaptureConsent") == 2, types
    consent_tools = {e["data"]["tool"] for e in evts
                     if e["type"] == "MediaCaptureConsent"}
    assert consent_tools == set(SENSES)
    # ApprovalRequested data carries tier but no tool key (by design —
    # see test_pipeline_mock); with exactly two write calls above, both
    # events are the senses calls' and both must be always_ask.
    appr = [e for e in evts if e["type"] == "ApprovalRequested"]
    assert len(appr) == 2
    assert all(e["data"]["tier"] == "always_ask" for e in appr)


# ----------------------------------------------------------------- jobs

def _manager(tmp_path, executor, **kw):
    kw.setdefault("watchdog_interval_s", 0.1)
    mgr = jobs.JobManager(db_path=str(tmp_path / "jobs.db"),
                          executor=executor, **kw)
    return mgr


def _wait(mgr, job_id, states=("succeeded",), timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = mgr.status(job_id)
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck: {mgr.status(job_id)}")


def test_resource_map_serializes_senses_with_desktop(tmp_path):
    assert jobs.RESOURCE_MAP["camera_snapshot"] == "desktop"
    assert jobs.RESOURCE_MAP["record_audio"] == "desktop"
    intervals = {}
    lock = threading.Lock()

    def timed(tool, args, ctx):
        t0 = time.monotonic()
        time.sleep(0.5)
        with lock:
            intervals.setdefault(tool, []).append((t0, time.monotonic()))
        return {"ok": True}

    mgr = _manager(tmp_path, timed, max_workers=4)
    try:
        ids = [mgr.submit(t, {}, f"ser-{t}")["job_id"]
               for t in ("camera_snapshot", "screenshot")]
        for jid in ids:
            _wait(mgr, jid)
    finally:
        mgr.shutdown()
    (a0, a1), (b0, b1) = sorted(
        intervals["camera_snapshot"] + intervals["screenshot"])
    assert b0 >= a1 - 0.05, "desktop-locked jobs overlapped"


@pytest.mark.parametrize("tool_name,key", [
    ("camera_snapshot", "png_base64"), ("record_audio", "wav_base64")])
def test_job_blob_roundtrip_and_senses_registration(tmp_path, tool_name,
                                                    key):
    big = "A" * 5000  # result payload well over BLOB_THRESHOLD (2048)

    def executor(tool, args, ctx):
        return {key: big, "width": 2, "height": 2} if key == "png_base64" \
            else {key: big, "duration_s": 2}

    mgr = _manager(tmp_path, executor)
    try:
        jid = mgr.submit(tool_name, {}, f"blob-{tool_name}")["job_id"]
        _wait(mgr, jid)
        row = mgr.result(jid)
        result = row["result"]
        assert result[key] == big  # round-trip intact via blob ref
        ref = mgr.journal.get(jid)["result_ref"]
        assert ref and ref.startswith("sha256:")
        # MF-5 wiring: the jobs layer registered this senses blob.
        assert ref in blob_store._load_senses_index()
        # ...and the purge path can expire it (TTL from registration).
        out = blob_store.purge_senses_media(
            now=time.time() + blob_store.SENSES_MEDIA_TTL_S + 1)
        assert ref in out["refs"]
        assert blob_store.get(ref) is None
    finally:
        mgr.shutdown()


def test_watchdog_purge_cadence_deletes_expired(tmp_path):
    ref = blob_store.put(b"expired senses bytes")
    blob_store.register_senses_blob(
        ref, now=time.time() - blob_store.SENSES_MEDIA_TTL_S - 10)
    mgr = _manager(tmp_path, lambda t, a, c: {"ok": True})
    try:
        mgr._last_senses_purge = 0.0
        mgr._purge_senses_media_throttled()
        assert blob_store.get(ref) is None
        # Throttled: an immediate second call is a no-op even for a
        # newly expired blob.
        ref2 = blob_store.put(b"second expired")
        blob_store.register_senses_blob(
            ref2, now=time.time() - blob_store.SENSES_MEDIA_TTL_S - 10)
        mgr._purge_senses_media_throttled()
        assert blob_store.get(ref2) is not None
    finally:
        mgr.shutdown()
