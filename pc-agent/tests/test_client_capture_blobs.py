"""Client battery for bridge/op_call.py — Slice A (flawless build):

  Q1  stale-capture fix: capture files unlinked BEFORE the request,
      written via temp file + atomic rename, success line carries
      bytes + SHA-256.
  Q3  ROI-first capture helper: window resolved via list_windows,
      region from UIA rects, native-resolution region capture,
      full-screen fallback.
  Q4c response parsing: plain JSON bodies AND legacy SSE `data:`
      bodies both parse.
  S1  blob fetch: <=chunk-size Range requests, per-chunk retry,
      resume from the partial file on disk, SHA-256 verification,
      hash-mismatch rejection.

Stdlib unittest (pytest-compatible), local stub servers only — no
real network, no real PC. Run with:
    python -m pytest tests/test_client_capture_blobs.py -q
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PC_AGENT = Path(__file__).resolve().parent.parent
BRIDGE = PC_AGENT.parent / "bridge"
sys.path.insert(0, str(BRIDGE))
os.environ.setdefault("APPDATA", "/tmp/pc-bridge-test-appdata")

_LOAD_N = [0]


def _load_op_call(url):
    os.environ["PC_BRIDGE_URL"] = url
    _LOAD_N[0] += 1
    spec = importlib.util.spec_from_file_location(
        f"op_call_slice_a_{_LOAD_N[0]}", BRIDGE / "op_call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TOKEN = "test-token"
    return mod


OP = _load_op_call("http://127.0.0.1:1")


# ---------------------------------------------------------------------------
# Q4c — response body parsing (unit + via a stub MCP server)
# ---------------------------------------------------------------------------

class TestParsePayload(unittest.TestCase):
    def test_plain_json_body(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "result": {"ok": True}})
        self.assertEqual(OP._parse_rpc_payload(raw)["result"],
                         {"ok": True})

    def test_plain_json_pretty_printed(self):
        raw = json.dumps({"result": [1, 2]}, indent=2)
        self.assertEqual(OP._parse_rpc_payload(raw)["result"], [1, 2])

    def test_sse_body_still_parses(self):
        raw = ("event: message\n"
               "data: " + json.dumps({"result": {"sse": 1}}) + "\n\n")
        self.assertEqual(OP._parse_rpc_payload(raw)["result"],
                         {"sse": 1})

    def test_empty_body(self):
        self.assertEqual(OP._parse_rpc_payload(""), {})
        self.assertEqual(OP._parse_rpc_payload("  \n"), {})

    def test_error_body(self):
        raw = json.dumps({"error": {"code": -1, "message": "boom"}})
        self.assertIn("error", OP._parse_rpc_payload(raw))


class _McpStub(BaseHTTPRequestHandler):
    MODE = "json"  # or "sse"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        result = {"protocolVersion": "2025-06-18", "capabilities": {},
                  "serverInfo": {"name": "stub", "version": "0"}}
        msg = {"jsonrpc": "2.0", "id": 1, "result": result}
        if _McpStub.MODE == "sse":
            raw = ("data: " + json.dumps(msg) + "\n").encode()
            ctype = "text/event-stream"
        else:
            raw = json.dumps(msg).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Mcp-Session-Id", "stub-session")
        self.end_headers()
        self.wfile.write(raw)


class TestPostBothModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _McpStub)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _init(self, mode):
        _McpStub.MODE = mode
        op = _load_op_call(self.url)
        res, sid = op.post(
            "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {}})
        self.assertEqual(res["serverInfo"]["name"], "stub")
        self.assertEqual(sid, "stub-session")

    def test_post_parses_plain_json(self):
        self._init("json")

    def test_post_parses_sse(self):
        self._init("sse")


# ---------------------------------------------------------------------------
# Q1 — stale capture files
# ---------------------------------------------------------------------------

class TestCaptureFiles(unittest.TestCase):
    def test_prepare_unlinks_stale_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "shot-full.json"
            p.write_text("PREVIOUS CAPTURE")
            OP._prepare_capture(p)
            self.assertFalse(p.exists())

    def test_save_is_atomic_and_reports_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "shot-full.json"
            n, digest = OP._save_capture("payload-123", p)
            data = p.read_bytes()
            self.assertEqual(n, len(data))
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual(data, b"payload-123")
            leftovers = [x for x in Path(d).iterdir() if x != p]
            self.assertEqual(leftovers, [])  # no temp files left behind

    def _run_main_screenshot(self, tmp, post_impl):
        op = _load_op_call("http://127.0.0.1:1")
        op.CAPTURE_FILE = Path(tmp) / "shot-full.json"
        op.FULL_FILE = Path(tmp) / "op-full.json"
        op.CAPTURE_FILE.write_text("STALE")
        op.FULL_FILE.write_text("STALE")
        real_post, real_argv = op.post, sys.argv
        op.post = post_impl
        sys.argv = ["op_call.py", "call", "screenshot", "{}"]
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                op.main()
            return op, out.getvalue()
        finally:
            op.post, sys.argv = real_post, real_argv

    @staticmethod
    def _fake_post_ok(path, body, session_id=None):
        if body.get("method") == "initialize":
            return {"ok": True}, "sid"
        if body.get("method") == "tools/call":
            text = json.dumps({"png_base64": "QUJD", "width": 1,
                               "height": 1})
            return {"content": [{"type": "text", "text": text}]}, "sid"
        return None, "sid"

    @staticmethod
    def _fake_post_dies(path, body, session_id=None):
        if body.get("method") == "initialize":
            return {"ok": True}, "sid"
        raise RuntimeError("capture died mid-transfer")

    def test_failed_capture_leaves_no_stale_file(self):
        with tempfile.TemporaryDirectory() as d:
            op = _load_op_call("http://127.0.0.1:1")
            op.CAPTURE_FILE = Path(d) / "shot-full.json"
            op.FULL_FILE = Path(d) / "op-full.json"
            op.CAPTURE_FILE.write_text("STALE")
            real_post, real_argv = op.post, sys.argv
            op.post = self._fake_post_dies
            sys.argv = ["op_call.py", "call", "screenshot", "{}"]
            try:
                with self.assertRaises(RuntimeError):
                    op.main()
            finally:
                op.post, sys.argv = real_post, real_argv
            self.assertFalse(op.CAPTURE_FILE.exists())
            self.assertFalse(op.FULL_FILE.exists())

    def test_success_line_prints_bytes_and_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            op, printed = self._run_main_screenshot(d, self._fake_post_ok)
            data = op.CAPTURE_FILE.read_bytes()
            expect = json.dumps({"png_base64": "QUJD", "width": 1,
                                 "height": 1}).encode()
            self.assertEqual(data, expect)
            self.assertIn(f"({len(expect)} bytes, sha256 "
                          f"{hashlib.sha256(expect).hexdigest()})",
                          printed)


# ---------------------------------------------------------------------------
# Q3 — ROI-first capture
# ---------------------------------------------------------------------------

WINDOWS = {"windows": [
    {"hwnd": 111, "pid": 10, "title": "Hermes — Chat"},
    {"hwnd": 222, "pid": 11, "title": "Something Else"}]}
ELEMENTS = [
    {"name": "Message", "control_type": "Edit", "automation_id": "",
     "x": 100, "y": 200, "width": 50, "height": 20},
    {"name": "Send", "control_type": "Button", "automation_id": "",
     "x": 120, "y": 240, "width": 80, "height": 30}]


class _FakeCall:
    def __init__(self, uia_error=False, elements=ELEMENTS):
        self.calls = []
        self.uia_error = uia_error
        self.elements = elements

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "list_windows":
            return WINDOWS
        if name == "uia_find":
            if self.uia_error:
                raise RuntimeError("window not found")
            return {"ok": True, "count": len(self.elements),
                    "elements": self.elements}
        if name == "screenshot":
            if args.get("scale") == 0.5:
                return {"png_base64": "T1ZFUg==", "width": 960,
                        "height": 540}
            return {"png_base64": "U0hPVA==", "width": 100,
                    "height": 70}
        raise AssertionError(name)  # pragma: no cover

    def shot_args(self):
        return [a for n, a in self.calls if n == "screenshot"]


class TestCaptureWindow(unittest.TestCase):
    def test_resolve_window_variants(self):
        self.assertEqual(OP._resolve_window(WINDOWS, "Hermes")["hwnd"],
                         111)
        self.assertEqual(OP._resolve_window(WINDOWS, "222")["hwnd"], 222)
        self.assertEqual(OP._resolve_window(WINDOWS, "some.*else")["hwnd"],
                         222)
        self.assertIsNone(OP._resolve_window(WINDOWS, "nope"))

    def test_union_region_at_native_resolution(self):
        fake = _FakeCall()
        out = OP.capture_window(fake, "Hermes")
        self.assertEqual(out["mode"], "region")
        self.assertEqual(out["region_source"], "window-union")
        # union of (100,200,50,20) and (120,240,80,30)
        self.assertEqual(out["region"], {"x": 100, "y": 200,
                                         "width": 100, "height": 70})
        self.assertEqual(fake.shot_args(),
                         [{"region": out["region"], "scale": 1.0}])
        # UIA was queried against the resolved hwnd, not the title
        uia = [a for n, a in fake.calls if n == "uia_find"][0]
        self.assertEqual(uia["window"], "111")

    def test_filtered_element_region(self):
        fake = _FakeCall()
        out = OP.capture_window(fake, "Hermes", name_rx="Message")
        self.assertEqual(out["region_source"], "element")
        self.assertEqual(out["region"], {"x": 100, "y": 200,
                                         "width": 50, "height": 20})

    def test_index_selects_element(self):
        fake = _FakeCall()
        out = OP.capture_window(fake, "Hermes", index=1)
        self.assertEqual(out["region"], {"x": 120, "y": 240,
                                         "width": 80, "height": 30})

    def test_fallback_fullscreen_when_uia_fails(self):
        fake = _FakeCall(uia_error=True)
        out = OP.capture_window(fake, "Hermes")
        self.assertEqual(out["mode"], "fullscreen-fallback")
        self.assertEqual(fake.shot_args(), [{}])

    def test_fallback_fullscreen_when_no_elements(self):
        fake = _FakeCall(elements=[])
        out = OP.capture_window(fake, "Hermes")
        self.assertEqual(out["mode"], "fullscreen-fallback")

    def test_overview_alongside_region(self):
        fake = _FakeCall()
        out = OP.capture_window(fake, "Hermes", overview=True)
        self.assertEqual(out["mode"], "region")
        self.assertIn("overview", out)
        self.assertEqual(out["overview"]["width"], 960)

    def test_clamp_negative_origin(self):
        self.assertEqual(OP._clamp_region(-10, -5, 50, 40),
                         {"x": 0, "y": 0, "width": 40, "height": 35})
        self.assertIsNone(OP._clamp_region(-100, 0, 50, 40))


# ---------------------------------------------------------------------------
# S1 — blob fetch against a local stub server
# ---------------------------------------------------------------------------

CONTENT = bytes(range(256)) * 5          # 1280 deterministic bytes
DIGEST = hashlib.sha256(CONTENT).hexdigest()
REF = f"sha256:{DIGEST}"


class _BlobHandler(BaseHTTPRequestHandler):
    content = CONTENT
    ignore_range = False
    fail_once_at = set()
    fail_always_at = set()
    attempts = {}
    ranges = []
    auths = []

    @classmethod
    def reset(cls, content=CONTENT):
        cls.content = content
        cls.ignore_range = False
        cls.fail_once_at = set()
        cls.fail_always_at = set()
        cls.attempts = {}
        cls.ranges = []
        cls.auths = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        cls = type(self)
        cls.auths.append(self.headers.get("Authorization"))
        body = cls.content
        rng = self.headers.get("Range")
        if rng and not cls.ignore_range:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            start, end = int(m.group(1)), int(m.group(2))
            cls.ranges.append((start, end))
            cls.attempts[start] = cls.attempts.get(start, 0) + 1
            if start in cls.fail_always_at or (
                    start in cls.fail_once_at
                    and cls.attempts[start] == 1):
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            chunk = body[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestBlobFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _BlobHandler)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _BlobHandler.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest_dir = Path(self.tmp.name)

    def fetch(self, ref=REF, size=len(CONTENT), **kw):
        kw.setdefault("sleep", lambda s: None)
        return OP.fetch_blob(ref, size, base_url=self.url,
                             token="test-token", dest_dir=self.dest_dir,
                             chunk_size=100, **kw)

    def test_chunked_fetch_verifies(self):
        info = self.fetch()
        self.assertTrue(info["verified"])
        self.assertEqual(info["path"],
                         str(self.dest_dir / DIGEST))
        self.assertEqual(Path(info["path"]).read_bytes(), CONTENT)
        starts = [s for s, _ in _BlobHandler.ranges]
        self.assertEqual(starts, list(range(0, len(CONTENT), 100)))
        self.assertTrue(all(e - s < 100 for s, e in _BlobHandler.ranges))
        self.assertIn("Bearer test-token", _BlobHandler.auths)

    def test_chunk_retry_within_call(self):
        _BlobHandler.fail_once_at = {100}
        info = self.fetch()
        self.assertTrue(info["verified"])
        self.assertEqual(_BlobHandler.attempts[100], 2)
        self.assertEqual(Path(info["path"]).read_bytes(), CONTENT)

    def test_resume_after_failure(self):
        _BlobHandler.fail_always_at = {200}
        with self.assertRaises(RuntimeError):
            self.fetch(max_attempts=2)
        partial = self.dest_dir / DIGEST
        self.assertTrue(partial.exists())
        self.assertEqual(partial.stat().st_size, 200)  # kept on disk
        _BlobHandler.fail_always_at = set()
        _BlobHandler.ranges = []
        info = self.fetch()
        self.assertTrue(info["verified"])
        self.assertEqual(_BlobHandler.ranges[0][0], 200)  # resumed
        self.assertEqual(Path(info["path"]).read_bytes(), CONTENT)

    def test_hash_mismatch_rejected(self):
        _BlobHandler.reset(content=b"x" * len(CONTENT))  # wrong bytes
        with self.assertRaises(RuntimeError) as cm:
            self.fetch()
        self.assertIn("mismatch", str(cm.exception).lower())
        self.assertFalse((self.dest_dir / DIGEST).exists())

    def test_server_ignoring_range_with_partial_on_disk(self):
        (self.dest_dir / DIGEST).write_bytes(CONTENT[:50])
        _BlobHandler.ignore_range = True
        info = self.fetch()
        self.assertTrue(info["verified"])
        self.assertEqual(Path(info["path"]).read_bytes(), CONTENT)

    def test_already_complete_short_circuits(self):
        (self.dest_dir / DIGEST).write_bytes(CONTENT)
        _BlobHandler.fail_always_at = set(range(0, len(CONTENT), 100))
        info = self.fetch()
        self.assertTrue(info["verified"])
        self.assertEqual(_BlobHandler.ranges, [])  # zero requests

    def test_bad_ref_rejected(self):
        with self.assertRaises(ValueError):
            OP.fetch_blob("sha256:nothex", 10, base_url=self.url,
                          token="t", dest_dir=self.dest_dir)


class TestMaterializeBlobs(unittest.TestCase):
    def test_nested_blob_gains_path_and_verified(self):
        seen = []

        def fake_fetch(ref, size):
            seen.append((ref, size))
            return {"path": "/tmp/blobs/" + "a" * 64, "size": size,
                    "sha256": "a" * 64, "verified": True}

        ref = "sha256:" + "a" * 64
        out = OP._materialize_blobs(
            {"shot": {"blob_ref": ref, "size": 3, "mime": "image/png",
                      "width": 9}},
            fetcher=fake_fetch)
        self.assertEqual(seen, [(ref, 3)])
        self.assertEqual(out["shot"]["path"], "/tmp/blobs/" + "a" * 64)
        self.assertTrue(out["shot"]["verified"])
        self.assertEqual(out["shot"]["width"], 9)  # other keys preserved

    def test_no_blob_passthrough(self):
        obj = {"a": [1, {"b": "c"}]}
        self.assertEqual(OP._materialize_blobs(obj), obj)


if __name__ == "__main__":
    unittest.main()
