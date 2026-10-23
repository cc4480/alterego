"""Client battery for bridge/op_call.py job mode (Phase 2): a stub MCP
HTTP server fronts a REAL JobManager, and the REAL op_call module
drives it — submit, poll, disconnect, resume-by-job_id, queue-full.

Includes the >62s proof: one job runs 63 seconds server-side while
the client disconnects and later resumes by job_id to collect the
finished result. Stdlib unittest (pytest-compatible); run with:
    python tests/test_client_jobs.py   (from pc-agent/)
"""
import http.client
import importlib.util
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PC_AGENT = Path(__file__).resolve().parent.parent
BRIDGE = PC_AGENT.parent / "bridge"
sys.path.insert(0, str(PC_AGENT))
os.environ.setdefault("APPDATA", "/tmp/pc-bridge-test-appdata")

import jobs  # noqa: E402

# ---------------------------------------------------------------------------
# Stub MCP server: just enough JSON-RPC/SSE for op_call.post(), routing
# the four job ops to a real JobManager.
# ---------------------------------------------------------------------------

MANAGER = None  # set by the test case


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method", "")
        rid = body.get("id", 1)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {},
                      "serverInfo": {"name": "stub", "version": "0"}}
        elif method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        elif method == "tools/call":
            name = body["params"]["name"]
            args = body["params"].get("arguments") or {}
            if name == "submit_job":
                payload = MANAGER.submit(args["tool"], args.get("args") or {},
                                         args.get("idempotency_key") or None)
            elif name == "job_status":
                payload = MANAGER.status(args["job_id"])
            elif name == "job_result":
                payload = MANAGER.result(args["job_id"])
            elif name == "job_cancel":
                payload = MANAGER.cancel(args["job_id"])
            else:  # pragma: no cover
                payload = {"error": f"stub: no tool {name}"}
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload)}]}
        else:  # pragma: no cover
            result = {}
        raw = ("data: " + json.dumps(
            {"jsonrpc": "2.0", "id": rid, "result": result}) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "stub-session")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _load_op_call(url):
    os.environ["PC_BRIDGE_URL"] = url
    sys.path.insert(0, str(BRIDGE))
    spec = importlib.util.spec_from_file_location(
        "op_call_under_test", BRIDGE / "op_call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TOKEN = "test-token"
    return mod


class ClientCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.op = _load_op_call(f"http://127.0.0.1:{cls.port}")
        cls.op.JOBS_FILE = Path(f"/tmp/client-jobs-{time.time_ns()}.json")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def make_manager(self, executor, **kw):
        global MANAGER
        MANAGER = jobs.JobManager(
            db_path=f"/tmp/client-jobs-{time.time_ns()}.db",
            executor=executor, watchdog_interval_s=0.2, **kw)
        self.addCleanup(MANAGER.shutdown)
        return MANAGER

    def session(self):
        _res, sid = self.op.post(
            "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "t", "version": "0"}}})
        return sid


class TestClassify(unittest.TestCase):
    def test_classes(self):
        op = _load_op_call("http://127.0.0.1:1")
        self.assertEqual(
            op.classify_transport_error(http.client.IncompleteRead(b"")),
            "truncated")
        self.assertEqual(
            op.classify_transport_error(
                urllib.error.URLError(ConnectionRefusedError("no"))),
            "refused")
        self.assertEqual(op.classify_transport_error(TimeoutError("t")),
                         "timeout")
        err = urllib.error.HTTPError("http://x", 503, "unavailable", {}, None)
        self.assertEqual(op.classify_transport_error(err), "server")


class TestSubmitPoll(ClientCase):
    def test_submit_poll_completes(self):
        self.make_manager(lambda t, a, c: {"echo": a})
        sid = self.session()
        out = self.op._submit("system_info", {"x": 1}, "cli-fast-1", sid)
        result = self.op._poll_until_done(out["job_id"], sid, 30)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["result"], {"echo": {"x": 1}})

    def test_queue_full_surfaces_as_refusal(self):
        gate = threading.Event()
        self.make_manager(lambda t, a, c: gate.wait(timeout=30),
                          max_workers=1, max_queued=0)
        sid = self.session()
        self.op._submit("t", {}, "cli-full-1", sid)
        deadline = time.time() + 10
        while time.time() < deadline:  # wait until it is running
            if MANAGER.status(
                    MANAGER.journal.get_by_key("cli-full-1")["job_id"]
                    )["state"] == "running":
                break
            time.sleep(0.05)
        with self.assertRaises(SystemExit) as cm:
            self.op._submit("t", {}, "cli-full-2", sid)
        self.assertIn("queue_full", str(cm.exception))
        gate.set()


class TestLongJobResume(ClientCase):
    def test_over_62s_job_survives_disconnect_and_resumes(self):
        """The battery's long-job proof: 63s of server-side work, the
        client walks away mid-run, then resume-by-job_id collects the
        finished result from the journal."""
        def slow(tool, args, ctx):
            time.sleep(63)
            return {"slept": 63}

        mgr = self.make_manager(slow, max_workers=2)
        sid = self.session()
        t0 = time.time()
        out = self.op._submit("slow_tool", {}, "cli-long-1", sid)
        job_id = out["job_id"]
        # client polls: the job leaves the queue and is running
        deadline = time.time() + 10
        st = self.op._tool_call("job_status", {"job_id": job_id}, sid)
        while st["state"] == "queued" and time.time() < deadline:
            time.sleep(0.2)
            st = self.op._tool_call("job_status", {"job_id": job_id}, sid)
        self.assertEqual(st["state"], "running")
        # --- disconnect: this client goes away entirely ---
        sid = None
        time.sleep(5)
        # --- a fresh client session resumes by job_id ---
        sid2 = self.session()
        result = self.op._poll_until_done(job_id, sid2, 180)
        elapsed = time.time() - t0
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["result"], {"slept": 63})
        self.assertGreater(elapsed, 62)
        # resume also works from the local record (jobs.json)
        rec = self.op._lookup_recorded(job_id=job_id)
        self.assertEqual(rec["job_id"], job_id)
        again = self.op._poll_until_done(rec["job_id"], sid2, 30)
        self.assertEqual(again["result"], {"slept": 63})
        self.assertEqual(mgr.status(job_id)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
