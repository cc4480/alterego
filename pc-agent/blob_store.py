"""Content-addressed blob store for large event values.

Values over BLOB_THRESHOLD bytes (file contents, screenshots, long
outputs) are stored by SHA-256 under blobs/sha256/ab/abcdef... and the
event carries {"$blob": "sha256:<hex>"} instead. This keeps the event
log greppable and small.

Redaction happens BEFORE storage — secrets never reach the blob store
(the caller redacts first; see toolcall._event_value).
"""
import hashlib
import json
import time
from pathlib import Path

from auth import app_dir

BLOB_THRESHOLD = 2048  # bytes/chars: above this, values go to the blob store

# ---- senses-media retention (privacy MF-5) --------------------------------
# Media captured by the senses ops (camera_snapshot frames, record_audio
# clips) may not sit in this store indefinitely. Senses blobs are recorded
# in ONE index (blobs/senses_media.json: ref -> {"ts", "expiry"}) and
# purge_senses_media() deletes the expired ones. There is exactly ONE
# retention policy for both ops (coordinator decision at integration —
# the camera slice's 7-day value and the audio slice's 48-hour value were
# unified to this single constant):
SENSES_MEDIA_TTL_S = 48 * 3600  # 48 hours — the single senses-media policy
# Legacy name from the camera slice, kept only as an alias of the one
# policy above so no second value can survive:
SENSES_MEDIA_RETENTION_S = SENSES_MEDIA_TTL_S
SENSES_TAG = "media_capture"


def _blob_path(digest_hex):
    return app_dir() / "blobs" / "sha256" / digest_hex[:2] / digest_hex


def _digest_hex(ref):
    """Hex digest for a 'sha256:<hex>' reference (bare hex passes
    through unchanged, matching get()'s long-standing behaviour)."""
    return ref.split(":", 1)[1] if ref.startswith("sha256:") else ref


def _senses_index_path():
    return app_dir() / "blobs" / "senses_media.json"


def _load_senses_index():
    try:
        return json.loads(_senses_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_senses_index(index):
    p = _senses_index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index), encoding="utf-8")


def _register(ref, ts):
    """Record ref in the senses index with expiry ts + TTL. Keeps the
    earliest registration for a ref (first capture time wins)."""
    index = _load_senses_index()
    if ref not in index:
        index[ref] = {"ts": ts, "expiry": ts + SENSES_MEDIA_TTL_S}
        _save_senses_index(index)
    return index[ref]["expiry"]


def register_senses_blob(ref, now=None):
    """Index a senses-media blob for expiry after SENSES_MEDIA_TTL_S.
    Callers (the job layer, when it persists a senses result) must call
    this for every senses blob they put(). Returns the expiry epoch."""
    ts = time.time() if now is None else now
    return _register(ref, ts)


def put(value, tag=None):
    """Store str/bytes, return the 'sha256:<hex>' reference. Idempotent:
    the same content is never written twice (single-writer).

    tag="media_capture" additionally records the blob in the senses
    index so the retention cap can find it. Untagged puts behave
    exactly as before."""
    data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    digest = hashlib.sha256(data).hexdigest()
    p = _blob_path(digest)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    ref = f"sha256:{digest}"
    if tag is not None:
        _register(ref, time.time())
    return ref


def delete(ref):
    """Delete one blob by reference. Returns True if a blob was removed.
    Also drops any senses-index entry for the ref."""
    hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
    removed = False
    try:
        _blob_path(hexpart).unlink()
        removed = True
    except OSError:
        pass
    index = _load_senses_index()
    if ref in index:
        del index[ref]
        _save_senses_index(index)
    return removed


def get(ref):
    """Fetch bytes for a 'sha256:<hex>' reference, or None if missing."""
    try:
        return _blob_path(_digest_hex(ref)).read_bytes()
    except OSError:
        return None


def size(ref):
    """Byte size of the blob for a 'sha256:<hex>' reference, or None if
    missing. Lets callers describe a blob (e.g. a job_result reference)
    without loading its bytes."""
    try:
        return _blob_path(_digest_hex(ref)).stat().st_size
    except OSError:
        return None


def read_range(ref, offset, length=None):
    """Read exactly the requested byte range of a blob: up to `length`
    bytes starting at `offset` (to end-of-blob when length is None).

    Range semantics for resumable fetches (GET /blobs/<sha>):
    - an overrunning length is truncated at EOF (short read, never
      padded and never an error);
    - an offset at or beyond EOF returns b"";
    - a missing blob returns None (matching get()).
    Negative offset/length are caller errors and raise ValueError.
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if length is not None and length < 0:
        raise ValueError(f"length must be >= 0 or None, got {length}")
    try:
        f = _blob_path(_digest_hex(ref)).open("rb")
    except OSError:
        return None
    with f:
        f.seek(offset)
        return f.read() if length is None else f.read(length)


def store_if_large(value, threshold=BLOB_THRESHOLD):
    """Return {"$blob": ref} for large str/bytes values; else value as-is."""
    if isinstance(value, (str, bytes)) and len(value) > threshold:
        return {"$blob": put(value)}
    return value


def blob_dir():
    """Base directory of the blob store (for inspection tooling)."""
    return app_dir() / "blobs"


def purge_senses_media(now=None):
    """Delete every expired senses-media blob and prune the index
    (privacy MF-5). Returns {"purged": n, "bytes": b, "refs": [...]}.
    Non-senses blobs are untouched — only refs present in the senses
    index are ever deleted here."""
    now = time.time() if now is None else now
    index = _load_senses_index()
    purged, freed, refs = 0, 0, []
    for ref, meta in list(index.items()):
        expiry = (meta.get("expiry") if isinstance(meta, dict)
                  else None)
        if expiry is None:  # tolerate a bare-epoch entry
            expiry = float(meta) if isinstance(meta, (int, float)) else now
        if now < float(expiry):
            continue
        hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
        try:
            freed += _blob_path(hexpart).stat().st_size
        except OSError:
            pass
        if delete(ref):  # also prunes the index entry
            purged += 1
            refs.append(ref)
    return {"purged": purged, "bytes": freed, "refs": refs}


def _senses_stats():
    index = _load_senses_index()
    count, total = 0, 0
    for ref in index:
        hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
        try:
            total += _blob_path(hexpart).stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


def senses_media_stats():
    """Inventory of senses-tagged blobs: {"count": n, "bytes": b} over
    blobs that still exist on disk."""
    count, total = _senses_stats()
    return {"count": count, "bytes": total}


def senses_media_inventory():
    """MF-13 (partial): count + total bytes of indexed senses media."""
    count, total = _senses_stats()
    return {"count": count, "bytes": total, "ttl_s": SENSES_MEDIA_TTL_S}
