#!/usr/bin/env python3
"""Client-side stitching for the screenshot_tiles op (STDLIB ONLY).

The server (pc-agent screenshot_tiles, slice C / S2) captures the screen
as raw-RGB tiles, stores each tile in its blob store, and returns a
manifest: {grid, tile_size, tile_width, tile_height, width, height,
full_frame_sha256, delta, tiles: [{index, x, y, width, height,
blob_ref "sha256:<hex>", size, sha256} | {index, sha256} for unchanged
tiles in delta manifests]}. A tile blob IS the tile's raw RGB bytes, so
the blob ref's hash doubles as the tile hash.

This module reassembles a full frame from a manifest:

    frame = stitch_tiles(manifest, fetch)            # raw RGB bytes
    out = assemble(manifest, fetch, out_path="f.png")  # + PNG file

`fetch` is any callable taking a blob_ref (or bare hex) and returning
the blob bytes. fetch_blob()/make_fetcher() provide one against the
blob contract: GET /blobs/<hex> with an Authorization: Bearer header,
fetched in <=256 KB chunks via HTTP Range requests, SHA-256 verified
against the ref (the ref IS the hash, so verification is free).

NOTE: bridge/op_call.py exposes no importable blob-fetch helper (only
the MCP post/_tool_call path), so the fetcher is implemented here
against the same contract; if op_call grows one, make_fetcher can be
swapped for it without touching the stitch logic.

Delta manifests list unchanged tiles by index + sha256 only; stitch
fills those tiles from `prev_frame` (the previous full frame's raw RGB
bytes, same dimensions). Any hash mismatch — a tile that does not match
its manifest sha256, an unchanged tile whose previous bytes no longer
hash to the listed value, or a stitched frame whose sha256 differs
from full_frame_sha256 — raises DesyncError. assemble() turns that
into self-healing: given `request_full` (a callable returning a fresh
full, non-delta manifest), it retries exactly once with that manifest.
"""
import hashlib
import struct
import urllib.error
import urllib.request
import zlib

CHUNK_SIZE = 256 * 1024  # blob fetch chunk ceiling per the contract

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/126.0.0.0 Safari/537.36")


class DesyncError(Exception):
    """The tiles/previous frame do not hash to the manifest's values."""


def ref_hex(ref) -> str:
    """Bare hex digest from a 'sha256:<hex>' ref (or a bare hex str)."""
    h = ref.split(":", 1)[1] if isinstance(ref, str) and ":" in ref else ref
    if not isinstance(h, str) or len(h) != 64:
        raise ValueError(f"bad blob ref: {ref!r}")
    return h.lower()


