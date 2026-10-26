"""Content-addressed blob store for large event values.

Values over BLOB_THRESHOLD bytes (file contents, screenshots, long
outputs) are stored by SHA-256 under blobs/sha256/ab/abcdef... and the
event carries {"$blob": "sha256:<hex>"} instead. This keeps the event
log greppable and small.

Redaction happens BEFORE storage — secrets never reach the blob store
(the caller redacts first; see toolcall._event_value).
"""
import hashlib
from pathlib import Path

from auth import app_dir

BLOB_THRESHOLD = 2048  # bytes/chars: above this, values go to the blob store


def _blob_path(digest_hex):
    return app_dir() / "blobs" / "sha256" / digest_hex[:2] / digest_hex


def put(value):
    """Store str/bytes, return the 'sha256:<hex>' reference. Idempotent:
    the same content is never written twice (single-writer)."""
    data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    digest = hashlib.sha256(data).hexdigest()
    p = _blob_path(digest)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return f"sha256:{digest}"


def get(ref):
    """Fetch bytes for a 'sha256:<hex>' reference, or None if missing."""
    hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
    try:
        return _blob_path(hexpart).read_bytes()
    except OSError:
        return None


def store_if_large(value, threshold=BLOB_THRESHOLD):
    """Return {"$blob": ref} for large str/bytes values; else value as-is."""
    if isinstance(value, (str, bytes)) and len(value) > threshold:
        return {"$blob": put(value)}
    return value


def blob_dir():
    """Base directory of the blob store (for inspection tooling)."""
    return app_dir() / "blobs"


def delete(ref):
    """Delete the blob for a 'sha256:<hex>' reference. Returns True if a
    blob was removed. (This is the store's only delete path — added for
    the senses-media retention cap below, privacy MF-5.)"""
    hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
    try:
        _blob_path(hexpart).unlink()
        return True
    except OSError:
        return False


# ---- senses-media retention (privacy MF-5) --------------------------------
# Media captured by the senses ops (record_audio / camera_snapshot) may
# not sit in this store indefinitely. Senses result blobs are registered
# in an index with an expiry; purge_senses_media() deletes expired ones.
# The cap is named and tunable in ONE place:
SENSES_MEDIA_TTL_S = 48 * 3600  # 48 hours


def _senses_index_path():
    return app_dir() / "blobs" / "senses_media.json"


def _load_senses_index():
    import json
    try:
        return json.loads(_senses_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_senses_index(index):
    import json
    p = _senses_index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, indent=1), encoding="utf-8")


def register_senses_blob(ref, now=None):
    """Index a senses-media blob for expiry after SENSES_MEDIA_TTL_S.
    Callers (the job layer, when it persists a senses result) must call
    this for every senses blob they put(). Returns the expiry epoch."""
    import time
    expiry = (now if now is not None else time.time()) + SENSES_MEDIA_TTL_S
    index = _load_senses_index()
    index[ref] = expiry
    _save_senses_index(index)
    return expiry


def purge_senses_media(now=None):
    """Delete every expired senses-media blob and prune the index.
    Returns the list of refs deleted. Non-senses blobs are untouched —
    only refs present in the senses index are ever deleted here."""
    import time
    now = time.time() if now is None else now
    index = _load_senses_index()
    expired = [ref for ref, exp in index.items() if exp <= now]
    for ref in expired:
        delete(ref)
        del index[ref]
    if expired:
        _save_senses_index(index)
    return expired


def senses_media_inventory():
    """MF-13 (partial): count + total bytes of indexed senses media."""
    index = _load_senses_index()
    total = 0
    for ref in index:
        hexpart = ref.split(":", 1)[1] if ref.startswith("sha256:") else ref
        try:
            total += _blob_path(hexpart).stat().st_size
        except OSError:
            pass
    return {"count": len(index), "bytes": total,
            "ttl_s": SENSES_MEDIA_TTL_S}
