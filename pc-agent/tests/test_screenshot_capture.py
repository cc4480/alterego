"""Slice C tests: screenshot scale/format (Q2/S5), tiling + delta (S2),
mock parity, and the stdlib client stitch module bridge/tiles.py.

The Windows provider is driven through a fake `mss` module injected
into sys.modules (the provider imports mss lazily), backed by synthetic
frames. Tests that need Pillow skip cleanly when it is absent (the
shared venv has none); they run in the slice's private venv.
"""
import base64
import hashlib
import importlib.util
import io
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import blob_store
from seams.providers.mock import read as mock_read
from seams.providers.windows import read as win_read

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
needs_pil = pytest.mark.skipif(not HAVE_PIL, reason="Pillow not installed")

_BRIDGE_TILES = (Path(__file__).resolve().parents[2] / "bridge" / "tiles.py")
_spec = importlib.util.spec_from_file_location("bridge_tiles",
                                               _BRIDGE_TILES)
tiles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tiles)

_DEFAULT_MOCK_FRAME = bytes(mock_read._mock_frame)


# --- fake mss ---------------------------------------------------------------

class _FakeImg:
    def __init__(self, rgb, size):
        self.rgb = rgb
        self.size = size
        self.width, self.height = size


class _FakeScreen:
    def __init__(self, frame, w, h):
        self.frame, self.w, self.h = frame, w, h
        self.monitors = [{"left": 0, "top": 0, "width": w, "height": h},
                         {"left": 0, "top": 0, "width": w, "height": h}]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, area):
        left, top = area["left"], area["top"]
        w, h = area["width"], area["height"]
        if (left, top, w, h) == (0, 0, self.w, self.h):
            return _FakeImg(self.frame, (w, h))
        out = bytearray(w * h * 3)
        for ly in range(h):
            src = ((top + ly) * self.w + left) * 3
            out[ly * w * 3:(ly + 1) * w * 3] = self.frame[src:src + w * 3]
        return _FakeImg(bytes(out), (w, h))


@pytest.fixture()
def fake_screen(monkeypatch):
    def install(frame, w, h):
        screen = _FakeScreen(frame, w, h)
        mss_mod = types.ModuleType("mss")
        mss_mod.mss = lambda: screen
        tools_mod = types.ModuleType("mss.tools")
        tools_mod.to_png = lambda rgb, size: tiles.png_bytes(
            rgb, size[0], size[1])
        mss_mod.tools = tools_mod
        monkeypatch.setitem(sys.modules, "mss", mss_mod)
        monkeypatch.setitem(sys.modules, "mss.tools", tools_mod)
        return screen
    return install


@pytest.fixture(autouse=True)
def _reset_tile_states():
    win_read._reset_tiles_state()
    mock_read._reset_tiles_state()
    mock_read.set_mock_frame(_DEFAULT_MOCK_FRAME, 16, 8)
    yield
    win_read._reset_tiles_state()
    mock_read._reset_tiles_state()
    mock_read.set_mock_frame(_DEFAULT_MOCK_FRAME, 16, 8)


def pattern_frame(w, h):
    frame = bytearray(w * h * 3)
    for y in range(h):
        row = bytearray(w * 3)
        for x in range(w):
            o = x * 3
            row[o] = (x * 7 + y) % 256
            row[o + 1] = (y * 5 + x) % 256
            row[o + 2] = (x + y * 3) % 256
        frame[y * w * 3:(y + 1) * w * 3] = row
    return bytes(frame)


def _fetch(ref):
    return blob_store.get(ref)


# --- Q2: scale --------------------------------------------------------------

@needs_pil
def test_scale_math_explicit_half(fake_screen):
    fake_screen(pattern_frame(640, 400), 640, 400)
    r = win_read.screenshot(scale=0.5, format="png")
    assert (r["width"], r["height"]) == (320, 200)
    assert r["scale"] == 0.5
    assert r["format"] == "png" and r["mime"] == "image/png"
    assert r["png_base64"] == r["image_base64"]
    img = Image.open(io.BytesIO(base64.b64decode(r["image_base64"])))
    assert img.size == (320, 200)


