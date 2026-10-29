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


def _digest_hex(ref):
    """Hex digest for a 'sha256:<hex>' reference (bare hex passes
    through unchanged, matching get()'s long-standing behaviour)."""
    return ref.split(":", 1)[1] if ref.startswith("sha256:") else ref


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
