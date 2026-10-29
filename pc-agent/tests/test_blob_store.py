"""Blob store battery: content addressing (the ref IS the sha256 of
the exact bytes), size(), and read_range() range semantics backing the
Range-capable GET /blobs/<sha> route (S1 bulk-by-reference).
Stdlib unittest (also pytest-compatible); run with:
    python -m unittest tests.test_blob_store   (from pc-agent/)
or  python tests/test_blob_store.py
"""
import hashlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("APPDATA", "/tmp/pc-bridge-test-appdata")

import blob_store  # noqa: E402

DATA = bytes((i * 31 + 7) % 256 for i in range(10_000))
MISSING_REF = "sha256:" + "0" * 64


class TestContentAddressing(unittest.TestCase):
    def test_put_get_round_trip_bytes_and_str(self):
        ref = blob_store.put(DATA)
        self.assertEqual(ref, "sha256:" + hashlib.sha256(DATA).hexdigest())
        self.assertEqual(blob_store.get(ref), DATA)
        text = "héllo wörld"  # non-ASCII: stored as its utf-8 bytes
        tref = blob_store.put(text)
        self.assertEqual(
            tref,
            "sha256:" + hashlib.sha256(
                text.encode("utf-8")).hexdigest())
        self.assertEqual(blob_store.get(tref), text.encode("utf-8"))

    def test_put_is_idempotent(self):
        self.assertEqual(blob_store.put(DATA), blob_store.put(DATA))

    def test_bare_hex_ref_accepted(self):
        ref = blob_store.put(DATA)
        bare = ref.split(":", 1)[1]
        self.assertEqual(blob_store.get(bare), DATA)
        self.assertEqual(blob_store.size(bare), len(DATA))
        self.assertEqual(blob_store.read_range(bare, 0, 4), DATA[:4])

    def test_missing_blob(self):
        self.assertIsNone(blob_store.get(MISSING_REF))
        self.assertIsNone(blob_store.size(MISSING_REF))
        self.assertIsNone(blob_store.read_range(MISSING_REF, 0, 10))

    def test_size(self):
        ref = blob_store.put(DATA)
        self.assertEqual(blob_store.size(ref), len(DATA))

    def test_store_if_large_unchanged(self):
        self.assertEqual(blob_store.store_if_large("small"), "small")
        big = "b" * (blob_store.BLOB_THRESHOLD + 1)
        out = blob_store.store_if_large(big)
        self.assertEqual(set(out), {"$blob"})
        self.assertEqual(blob_store.get(out["$blob"]),
                         big.encode("utf-8"))


class TestReadRange(unittest.TestCase):
    def setUp(self):
        # per-test (not setUpClass): the pytest isolation fixture gives
        # each test its own APPDATA, so the blob must be stored inside
        # the test's own app dir. put() is idempotent — this is cheap.
        self.ref = blob_store.put(DATA)

    def test_start(self):
        self.assertEqual(blob_store.read_range(self.ref, 0, 100),
                         DATA[:100])

    def test_middle(self):
        self.assertEqual(blob_store.read_range(self.ref, 5000, 123),
                         DATA[5000:5123])

    def test_length_none_reads_to_eof(self):
        self.assertEqual(blob_store.read_range(self.ref, 9990),
                         DATA[9990:])
        self.assertEqual(blob_store.read_range(self.ref, 9990, None),
                         DATA[9990:])

    def test_eof_overrun_truncates_never_pads(self):
        out = blob_store.read_range(self.ref, len(DATA) - 10, 1000)
        self.assertEqual(out, DATA[-10:])
        self.assertEqual(len(out), 10)

    def test_offset_at_and_beyond_eof(self):
        self.assertEqual(blob_store.read_range(self.ref, len(DATA), 10),
                         b"")
        self.assertEqual(
            blob_store.read_range(self.ref, len(DATA) + 5000, 10), b"")

    def test_zero_length(self):
        self.assertEqual(blob_store.read_range(self.ref, 0, 0), b"")
        self.assertEqual(blob_store.read_range(self.ref, 500, 0), b"")

    def test_negative_offset_or_length_raises(self):
        with self.assertRaises(ValueError):
            blob_store.read_range(self.ref, -1, 10)
        with self.assertRaises(ValueError):
            blob_store.read_range(self.ref, 0, -5)

    def test_chunked_reassembly_is_byte_identical(self):
        # the resumable-fetch pattern the /blobs route serves: fixed
        # 256-byte chunks, each independently fetchable, reassemble
        # to exactly the stored bytes
        chunks = []
        offset = 0
        while True:
            chunk = blob_store.read_range(self.ref, offset, 256)
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        self.assertEqual(b"".join(chunks), DATA)
        self.assertEqual(offset, len(DATA))


if __name__ == "__main__":
    unittest.main()
