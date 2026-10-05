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