def fetch_blob(base_url, token, ref, timeout=30, opener=None) -> bytes:
    """Fetch one blob by ref, in <=256 KB Range chunks, hash-verified.

    GET /blobs/<hex> with `Authorization: Bearer <token>`. A server that
    answers 200 (Range ignored) to the first request is accepted whole;
    206 responses are reassembled via Content-Range until the total is
    complete. Raises DesyncError if the bytes do not hash to the ref.
    """
    hexpart = ref_hex(ref)
    url = base_url.rstrip("/") + "/blobs/" + hexpart
    open_fn = opener or urllib.request.urlopen
    data = bytearray()
    total = None
    while total is None or len(data) < total:
        start = len(data)
        end = start + CHUNK_SIZE - 1
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": _UA,
            "Range": f"bytes={start}-{end}",
        })
        with open_fn(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
            crange = resp.headers.get("Content-Range") \
                if getattr(resp, "headers", None) else None
        if status == 200:
            # Range ignored (or a one-shot server): this is the blob.
            data = bytearray(body)
            break
        if crange and "/" in crange:
            total = int(crange.rsplit("/", 1)[1])
        if not body:
            break  # server ended the stream; the hash check will judge
        data.extend(body)
        if len(body) < CHUNK_SIZE and total is None:
            break
    out = bytes(data)
    if hashlib.sha256(out).hexdigest() != hexpart:
        raise DesyncError(
            f"blob {hexpart[:12]}... failed sha256 verification "
            f"({len(out)} bytes fetched)")
    return out


def make_fetcher(base_url, token, timeout=30):
    """Return a fetch(blob_ref) -> bytes callable for stitch/assemble."""
    def _fetch(ref):
        return fetch_blob(base_url, token, ref, timeout=timeout)
    return _fetch


def _tile_geometry(manifest, entry):
    """(x, y, w, h) for a tile entry: explicit fields win; otherwise
    derive from the manifest grid + nominal tile size."""
    if "x" in entry and "width" in entry:
        return (int(entry["x"]), int(entry["y"]),
                int(entry["width"]), int(entry["height"]))
    cols = int(manifest["grid"]["cols"])
    idx = int(entry["index"])
    width, height = int(manifest["width"]), int(manifest["height"])
    tile_w = int(manifest.get("tile_width") or -(-width // cols))
    tile_h = int(manifest.get("tile_height")
                 or -(-height // int(manifest["grid"]["rows"])))
    x, y = (idx % cols) * tile_w, (idx // cols) * tile_h
    return x, y, min(tile_w, width - x), min(tile_h, height - y)


def _slice_tile(frame, width, x, y, w, h):
    """Raw RGB bytes of a tile region cut from a full frame."""
    out = bytearray(w * h * 3)
    for ly in range(h):
        src = ((y + ly) * width + x) * 3
        out[ly * w * 3:(ly + 1) * w * 3] = frame[src:src + w * 3]
    return bytes(out)


def _paste_tile(frame, width, raw, x, y, w, h):
    for ly in range(h):
        src = ly * w * 3
        dst = ((y + ly) * width + x) * 3
        frame[dst:dst + w * 3] = raw[src:src + w * 3]


def stitch_tiles(manifest, fetch, prev_frame=None) -> bytes:
    """Assemble the full raw-RGB frame described by `manifest`.

    fetch(blob_ref) -> bytes supplies changed/full tiles; unchanged
    delta tiles (no blob_ref) are cut from prev_frame. Verifies every
    tile hash and the final full_frame_sha256. Raises DesyncError on
    any mismatch or when an unchanged tile has no usable prev_frame.
    """
    width, height = int(manifest["width"]), int(manifest["height"])
    frame = bytearray(width * height * 3)
    for entry in manifest["tiles"]:
        x, y, w, h = _tile_geometry(manifest, entry)
        if "blob_ref" in entry:
            raw = fetch(entry["blob_ref"])
        elif prev_frame is not None:
            if len(prev_frame) != width * height * 3:
                raise DesyncError(
                    "previous frame dimensions do not match manifest")
            raw = _slice_tile(prev_frame, width, x, y, w, h)
        else:
            raise DesyncError(
                f"tile {entry['index']} unchanged but no previous "
                "frame supplied")
        if len(raw) != w * h * 3:
            raise DesyncError(
                f"tile {entry['index']}: {len(raw)} bytes, "
                f"expected {w * h * 3}")
        if hashlib.sha256(raw).hexdigest() != ref_hex(entry["sha256"]):
            raise DesyncError(
                f"tile {entry['index']} hash mismatch (desync)")
        _paste_tile(frame, width, raw, x, y, w, h)
    out = bytes(frame)
    if hashlib.sha256(out).hexdigest() != ref_hex(
            manifest["full_frame_sha256"]):
        raise DesyncError("full-frame hash mismatch (desync)")
    return out


def png_bytes(rgb, width, height) -> bytes:
    """Encode raw RGB bytes as a PNG using only zlib/struct (stdlib)."""
    if len(rgb) != width * height * 3:
        raise ValueError("rgb length must be width*height*3")

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride]
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def write_png(path, rgb, width, height) -> str:
    """Write raw RGB bytes to `path` as a PNG file; returns the path."""
    with open(path, "wb") as f:
        f.write(png_bytes(rgb, width, height))
    return str(path)


def assemble(manifest, fetch, out_path=None, prev_frame=None,
             request_full=None) -> dict:
    """Stitch a manifest into a full image, self-healing once on desync.

    On DesyncError, if `request_full` is given it is called once for a
    fresh full manifest and the stitch is retried without prev_frame; a
    second mismatch raises. Returns {"width", "height", "sha256",
    "healed", "rgb"} plus "path" when out_path was written.
    """
    healed = False
    try:
        frame = stitch_tiles(manifest, fetch, prev_frame=prev_frame)
    except DesyncError:
        if request_full is None:
            raise
        manifest = request_full()
        frame = stitch_tiles(manifest, fetch)
        healed = True
    result = {
        "width": int(manifest["width"]),
        "height": int(manifest["height"]),
        "sha256": ref_hex(manifest["full_frame_sha256"]),
        "healed": healed,
        "rgb": frame,
    }
    if out_path is not None:
        result["path"] = write_png(out_path, frame, result["width"],
                                   result["height"])
    return result
