"""record_audio: definition/profile tier agreement (MF-1), provider
gates (MF-2/3/4), backend priority + fail-loud rules (MF-7), retention
(MF-5), job lock (MF-6), and mock roundtrip.

The Windows capture backends cannot run on this Linux VM; the provider
tests therefore exercise the pure helpers directly and monkeypatch the
device-touching seams (_available_backends / _wavein_device_names /
_record_wavein) while keeping validation, consent, audit, and
finalize logic real.
"""
import array
import base64
import importlib.util
import io
import time
import wave as _wave

import pytest

import approval
import blob_store
import tool_profiles
import toolcall
from conftest import read_events
from seams import definitions
from seams.providers import mock as mock_state
from seams.providers.mock import pc as mock_pc
from seams.providers.windows import pc as win_pc


def _loud_pcm(seconds=1, rate=44100, amp=16384):
    return array.array(
        "h", [amp if (i // 8) % 2 == 0 else -amp
              for i in range(rate * seconds)]).tobytes()


@pytest.fixture(autouse=True)
def _reset_rate_guard():
    win_pc._last_capture_end[0] = 0.0
    yield
    win_pc._last_capture_end[0] = 0.0


# ---- MF-1: tier agreement, never silent ---------------------------------

def test_definition_tier_is_always_ask():
    defn = definitions.by_name("record_audio")
    assert defn is not None
    assert defn.group == "pc"
    assert defn.approval_tier == "always_ask"
    assert set(defn.args) == {"duration_s", "device"}  # MF-9: no stream args
    assert set(defn.result_keys) == {
        "wav_base64", "duration_s", "sample_rate", "channels", "peak",
        "rms", "near_silent", "backend", "device"}


def test_profile_computes_always_ask():
    profile = tool_profiles.PROFILES["record_audio"]
    assert profile["blast_radius"] != "none"  # Trap A: "none" => silent
    assert "media_capture" in profile["sensitivity_tags"]
    assert tool_profiles.approval_tier("record_audio") == "always_ask"
    # The two registries agree (MF-1).
    assert definitions.by_name("record_audio").approval_tier == \
        tool_profiles.approval_tier("record_audio")


def test_not_in_accept_edits_auto():
    assert "record_audio" not in approval.ACCEPT_EDITS_AUTO  # MF-10


# ---- arg validation (cap enforcement) ------------------------------------

@pytest.mark.parametrize("bad", [0, -1, 31, 300, "5", None, True, 2.5])
def test_validate_rejects_bad_duration(bad):
    with pytest.raises(ValueError):
        win_pc._validate_record_args(bad, None)


def test_validate_accepts_bounds_and_hint():
    assert win_pc._validate_record_args(1, None) == (1, None)
    assert win_pc._validate_record_args(30, " Brio ") == (30, "Brio")
    assert win_pc._validate_record_args(5.0, None) == (5, None)
    with pytest.raises(ValueError):
        win_pc._validate_record_args(5, "   ")


def test_mock_mirrors_validation():
    for bad in (0, 31, "5", None):
        with pytest.raises(ValueError):
            mock_pc.record_audio(bad)


# ---- mock roundtrip through the full pipeline ----------------------------

def test_mock_roundtrip_via_pipeline():
    result = toolcall.call("record_audio", {"duration_s": 2,
                                            "device": None}, write=True)
    defn = definitions.by_name("record_audio")
    assert set(result.keys()) == set(defn.result_keys), result
    assert result["backend"] == "mock"
    assert result["near_silent"] is False
    raw = base64.b64decode(result["wav_base64"])
    with _wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getframerate() == result["sample_rate"] == 8000
        assert w.getnchannels() == 1
        assert w.getnframes() == 2 * 8000
    assert mock_state.CALLS[-1] == {
        "tool": "record_audio",
        "args": {"duration_s": 2, "device": None}}


def test_pipeline_rejects_over_cap():
    result = toolcall.call("record_audio", {"duration_s": 31,
                                            "device": None}, write=True)
    assert set(result.keys()) == {"error"}
    assert "1-30" in result["error"]


# ---- backend priority + device matching ----------------------------------

def test_backend_priority_order():
    assert win_pc._available_backends(True, None) == ["wavein", "mci"]
    assert win_pc._available_backends(True, "/x/ffmpeg") == [
        "wavein", "ffmpeg", "mci"]
    assert win_pc._available_backends(False, "/x/ffmpeg") == ["ffmpeg"]
    assert win_pc._available_backends(False, None) == []


def test_device_match_substring_case_insensitive():
    names = ["Microphone (Brio 100)", "Headset Mic (onn Wired)"]
    assert win_pc._match_device_index(names, "brio") == 0
    assert win_pc._match_device_index(names, "ONN WIRED") == 1
    assert win_pc._match_device_index(names, "nope") is None


def test_no_backend_fails_loud(monkeypatch):
    monkeypatch.setattr(win_pc, "_available_backends",
                        lambda *a: [])
    with pytest.raises(RuntimeError, match="no audio capture backend"):
        win_pc.record_audio(2)


def test_unknown_device_fails_before_capture(monkeypatch):
    monkeypatch.setattr(win_pc, "_available_backends",
                        lambda *a: ["wavein"])
    monkeypatch.setattr(win_pc, "_wavein_device_names",
                        lambda: ["Microphone (Brio 100)"])
    with pytest.raises(RuntimeError, match="no input device matching"):
        win_pc.record_audio(2, device="nonexistent mic")


# ---- PCM stats / finalize: silence is a non-success (MF-7) ---------------

def test_pcm_stats_and_wav_roundtrip():
    wav = win_pc._wav_from_pcm(_loud_pcm(1))
    info = win_pc._wav_info(wav)
    assert info["sample_rate"] == 44100 and info["channels"] == 1
    assert info["duration_s"] == 1.0
    assert info["peak"] == 0.5 and info["rms"] == 0.5


def test_finalize_rejects_silence():
    silent = win_pc._wav_from_pcm(b"\x00\x00" * 44100)
    with pytest.raises(RuntimeError, match="silent_capture"):
        win_pc._finalize_capture(silent, "wavein", "some mic")


def test_finalize_success_shape():
    result = win_pc._finalize_capture(
        win_pc._wav_from_pcm(_loud_pcm(1)), "wavein", "Brio mic")
    assert result["near_silent"] is False
    assert result["backend"] == "wavein"
    assert result["device"] == "Brio mic"
    assert result["peak"] == 0.5


def test_silent_capture_through_provider(monkeypatch):
    monkeypatch.setattr(win_pc, "_available_backends",
                        lambda *a: ["wavein"])
    monkeypatch.setattr(win_pc, "_wavein_device_names",
                        lambda: ["Microphone (Brio 100)"])
    monkeypatch.setattr(win_pc, "_record_wavein",
                        lambda d, i: b"\x00\x00" * 44100 * d)
    with pytest.raises(RuntimeError, match="silent_capture"):
        win_pc.record_audio(1)


# ---- MF-4: audit-or-refuse ------------------------------------------------

def test_event_log_disabled_refuses(monkeypatch):
    monkeypatch.setenv("EVENT_LOG_ENABLED", "0")
    with pytest.raises(RuntimeError, match="EVENT_LOG_ENABLED=0"):
        win_pc._require_audit_log()
    with pytest.raises(RuntimeError, match="EVENT_LOG_ENABLED=0"):
        win_pc.record_audio(2)


# ---- MF-3: consent is explicit per permission mode ------------------------

def _stub_wavein(monkeypatch):
    monkeypatch.setattr(win_pc, "_available_backends",
                        lambda *a: ["wavein"])
    monkeypatch.setattr(win_pc, "_wavein_device_names",
                        lambda: ["Microphone (Brio 100)"])
    monkeypatch.setattr(win_pc, "_record_wavein",
                        lambda d, i: _loud_pcm(d))


def test_dontask_mode_records_session_consent(monkeypatch):
    # conftest pins PC_BRIDGE_PERMISSION_MODE=dontAsk.
    assert approval.get_permission_mode() == "dontask"
    _stub_wavein(monkeypatch)
    result = win_pc.record_audio(1, device="brio")
    assert result["device"] == "Microphone (Brio 100)"
    consent = [e for e in read_events()
               if e["type"] == "MediaCaptureConsent"]
    assert len(consent) == 1
    data = consent[0]["data"]
    assert data["permission_mode"] == "dontask"
    assert data["consent"] == "session_dontask"
    assert data["device"] == "Microphone (Brio 100)"
    assert data["duration_s"] == 1


def test_default_mode_dialog_gate_fires(monkeypatch):
    # In default mode the always_ask dialog must fire. On this Linux VM
    # the dialog cannot be shown, and approval fails CLOSED (denied) —
    # which is exactly the proof that the gate is on the capture path:
    # no consent event, no capture.
    monkeypatch.setattr(approval, "PERMISSION_MODE", "default")
    _stub_wavein(monkeypatch)
    called = []
    monkeypatch.setattr(win_pc, "_record_wavein",
                        lambda d, i: called.append(1) or _loud_pcm(d))
    with pytest.raises(PermissionError):
        win_pc.record_audio(1, device="brio")
    assert called == []  # mic was never opened
    consent = [e for e in read_events()
               if e["type"] == "MediaCaptureConsent"]
    assert consent == []


# ---- MF-12: rate guard -----------------------------------------------------

def test_rate_guard():
    win_pc._last_capture_end[0] = time.time()
    with pytest.raises(RuntimeError, match="rate guard"):
        win_pc._check_rate_guard()
    win_pc._last_capture_end[0] = time.time() - 60
    win_pc._check_rate_guard()  # must not raise


# ---- MF-5: senses-media retention cap + purge ------------------------------

def test_senses_blob_retention_purge():
    keep = blob_store.put(b"not senses media")
    ref = blob_store.put(b"fake senses wav bytes")
    blob_store.register_senses_blob(ref, now=1000.0)
    ttl = blob_store.SENSES_MEDIA_TTL_S
    # Unified purge API (integration): returns a dict, refs included.
    young = blob_store.purge_senses_media(now=1000.0 + ttl - 1)
    assert young["purged"] == 0 and young["refs"] == []
    assert blob_store.get(ref) is not None
    deleted = blob_store.purge_senses_media(now=1000.0 + ttl + 1)
    assert deleted["purged"] == 1 and deleted["refs"] == [ref]
    assert blob_store.get(ref) is None
    assert blob_store.get(keep) == b"not senses media"  # untouched
    inv = blob_store.senses_media_inventory()
    assert inv["count"] == 0 and inv["ttl_s"] == ttl


# ---- MF-6: job resource lock (jobs.py lives on the jobs branch) -----------

def test_jobs_resource_lock_when_jobs_module_present():
    if importlib.util.find_spec("jobs") is None:
        pytest.skip("jobs.py is not on main; RESOURCE_MAP entry is an "
                    "integration-time edit on the jobs branch (MF-6)")
    import jobs
    assert jobs.RESOURCE_MAP["record_audio"] == "desktop"


def test_wavehdr_lpdata_void_p_and_string_at_roundtrips_nuls():
    """Regression (flawless Wave 2 item 5): _WAVEHDR.lpData declared
    c_char_p truncates PCM at the first NUL byte when the field is read
    back, which produced near-zero reported durations. lpData must be
    c_void_p and read-back must assemble with ctypes.string_at over
    dwBytesRecorded — proven here with NUL-heavy PCM."""
    import ctypes
    field_types = dict(win_pc._WAVEHDR._fields_)
    assert field_types["lpData"] is ctypes.c_void_p
    pcm = bytes([0, 32, 0, 0, 255, 127, 0, 64]) * 64
    buf = ctypes.create_string_buffer(pcm, len(pcm))
    hdr = win_pc._WAVEHDR(ctypes.cast(buf, ctypes.c_void_p), len(pcm),
                          len(pcm), None, 0, 0, None, None)
    assembled = ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded)
    assert assembled == pcm