@needs_pil
def test_default_scale_fits_long_edge_1280(fake_screen):
    frame = Image.new("RGB", (2560, 1440), (200, 30, 30)).tobytes()
    fake_screen(frame, 2560, 1440)
    r = win_read.screenshot(format="png")
    assert (r["width"], r["height"]) == (1280, 720)
    assert r["scale"] == pytest.approx(0.5)


@needs_pil
def test_default_scale_small_frame_stays_native(fake_screen):
    fake_screen(pattern_frame(800, 600), 800, 600)
    r = win_read.screenshot(format="png")
    assert (r["width"], r["height"]) == (800, 600)
    assert r["scale"] == 1.0


@needs_pil
def test_explicit_scale_1_returns_native(fake_screen):
    frame = Image.new("RGB", (2560, 1440), (10, 200, 30)).tobytes()
    fake_screen(frame, 2560, 1440)
    r = win_read.screenshot(scale=1.0, format="png")
    assert (r["width"], r["height"]) == (2560, 1440)
    assert r["scale"] == 1.0


@needs_pil
def test_region_capture_dimensions(fake_screen):
    fake_screen(pattern_frame(640, 480), 640, 480)
    r = win_read.screenshot(region={"x": 10, "y": 20, "width": 100,
                                    "height": 50},
                            scale=1.0, format="png")
    assert (r["width"], r["height"]) == (100, 50)


def test_screenshot_validation_errors(fake_screen):
    fake_screen(pattern_frame(64, 64), 64, 64)
    with pytest.raises(ValueError, match="format"):
        win_read.screenshot(format="gif")
    with pytest.raises(ValueError, match="scale"):
        win_read.screenshot(scale=2.0)
    with pytest.raises(ValueError, match="region"):
        win_read.screenshot(region="not-a-dict")


def test_scale_without_pillow_fails_loud_but_native_png_works(
        fake_screen, monkeypatch):
    fake_screen(pattern_frame(100, 50), 100, 50)
    monkeypatch.setitem(sys.modules, "PIL", None)  # import PIL -> ImportError
    with pytest.raises(RuntimeError, match="scale requires Pillow"):
        win_read.screenshot(scale=0.5, format="png")
    with pytest.raises(RuntimeError, match="requires Pillow"):
        win_read.screenshot(format="webp", scale=1.0)
    r = win_read.screenshot(format="png")  # native PNG via mss, no Pillow
    assert r["format"] == "png" and r["png_base64"] == r["image_base64"]
    assert (r["width"], r["height"]) == (100, 50)


# --- S5: formats --------------------------------------------------------------

@needs_pil
def test_default_format_is_lossless_webp(fake_screen):
    fake_screen(pattern_frame(120, 80), 120, 80)
    r = win_read.screenshot(scale=1.0)
    assert r["format"] == "webp" and r["mime"] == "image/webp"
    assert r["png_base64"] is None
    raw = base64.b64decode(r["image_base64"])
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"


@needs_pil
def test_webp_lossless_pixels_equal_png(fake_screen):
    import random
    rng = random.Random(42)
    w, h = 320, 240
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randrange(256), rng.randrange(256),
                  rng.randrange(256)) for _ in range(w * h)])
    fake_screen(img.tobytes(), w, h)
    webp = win_read.screenshot(scale=1.0, format="webp")
    png = win_read.screenshot(scale=1.0, format="png")
    wp = Image.open(io.BytesIO(base64.b64decode(webp["image_base64"])))
    pp = Image.open(io.BytesIO(base64.b64decode(png["image_base64"])))
    assert wp.convert("RGB").tobytes() == pp.convert("RGB").tobytes()
    assert wp.convert("RGB").tobytes() == img.tobytes()


@needs_pil
def test_jpeg_quality_90_plus(fake_screen):
    gradient = Image.linear_gradient("L").convert("RGB").resize((320, 240))
    fake_screen(gradient.tobytes(), 320, 240)
    r = win_read.screenshot(scale=1.0, format="jpeg")
    assert r["format"] == "jpeg" and r["mime"] == "image/jpeg"
    assert r["png_base64"] is None
    raw = base64.b64decode(r["image_base64"])
    assert raw[:2] == b"\xff\xd8"  # JPEG magic
    dec = Image.open(io.BytesIO(raw)).convert("RGB")
    assert dec.size == (320, 240)
    src, out = gradient.tobytes(), dec.tobytes()
    mean_err = sum(abs(a - b) for a, b in zip(src, out)) / len(src)
    assert mean_err < 5.0  # q95 on a smooth gradient: near-lossless


