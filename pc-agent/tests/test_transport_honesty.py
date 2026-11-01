"""Transport-honesty battery (F3, 2026-10-31): the loss points where a
truncated, dropped, or multi-event response could reach the caller
looking like an intact success — and the proofs that they no longer do.

- op_call.post(): an SSE stream carrying notification/priming events
  around the real response must yield THE RESPONSE; a stream with
  events but no response must fail loudly, never return empty success.
- op_call print sites: the 4000-char display cap announces itself
  (printed/total chars + where the full payload lives), and every
  capped site stashes the full payload first — including job-result
  and failed-job prints, which previously stashed nothing.
- blob_store.get(): content-addressed reads verify the hash — a
  corrupted blob reads as missing, and jobs.result() surfaces that
  as result_missing instead of handing back damaged content.
- Chunked write_file transfer (the big-payload path): gzip -> base64
  -> part files via write_file -> read_file sha256 per part ->
  assemble -> gunzip reproduces the original SHA-256 exactly, and
  write_file is byte-exact (CRLF/trailing newline/unicode survive).

Stdlib unittest (pytest-compatible); run with:
    python tests/test_transport_honesty.py   (from pc-agent/)
"""
import base64
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PC_AGENT = Path(__file__).resolve().parent.parent
BRIDGE = PC_AGENT.parent / "bridge"
sys.path.insert(0, str(PC_AGENT))

_TMP = tempfile.mkdtemp(prefix="transport-honesty-")
os.environ["APPDATA"] = _TMP
os.environ["USERPROFILE"] = _TMP
os.environ.setdefault("PC_BRIDGE_PERMISSION_MODE", "dontAsk")

import blob_store  # noqa: E402
import jobs  # noqa: E402

blob_store.app_dir = lambda: Path(_TMP) / "app"  # noqa: E731


def _load_op_call(url):
    os.environ["PC_BRIDGE_URL"] = url
    sys.path.insert(0, str(BRIDGE))
    spec = importlib.util.spec_from_file_location(
        "op_call_honesty", BRIDGE / "op_call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TOKEN = "test-token"
    return mod


# ---------------------------------------------------------------------------
# Stub MCP server with selectable response-stream shapes
# ---------------------------------------------------------------------------

def _sse(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


class _ShapeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method", "")
        rid = body.get("id", 1)
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if method == "initialize":
            raw = _sse([{"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "stub", "version": "0"}}}])
            ctype = "text/event-stream"
        else:
            name = (body.get("params") or {}).get("name", "")
            result = {"content": [{"type": "text",
                                   "text": json.dumps({"ok": name})}]}
            if name == "multi_event":
                # notification, an empty priming event, then the answer
                raw = _sse([
                    {"jsonrpc": "2.0", "method": "notifications/progress",
                     "params": {"progress": 1}},
                    {"jsonrpc": "2.0", "id": rid, "result": result},
                ])
                ctype = "text/event-stream"
            elif name == "notif_only":
                raw = _sse([
                    {"jsonrpc": "2.0", "method": "notifications/progress",
                     "params": {"progress": 1}},
                ])
                ctype = "text/event-stream"
            elif name == "plain_json":
                raw = json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "result": result}).encode()
                ctype = "application/json"
            else:  # single_event baseline (what the live server sends)
                raw = _sse([{"jsonrpc": "2.0", "id": rid, "result": result}])
                ctype = "text/event-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", "stub-session")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestResponseSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ShapeHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.op = _load_op_call(f"http://127.0.0.1:{cls.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, name):
        res, _sid = self.op.post(
            "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": name, "arguments": {}}})
        return res

    def test_single_event_baseline(self):
        res = self.call("single_event")
        self.assertEqual(json.loads(res["content"][0]["text"]),
                         {"ok": "single_event"})

    def test_multi_event_returns_the_response_not_the_notification(self):
        res = self.call("multi_event")
        self.assertEqual(json.loads(res["content"][0]["text"]),
                         {"ok": "multi_event"})

    def test_plain_json_response(self):
        res = self.call("plain_json")
        self.assertEqual(json.loads(res["content"][0]["text"]),
                         {"ok": "plain_json"})

    def test_events_without_response_fail_loudly(self):
        with self.assertRaises(RuntimeError) as cm:
            self.call("notif_only")
        self.assertIn("no JSON-RPC", str(cm.exception))


# ---------------------------------------------------------------------------
# Print-cap honesty
# ---------------------------------------------------------------------------

class TestPrintCapped(unittest.TestCase):
    def setUp(self):
        self.op = _load_op_call("http://127.0.0.1:1")
        self.stash = Path(_TMP) / f"stash-{time.time_ns()}.json"
        self.op.FULL_OUT = str(self.stash)

    def run_capped(self, text):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self.op._print_capped(text)
        return out.getvalue(), err.getvalue()

    def test_short_text_prints_clean(self):
        out, err = self.run_capped("hello")
        self.assertEqual(out, "hello\n")
        self.assertEqual(err, "")

    def test_capped_text_announces_itself(self):
        text = "x" * 5000
        out, err = self.run_capped(text)
        self.assertEqual(out, "x" * 4000 + "\n")
        self.assertIn("printed 4000 of 5000 chars", err)
        self.assertIn(str(self.stash), err)

    def test_failed_job_result_is_stashed_and_announced(self):
        payload = {"state": "failed", "error": "boom",
                   "detail": "y" * 5000}
        out_s, err_s = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_s), \
                contextlib.redirect_stderr(err_s):
            ok = self.op._print_job_result(payload)
        self.assertFalse(ok)
        full = json.dumps(payload, indent=1, default=str)
        self.assertEqual(self.stash.read_text(encoding="utf-8"), full)
        self.assertIn(f"of {len(full)} chars", err_s.getvalue())

    def test_succeeded_job_result_still_stashed(self):
        payload = {"state": "succeeded", "result": {"v": "z" * 5000}}
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            ok = self.op._print_job_result(payload)
        self.assertTrue(ok)
        full = json.dumps(payload["result"], indent=1, default=str)
        self.assertEqual(self.stash.read_text(encoding="utf-8"), full)


