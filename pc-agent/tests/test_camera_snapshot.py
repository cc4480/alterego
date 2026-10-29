"""Tests for camera_snapshot (senses slice B).

Mirrors test_background_screenshot.py style (mock provider through the
pipeline) and adds the privacy-review gates that bind this op:
MF-1 tier, MF-2 provider gate + write=True, MF-3 both permission
modes, MF-4 audit-or-refuse, MF-5 retention purge, MF-6 jobs lock,
MF-7 blank/no-device failures, MF-9 no streaming args, MF-10 never
in ACCEPT_EDITS_AUTO.

Windows-provider tests call the real provider module directly: on
this Linux VM the approval dialog fails closed in default mode and
both capture backends fail loud in dontAsk mode, which is exactly
the behaviour being asserted. They skip on Windows, where a direct
call would show a real dialog / open a real camera (live proof is a
deployment step, not a unit test).
"""
import base64
import sys
from pathlib import Path

import pytest

import approval
import blob_store
import toolcall
import tool_profiles
from seams import definitions
from seams.providers import mock as mock_state
from seams.providers.mock import read as mock_read
from seams.providers.windows import camera_mf
from seams.providers.windows import read as win_read
from conftest import read_events

_ON_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------- mock path

def test_camera_no_args_roundtrip():
    r = toolcall.call("camera_snapshot", {}, write=True)
    assert r["backend"] == "mock"
    assert r["device"] == "Mock Camera"
    assert r["width"] == 1 and r["height"] == 1
    assert base64.b64decode(r["png_base64"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert mock_state.CALLS[-1] == {
        "tool": "camera_snapshot",
        "args": {"device": None, "max_width": None}}


def test_camera_with_device_and_max_width():
    r = toolcall.call("camera_snapshot",
                      {"device": "Brio", "max_width": 640}, write=True)
    assert r["backend"] == "mock"
    assert mock_state.CALLS[-1]["args"] == {"device": "Brio",
                                            "max_width": 640}
    r = toolcall.call("camera_snapshot", {"device": 0}, write=True)
    assert mock_state.CALLS[-1]["args"]["device"] == 0


def test_camera_result_keys_exact():
    defn = definitions.by_name("camera_snapshot")
    r = toolcall.call("camera_snapshot", {}, write=True)
    assert set(r.keys()) == set(defn.result_keys)


def test_camera_bad_device_rejected():
    for bad in (-1, "", "   ", 1.5, True, ["cam"]):
        r = toolcall.call("camera_snapshot", {"device": bad}, write=True)
        assert "error" in r, bad
        assert "device" in r["error"].lower(), (bad, r)


def test_camera_bad_max_width_rejected():
    for bad in (0, 15, -5, 7681, "wide", True, 12.5):
        r = toolcall.call("camera_snapshot", {"max_width": bad},
                          write=True)
        assert "error" in r, bad
        assert "max_width" in r["error"].lower(), (bad, r)


def test_camera_wrapper_registered_and_write_path():
    import tool_wrappers
    names = [f.__name__ for f in tool_wrappers.ALL_TOOLS]
    assert "camera_snapshot" in names
    r = tool_wrappers.camera_snapshot(device=0, max_width=320)
    assert r["backend"] == "mock"


def test_camera_plan_mode_never_executes(monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "plan")
    r = toolcall.call("camera_snapshot", {}, write=True)
    assert r["plan_mode"] is True
    assert r["would_execute"] == "camera_snapshot"
    assert mock_state.CALLS == []  # nothing journaled: not executed


# ------------------------------------------------------- MF-1 / MF-10 tiers

def test_camera_tier_is_always_ask_in_both_registries():
    defn = definitions.by_name("camera_snapshot")
    assert defn.approval_tier == "always_ask"          # declared (seams)
    assert tool_profiles.approval_tier("camera_snapshot") == "always_ask"
    profile = tool_profiles.get_profile("camera_snapshot")
    assert profile["blast_radius"] != "none"           # never silent
    assert profile["recoverability"] <= 3              # frame can't be recalled
    assert "media_capture" in profile["sensitivity_tags"]
    # Contrast: the sibling read op stays silent — camera must not.
    assert tool_profiles.approval_tier("screenshot") == "silent"


def test_camera_never_in_accept_edits_auto():
    assert "camera_snapshot" not in approval.ACCEPT_EDITS_AUTO


def test_camera_no_streaming_args():
    defn = definitions.by_name("camera_snapshot")
    assert set(defn.args) == {"device", "max_width"}   # MF-9


def test_camera_write_call_event_chain():
    toolcall.call("camera_snapshot", {}, write=True)
    evts = read_events()
    types = [e["type"] for e in evts]
    assert "MediaCaptureConsent" in types
    appr = [e for e in evts if e["type"] == "ApprovalRequested"]
    assert appr and appr[0]["data"]["tier"] == "always_ask"
    consent = [e for e in evts if e["type"] == "MediaCaptureConsent"]
    assert consent[0]["data"]["permission_mode"] == "dontask"
    assert consent[0]["data"]["consent"] == "session_level"
    assert types[-1] == "ToolCompleted"


# ------------------------------------------------------------- MF-4 refusal

def test_camera_refused_when_event_log_disabled(monkeypatch):
    monkeypatch.setenv("EVENT_LOG_ENABLED", "0")
    r = toolcall.call("camera_snapshot", {}, write=True)
    assert "error" in r
    assert "event log is disabled" in r["error"]
    with pytest.raises(RuntimeError, match="event log is disabled"):
        mock_read.camera_snapshot()


# ------------------------------------------------- MF-3 windows gate, modes

@pytest.mark.skipif(_ON_WINDOWS, reason="would show a real dialog")
def test_windows_gate_default_mode_dialog_denies(monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "default")
    with pytest.raises(PermissionError, match="denied by local approval"):
        win_read.camera_snapshot()
    # Denied at the gate: no consent event, no device attempt.
    assert not [e for e in read_events()
                if e["type"] == "MediaCaptureConsent"]


@pytest.mark.skipif(_ON_WINDOWS, reason="would open a real camera")
def test_windows_gate_dontask_proceeds_and_fails_loud():
    # Conftest pins dontAsk: the gate must pass by session-level
    # consent (not silently — the consent event proves it), then both
    # backends fail loud on this camera-less Linux VM.
    with pytest.raises(RuntimeError, match="no capture backend succeeded"):
        win_read.camera_snapshot()
    consent = [e for e in read_events()
               if e["type"] == "MediaCaptureConsent"]
    assert consent and consent[0]["data"]["consent"] == "session_level"


# ------------------------------------------------------- MF-7 frame checks

def test_finish_frame_blank_is_non_success():
    png = base64.b64decode(mock_read._RED_PNG_B64)
    with pytest.raises(RuntimeError, match="frame_blank_or_shutter_closed"):
        win_read._finish_camera_frame(png, "test", "cam", 1, 1, 0.0, True)
    with pytest.raises(RuntimeError, match="frame_blank_or_shutter_closed"):
        win_read._finish_camera_frame(png, "test", "cam", 1, 1, 1.0, False)
    ok = win_read._finish_camera_frame(png, "test", "cam", 1, 1, 54.0, False)
    assert ok["device"] == "cam" and ok["backend"] == "test"


# ------------------------------------------------------- windows unit paths

_DSHOW_SAMPLE = """[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Logitech Brio 100" (video)
[dshow @ 000001]     Alternative name "@device_pnp_\\\\?\\usb#vid_046d&pid_094c"
[dshow @ 000001]  "Microphone (Brio 100)" (audio)
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone (Brio 100)" (audio)
"""


def test_parse_dshow_devices_video_only():
    devs = win_read._parse_dshow_devices(_DSHOW_SAMPLE)
    assert [n for n, _a in devs] == ["Logitech Brio 100"]
    assert "vid_046d" in devs[0][1]


def test_resolve_dshow_device():
    devs = [("Cam A", ""), ("Cam B", "@device_pnp_x")]
    assert win_read._resolve_dshow_device(devs, None) == ("Cam A", None)
    assert win_read._resolve_dshow_device(devs, 1) == ("Cam B", None)
    assert win_read._resolve_dshow_device(devs, "cam b") == ("Cam B", None)
    assert win_read._resolve_dshow_device(devs, "pnp_x") == ("Cam B", None)
    with pytest.raises(RuntimeError, match="device index 5 not found"):
        win_read._resolve_dshow_device(devs, 5)
    with pytest.raises(RuntimeError, match="device 'Nope' not found"):
        win_read._resolve_dshow_device(devs, "Nope")
    with pytest.raises(RuntimeError, match="no camera device found"):
        win_read._resolve_dshow_device([], None)


def test_validate_camera_args():
    assert win_read._validate_camera_args(None, None) == (None, None)
    assert win_read._validate_camera_args(" Brio ", 640) == ("Brio", 640)
    for bad_device in (-1, "", True, 1.5):
        with pytest.raises(ValueError, match="device"):
            win_read._validate_camera_args(bad_device, None)
    for bad_width in (0, "wide", True):
        with pytest.raises(ValueError, match="max_width"):
            win_read._validate_camera_args(None, bad_width)


def test_png_dimensions():
    png = base64.b64decode(mock_read._RED_PNG_B64)
    assert win_read._png_dimensions(png) == (1, 1)
    with pytest.raises(RuntimeError, match="not a PNG"):
        win_read._png_dimensions(b"not a png at all........")


def test_mf_backend_fails_loud_off_windows():
    if _ON_WINDOWS:
        pytest.skip("off-Windows failure path only")
    with pytest.raises(RuntimeError, match="media_foundation"):
        camera_mf.capture()


def test_mf_png_encoder_and_luma_stats():
    white = bytes([255, 255, 255] * 4)
    png = camera_mf._encode_png_rgb(2, 2, white)
    assert win_read._png_dimensions(png) == (2, 2)
    mean, var = camera_mf._luma_stats(white)
    assert mean == pytest.approx(255.0) and var == 0.0
    mean, var = camera_mf._luma_stats(bytes(12))
    assert mean == 0.0 and var == 0.0


# ------------------------------------------------------------- MF-5 purge

def test_blob_store_senses_retention_purge():
    ref = blob_store.put("frame-bytes", tag=blob_store.SENSES_TAG)
    plain = blob_store.put("not senses media")
    assert blob_store.senses_media_stats()["count"] == 1
    # Unified index (integration): keyed by full ref, {"ts", "expiry"}.
    now = blob_store._load_senses_index()[ref]["ts"]
    # Too young: nothing purged.
    young = blob_store.purge_senses_media(now=now + 60)
    assert young["purged"] == 0 and young["refs"] == []
    assert blob_store.get(ref) == b"frame-bytes"
    # Past the named cap: senses blob gone, untagged blob untouched.
    out = blob_store.purge_senses_media(
        now=now + blob_store.SENSES_MEDIA_RETENTION_S + 1)
    assert out["purged"] == 1
    assert blob_store.get(ref) is None
    assert blob_store.get(plain) == b"not senses media"
    # ONE retention policy (coordinator decision at integration): the
    # camera slice's 7-day value was unified to the single 48h constant;
    # the old name survives only as an alias of that one value.
    assert blob_store.SENSES_MEDIA_TTL_S == 48 * 3600
    assert blob_store.SENSES_MEDIA_RETENTION_S == \
        blob_store.SENSES_MEDIA_TTL_S


def test_blob_store_delete():
    ref = blob_store.put("delete me")
    assert blob_store.delete(ref) is True
    assert blob_store.get(ref) is None
    assert blob_store.delete(ref) is False


# ------------------------------------------------------------- MF-6 lock

def test_jobs_resource_lock_entry():
    jobs_path = Path(__file__).resolve().parent.parent / "jobs.py"
    if not jobs_path.exists():
        pytest.skip(
            "jobs.py is not on main — it lives only on "
            "cho-zen1/bridge-jobs-2026-10-23. Integration must add "
            'RESOURCE_MAP["camera_snapshot"] = "desktop" there (MF-6); '
            "this test enforces it wherever jobs.py exists.")
    import jobs
    assert jobs.RESOURCE_MAP["camera_snapshot"] == "desktop"