# --- S2: tiles + delta (Windows provider) -------------------------------------

def test_tiles_manifest_correctness(fake_screen):
    frame = pattern_frame(100, 60)
    fake_screen(frame, 100, 60)
    m = win_read.screenshot_tiles(tile_size=32)
    assert (m["width"], m["height"]) == (100, 60)
    assert m["grid"] == {"cols": 4, "rows": 2}
    assert m["tile_size"] == 32
    assert m["delta"] is False
    assert m["full_frame_sha256"] == hashlib.sha256(frame).hexdigest()
    assert len(m["tiles"]) == 8
    for entry in m["tiles"]:
        raw = blob_store.get(entry["blob_ref"])
        assert raw is not None
        assert entry["blob_ref"] == f"sha256:{entry['sha256']}"
        assert entry["size"] == len(raw) == entry["width"] * entry["height"] * 3
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    assert tiles.stitch_tiles(m, _fetch) == frame


def test_tiles_delta_returns_only_changed(fake_screen):
    frame1 = pattern_frame(100, 60)
    fake_screen(frame1, 100, 60)
    win_read.screenshot_tiles(tile_size=32)
    frame2 = bytearray(frame1)
    # Tile index 5: grid 4x2, tile 25x30 -> x 25..49, y 30..59.
    for y in range(30, 60):
        for x in range(25, 50):
            o = (y * 100 + x) * 3
            frame2[o:o + 3] = b"\xff\x00\xff"
    frame2 = bytes(frame2)
    fake_screen(frame2, 100, 60)
    m = win_read.screenshot_tiles(tile_size=32, delta=True)
    assert m["delta"] is True
    assert m["changed"] == [5]
    assert m["unchanged"] == [0, 1, 2, 3, 4, 6, 7]
    assert "blob_ref" in m["tiles"][5]
    assert all("blob_ref" not in t for t in m["tiles"] if t["index"] != 5)
    assert m["full_frame_sha256"] == hashlib.sha256(frame2).hexdigest()
    assert tiles.stitch_tiles(m, _fetch, prev_frame=frame1) == frame2


def test_tiles_delta_grid_change_invalidates(fake_screen):
    frame = pattern_frame(100, 60)
    fake_screen(frame, 100, 60)
    win_read.screenshot_tiles(tile_size=32)
    m = win_read.screenshot_tiles(tile_size=16, delta=True)
    assert m["delta"] is False
    assert all("blob_ref" in t for t in m["tiles"])


def test_tiles_desync_detected_and_self_heals(fake_screen):
    frame1 = pattern_frame(100, 60)
    fake_screen(frame1, 100, 60)
    win_read.screenshot_tiles(tile_size=32)
    frame2 = bytearray(frame1)
    frame2[0:3] = b"\x01\x02\x03"  # inside tile 0
    frame2 = bytes(frame2)
    fake_screen(frame2, 100, 60)
    delta_m = win_read.screenshot_tiles(tile_size=32, delta=True)
    full_m = win_read.screenshot_tiles(tile_size=32)
    corrupt_prev = bytearray(frame1)
    corrupt_prev[-3:] = b"\xaa\xbb\xcc"  # damage an *unchanged* tile's area
    with pytest.raises(tiles.DesyncError):
        tiles.stitch_tiles(delta_m, _fetch, prev_frame=bytes(corrupt_prev))
    out = tiles.assemble(delta_m, _fetch, prev_frame=bytes(corrupt_prev),
                         request_full=lambda: full_m)
    assert out["healed"] is True
    assert out["rgb"] == frame2
    assert out["sha256"] == hashlib.sha256(frame2).hexdigest()
    with pytest.raises(tiles.DesyncError):
        tiles.stitch_tiles(full_m, lambda ref: bytes(len(_fetch(ref))))


# --- bridge/tiles.py client ---------------------------------------------------