# ---------------------------------------------------------------------------
# Blob integrity through the job layer
# ---------------------------------------------------------------------------

class TestBlobIntegrity(unittest.TestCase):
    def test_corrupt_blob_reads_as_missing(self):
        ref = blob_store.put("integrity probe payload")
        self.assertEqual(blob_store.get(ref), b"integrity probe payload")
        path = blob_store._blob_path(ref.split(":", 1)[1])
        data = bytearray(path.read_bytes())
        data[0] ^= 0xFF
        path.write_bytes(bytes(data))
        self.assertIsNone(blob_store.get(ref))

    def test_job_result_reports_missing_blob(self):
        big = "q" * 5000  # json payload > BLOB_THRESHOLD -> blob path
        mgr = jobs.JobManager(
            db_path=str(Path(_TMP) / f"jobs-{time.time_ns()}.db"),
            executor=lambda t, a, c: big, watchdog_interval_s=0.2)
        self.addCleanup(mgr.shutdown)
        job_id = mgr.submit("big_tool", {}, f"honesty-{time.time_ns()}")[
            "job_id"]
        deadline = time.time() + 15
        while time.time() < deadline:
            if mgr.status(job_id)["state"] == "succeeded":
                break
            time.sleep(0.05)
        out = mgr.result(job_id)
        self.assertEqual(out["result"], big)
        row = mgr.journal.get(job_id)
        self.assertTrue(row["result_ref"])
        path = blob_store._blob_path(row["result_ref"].split(":", 1)[1])
        path.write_bytes(b"truncated garbage")
        out = mgr.result(job_id)
        self.assertIsNone(out["result"])
        self.assertEqual(out["result_missing"], row["result_ref"])


# ---------------------------------------------------------------------------
# Chunked write_file transfer: gzip -> base64 -> parts -> assemble
# ---------------------------------------------------------------------------

class TestChunkedTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from seams.providers.windows import files as wfiles
        from seams.providers.windows import read as wread
        cls.wfiles = wfiles
        cls.wread = wread
        cls.dir = Path(_TMP) / "transfer"
        cls.dir.mkdir(parents=True, exist_ok=True)

    def test_gzip_base64_parts_roundtrip_sha256(self):
        import random
        # Incompressible on purpose: a repetitive payload would gzip
        # down to a single part and prove nothing about chunking.
        payload = random.Random(20261031).randbytes(230_400)
        want = hashlib.sha256(payload).hexdigest()
        blob = base64.b64encode(gzip.compress(payload)).decode("ascii")
        parts = [blob[i:i + 30000] for i in range(0, len(blob), 30000)]
        self.assertGreater(len(parts), 3)
        assembled = ""
        for i, part in enumerate(parts):
            p = self.dir / f"payload.part{i:02d}"
            res = self.wfiles.write_file(str(p), part)
            self.assertEqual(res["bytes_written"],
                             len(part.encode("utf-8")))
            back = self.wread.read_file(str(p))
            self.assertEqual(
                back["sha256"],
                hashlib.sha256(part.encode("utf-8")).hexdigest())
            assembled += back["content"]
        self.assertEqual(assembled, blob)
        restored = gzip.decompress(base64.b64decode(assembled))
        self.assertEqual(hashlib.sha256(restored).hexdigest(), want)

    def test_write_file_is_byte_exact(self):
        content = "line1\r\nline2\nunicode: ☃ é\ntrailing newline next\n"
        p = self.dir / "exact.txt"
        self.wfiles.write_file(str(p), content)
        self.assertEqual(p.read_bytes(), content.encode("utf-8"))
        back = self.wread.read_file(str(p))
        self.assertEqual(back["content"], content)
        self.assertEqual(
            back["sha256"],
            hashlib.sha256(content.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
