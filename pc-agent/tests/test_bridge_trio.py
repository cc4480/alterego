"""Bridge trio battery (2026-10-23 cherry-pick execution): ESTOP
sentinel, intent lineage, doctor op. Stdlib unittest (also
pytest-compatible); run with:
    python -m unittest tests.test_bridge_trio   (from pc-agent/)
or  python tests/test_bridge_trio.py
"""
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("APPDATA", "/tmp/pc-bridge-test-appdata")

import jobs  # noqa: E402


def wait_state(mgr, job_id, states, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = mgr.status(job_id)
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: "
                         f"{mgr.status(job_id)}")


class TrioCase(unittest.TestCase):
    def make_manager(self, executor, **kw):
        kw.setdefault("watchdog_interval_s", 0.1)
        d = Path(tempfile.mkdtemp(prefix="jobs-trio-"))
        mgr = jobs.JobManager(db_path=str(d / "jobs.db"),
                              executor=executor, **kw)
        self.addCleanup(mgr.shutdown)
        self.addCleanup(shutil.rmtree, d, True)
        return mgr


class TestEstop(TrioCase):
    def test_paused_job_waits_then_resumes_on_clear(self):
        calls = []

        def counting(tool, args, ctx):
            calls.append(tool)
            return {"ok": True}

        mgr = self.make_manager(counting)
        mgr.estop_path.write_text(json.dumps(
            {"reason": "operator hold", "engaged_at": "2026-10-23T00:00:00Z"}),
            encoding="utf-8")
        # submissions are still journaled + ACKed while ESTOP is engaged
        r = mgr.submit("system_info", {}, "es-1")
        self.assertEqual(r["state"], "queued")
        time.sleep(0.6)  # several scheduler ticks: nothing may start
        st = mgr.status(r["job_id"])
        self.assertEqual(st["state"], "queued")
        self.assertTrue(st["paused"])
        self.assertIn("operator hold", st["paused_reason"])
        self.assertEqual(calls, [])
        # removing the sentinel resumes execution automatically
        mgr.estop_path.unlink()
        st = wait_state(mgr, r["job_id"], ("succeeded",))
        self.assertEqual(calls, ["system_info"])
        self.assertNotIn("paused", st)  # terminal jobs carry no pause

    def test_empty_sentinel_still_engages(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        mgr.estop_path.write_text("", encoding="utf-8")  # fail-safe
        r = mgr.submit("system_info", {}, "es-empty")
        time.sleep(0.5)
        st = mgr.status(r["job_id"])
        self.assertEqual(st["state"], "queued")
        self.assertTrue(st["paused"])
        mgr.estop_path.unlink()
        wait_state(mgr, r["job_id"], ("succeeded",))

    def test_inflight_never_interrupted(self):
        gate = threading.Event()

        def dispatch(tool, args, ctx):
            if tool == "block":
                gate.wait(timeout=15)
            return {"ok": True}

        mgr = self.make_manager(dispatch, max_workers=2)
        first = mgr.submit("block", {}, "es-first")["job_id"]
        wait_state(mgr, first, ("running",))
        mgr.estop_path.write_text("hold", encoding="utf-8")
        second = mgr.submit("fast", {}, "es-second")["job_id"]
        time.sleep(0.5)
        self.assertEqual(mgr.status(second)["state"], "queued")
        # the in-flight job runs to completion despite the sentinel
        gate.set()
        wait_state(mgr, first, ("succeeded",))
        time.sleep(0.3)
        self.assertEqual(mgr.status(second)["state"], "queued")
        mgr.estop_path.unlink()
        wait_state(mgr, second, ("succeeded",))


class TestLineage(TrioCase):
    def test_lineage_round_trips(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        r = mgr.submit("system_info", {}, "lin-1", lineage="relay")
        self.assertEqual(r["lineage"], "relay")
        wait_state(mgr, r["job_id"], ("succeeded",))
        self.assertEqual(mgr.status(r["job_id"])["lineage"], "relay")
        self.assertEqual(mgr.result(r["job_id"])["lineage"], "relay")

    def test_default_and_unknown_normalize(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        r1 = mgr.submit("t", {}, "lin-def")
        self.assertEqual(r1["lineage"], "unattributed")
        r2 = mgr.submit("t", {}, "lin-bogus", lineage="carrier-pigeon")
        self.assertEqual(r2["lineage"], "unattributed")
        r3 = mgr.submit("t", {}, "lin-case", lineage="Typed")
        self.assertEqual(r3["lineage"], "typed")
        for r in (r1, r2, r3):
            wait_state(mgr, r["job_id"], ("succeeded",))

    def test_dedupe_keeps_original_lineage(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        r1 = mgr.submit("t", {}, "lin-dup", lineage="typed")
        r2 = mgr.submit("t", {}, "lin-dup", lineage="relay")
        self.assertTrue(r2["deduplicated"])
        self.assertEqual(r2["job_id"], r1["job_id"])
        # the journaled record is the provenance; first writer wins
        self.assertEqual(r2["lineage"], "typed")
        wait_state(mgr, r1["job_id"], ("succeeded",))

    def test_pre_lineage_journal_migrates(self):
        d = Path(tempfile.mkdtemp(prefix="jobs-trio-old-"))
        self.addCleanup(shutil.rmtree, d, True)
        db = d / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE,
                tool TEXT NOT NULL, args_json TEXT NOT NULL,
                args_hash TEXT NOT NULL, state TEXT NOT NULL,
                submitted_ts REAL NOT NULL, started_ts REAL,
                finished_ts REAL, progress_ts REAL,
                output_bytes INTEGER NOT NULL DEFAULT 0,
                classification TEXT, error TEXT,
                result_json TEXT, result_ref TEXT);
        """)
        conn.commit()
        conn.close()
        journal = jobs.JobJournal(db)
        row = journal.insert_new("job_x", "k-x", "t", {},
                                 lineage="scheduled")
        self.assertEqual(row["lineage"], "scheduled")


class TestDoctor(TrioCase):
    COMPONENTS = {"listener", "tunnel", "token", "journal", "estop"}

    def test_shape_and_component_contract(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        rep = jobs.doctor(mgr)
        self.assertEqual(set(rep["components"]), self.COMPONENTS)
        for name, comp in rep["components"].items():
            self.assertEqual(set(comp), {"ok", "detail", "fix"}, name)
            self.assertIsInstance(comp["ok"], bool, name)
            self.assertTrue(comp["detail"], name)
            if comp["ok"]:
                self.assertIsNone(comp["fix"], name)
            else:
                self.assertIsInstance(comp["fix"], str, name)
                self.assertTrue(comp["fix"], name)
        self.assertEqual(rep["ok"],
                         all(c["ok"] for c in rep["components"].values()))
        # a live manager's journal always reports healthy
        self.assertTrue(rep["components"]["journal"]["ok"])

    def test_estop_component_carries_fix(self):
        mgr = self.make_manager(lambda t, a, c: {"ok": True})
        rep = jobs.doctor(mgr)
        self.assertTrue(rep["components"]["estop"]["ok"])
        mgr.estop_path.write_text('{"reason": "maintenance"}',
                                  encoding="utf-8")
        rep = jobs.doctor(mgr)
        comp = rep["components"]["estop"]
        self.assertFalse(comp["ok"])
        self.assertIn("maintenance", comp["detail"])
        self.assertIn(str(mgr.estop_path), comp["fix"])
        self.assertFalse(rep["ok"])
        mgr.estop_path.unlink()
        self.assertTrue(jobs.doctor(mgr)["components"]["estop"]["ok"])

    def test_token_helper(self):
        d = Path(tempfile.mkdtemp(prefix="jobs-trio-tok-"))
        self.addCleanup(shutil.rmtree, d, True)
        missing = jobs._check_token(d / "session_token")
        self.assertFalse(missing["ok"])
        self.assertIn("pair", missing["fix"])
        f = d / "session_token"
        f.write_text('["tok-a", "tok-b"]\n', encoding="utf-8")
        ok = jobs._check_token(f)
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["fix"])
        f.write_text("bare-legacy-token\n", encoding="utf-8")
        self.assertTrue(jobs._check_token(f)["ok"])

    def test_listener_helper(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        down = jobs._check_listener("127.0.0.1", dead_port)
        self.assertFalse(down["ok"])
        self.assertTrue(down["fix"])
        live = socket.socket()
        live.bind(("127.0.0.1", 0))
        live.listen(1)
        self.addCleanup(live.close)
        up = jobs._check_listener("127.0.0.1", live.getsockname()[1])
        self.assertTrue(up["ok"])
        self.assertIsNone(up["fix"])


if __name__ == "__main__":
    unittest.main()
