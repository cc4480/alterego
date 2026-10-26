"""Mock PC tools: canned observation, journaled notifications, safe power.

power() returns its result dict WITHOUT performing any action — there is
no reboot/shutdown code path in the mock, by construction.
"""
from seams.providers.mock import PROCS, record


def active_window() -> dict:
    return {"hwnd": 12345, "title": "Mock Window", "pid": 1234,
            "process": "mock_process.exe"}


def idle_seconds() -> dict:
    return {"idle_seconds": 42}


def list_processes() -> dict:
    return {"count": len(PROCS),
            "processes": [dict(p) for p in PROCS]}


def notify(title: str, message: str, timeout_s: int = 5) -> dict:
    record("notify", {"title": title, "message": message,
                      "timeout_s": timeout_s})
    return {"notified": True, "title": title}


def speak(text: str) -> dict:
    record("speak", {"text": text})
    return {"spoken_chars": len(text)}


def record_audio(duration_s: int = 5, device: str = None) -> dict:
    """Synthetic WAV: a 0.5-full-scale square wave at 8 kHz mono, so the
    payload is a real, parseable WAV whose measured peak/rms match the
    returned metadata. Mirrors the Windows provider's validation."""
    import array
    import base64
    import io
    import wave as _wave
    if isinstance(duration_s, bool) or duration_s is None:
        raise ValueError("duration_s must be an int number of seconds")
    if isinstance(duration_s, float) and duration_s.is_integer():
        duration_s = int(duration_s)
    if not isinstance(duration_s, int):
        raise ValueError("duration_s must be an int number of seconds")
    if duration_s < 1 or duration_s > 30:
        raise ValueError(f"duration_s must be 1-30 (hard cap), "
                         f"got {duration_s}")
    if device is not None and (not isinstance(device, str)
                               or not device.strip()):
        raise ValueError("device must be a non-empty string or None")
    record("record_audio", {"duration_s": duration_s, "device": device})
    rate = 8000
    amp = 16384  # exactly 0.5 of full scale
    pcm = array.array("h", [amp if (i // 8) % 2 == 0 else -amp
                            for i in range(rate * duration_s)])
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return {"wav_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
            "duration_s": float(duration_s), "sample_rate": rate,
            "channels": 1, "peak": 0.5, "rms": 0.5, "near_silent": False,
            "backend": "mock",
            "device": device or "mock microphone"}


def set_volume(level: int) -> dict:
    record("set_volume", {"level": level})
    return {"volume": level}


def power(action: str) -> dict:
    """NEVER reboots/shuts down. The mock power tool is a pure stub: it
    records the requested action and returns, performing nothing.

    Note: returns exactly the ToolDef result_keys (["power"]). The design
    doc's {"ok": True, "note": ...} sketch predates result_keys; the
    contract wins."""
    record("power", {"action": action})
    return {"power": action}