def test_fetch_blob_range_chunks():
    import random
    blob = random.Random(7).randbytes(600_000)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Range"))
            if self.headers.get("Authorization") != "Bearer test-token":
                self.send_response(401)
                self.end_headers()
                return
            start_s, end_s = self.headers["Range"][6:].split("-")
            start, end = int(start_s), min(int(end_s), len(blob) - 1)
            body = blob[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{len(blob)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        ref = "sha256:" + hashlib.sha256(blob).hexdigest()
        assert tiles.fetch_blob(url, "test-token", ref) == blob
        assert len(seen) >= 3  # 600 KB in <=256 KB chunks
        assert all(r and r.startswith("bytes=") for r in seen)
        with pytest.raises(Exception):
            tiles.fetch_blob(url, "wrong-token", ref)
    finally:
        server.shutdown()


@needs_pil
def test_png_bytes_roundtrip():
    frame = pattern_frame(17, 11)
    img = Image.open(io.BytesIO(tiles.png_bytes(frame, 17, 11)))
    assert img.size == (17, 11)
    assert img.convert("RGB").tobytes() == frame


def test_assemble_writes_png(tmp_path):
    frame = pattern_frame(100, 60)
    # Build a manifest by hand over an in-memory blob dict.
    store = {}

    def put(raw):
        h = hashlib.sha256(raw).hexdigest()
        store[h] = raw
        return f"sha256:{h}"

    entries = []
    tw = th = 50
    for idx, (x, y) in enumerate([(0, 0), (50, 0), (0, 30), (50, 30)]):
        w, h = min(tw, 100 - x), min(30, 60 - y)
        raw = bytearray(w * h * 3)
        for ly in range(h):
            src = ((y + ly) * 100 + x) * 3
            raw[ly * w * 3:(ly + 1) * w * 3] = frame[src:src + w * 3]
        raw = bytes(raw)
        entries.append({"index": idx, "x": x, "y": y, "width": w,
                        "height": h, "blob_ref": put(raw),
                        "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"grid": {"cols": 2, "rows": 2}, "tile_size": 50,
                "tile_width": 50, "tile_height": 30, "width": 100,
                "height": 60,
                "full_frame_sha256": hashlib.sha256(frame).hexdigest(),
                "delta": False, "tiles": entries}
    out_path = tmp_path / "frame.png"
    out = tiles.assemble(manifest, lambda ref: store[tiles.ref_hex(ref)],
                         out_path=str(out_path))
    assert out["healed"] is False and out["rgb"] == frame
    assert out_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --- mock parity ----------------------------------------------------------------

def test_mock_screenshot_contract():
    r = mock_read.screenshot()
    assert r["format"] == "webp" and r["mime"] == "image/webp"
    assert r["scale"] == 1.0 and r["png_base64"] is None
    assert base64.b64decode(r["image_base64"])[:4] == b"RIFF"
    r = mock_read.screenshot(format="png")
    assert r["png_base64"] == r["image_base64"]
    r = mock_read.screenshot(format="jpeg", scale=0.5)
    assert r["mime"] == "image/jpeg" and r["scale"] == 0.5
    with pytest.raises(ValueError, match="format"):
        mock_read.screenshot(format="gif")
    with pytest.raises(ValueError, match="scale"):
        mock_read.screenshot(scale=2.0)


def test_mock_tiles_delta_parity():
    m1 = mock_read.screenshot_tiles(tile_size=8)
    assert m1["grid"] == {"cols": 2, "rows": 1}
    assert m1["delta"] is False and len(m1["tiles"]) == 2
    assert m1["full_frame_sha256"] == hashlib.sha256(
        _DEFAULT_MOCK_FRAME).hexdigest()
    frame2 = bytearray(_DEFAULT_MOCK_FRAME)
    for y in range(8):  # left half -> tile 0 only
        for x in range(8):
            o = (y * 16 + x) * 3
            frame2[o:o + 3] = b"\x00\xff\x00"
    mock_read.set_mock_frame(bytes(frame2), 16, 8)
    m2 = mock_read.screenshot_tiles(tile_size=8, delta=True)
    assert m2["delta"] is True and m2["changed"] == [0]
    assert "blob_ref" in m2["tiles"][0]
    assert "blob_ref" not in m2["tiles"][1]
    raw = mock_read.get_mock_tile(m2["tiles"][0]["blob_ref"])
    assert hashlib.sha256(raw).hexdigest() == m2["tiles"][0]["sha256"]
    fetch = lambda ref: mock_read.get_mock_tile(ref)
    assert tiles.stitch_tiles(m2, fetch,
                              prev_frame=_DEFAULT_MOCK_FRAME) == bytes(frame2)
