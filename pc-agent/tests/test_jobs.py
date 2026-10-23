"""Job layer battery (Hermes-pattern Phases 1+2): journal dedupe,
pool concurrency, watchdog fencing, admission bound, cancel, resource
serialization. Stdlib unittest (also pytest-compatible); run with:
    python -m unittest tests.test_jobs   (from pc-agent/)
or  python tests/test_jobs.py
"""
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("APPDATA", "/tmp/pc-bridge-test-appdata")

import jobs  # noqa: E402
import subproc  # noqa: E402


def wait_state(mgr, job_id, states, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = mgr.status(job_id)
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: "
                         f"{mgr.status(job_id)}")


class ManagerCase(unittest.TestCase):
    def make_manager(self, executor, **kw):
        kw.setdefault("watchdog_interval_s", 0.1)
        mgr = jobs.JobManager(
            db_path=f"/tmp/jobs-test-{self.id().split('.')[-1]}-"
                    f"{time.time_ns()}.db",
            executor=executor, **kw)
        self.addCleanup(mgr.shutdown)
        return mgr


class TestDedupe(ManagerCase):
    def test_same_key_executes_once(self):
        calls = []
        lock = threading.Lock()

        def counting(tool, args, ctx):
            with lock:
                calls.append(tool)
            return {"ok": True}

        mgr = self.make_manager(counting)
        r1 = mgr.submit("system_info", {}, "key-1")
        r2 = mgr.submit("system_info", {}, "key-1")
        self.assertEqual(r1["job_id"], r2["job_id"])
        self.assertFalse(r1["deduplicated"])
        self.assertTrue(r2["deduplicated"])
        wait_state(mgr, r1["job_id"], ("succeeded",))
        self.assertEqual(len(calls), 1)
        res = mgr.result(r1["job_id"])
        self.assertEqual(res["result"], {"ok": True})
        # a different key is a different job
        r3 = mgr.submit("system_info", {}, "key-2")
        self.assertNotEqual(r3["job_id"], r1["job_id"])
        wait_state(mgr, r3["job_id"], ("succeeded",))
        self.assertEqual(len(calls), 2)


class TestConcurrency(ManagerCase):
    def test_eight_way_parallel(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def barrier(tool, args, ctx):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 8:
                    release.set()
            release.wait(timeout=5)
            with lock:
                active -= 1
            return {"tool": tool}

        mgr = self.make_manager(barrier, max_workers=8)
        ids = [mgr.submit(f"t{i}", {}, f"conc-{i}")["job_id"]
               for i in range(8)]
        for jid in ids:
            wait_state(mgr, jid, ("succeeded",), timeout=10)
        self.assertEqual(peak, 8)


class TestWatchdog(ManagerCase):
    def test_stalled_job_killed_sibling_unaffected(self):
        def staller(tool, args, ctx):
            # A subprocess that never produces output and never ends:
            # runs through subproc, so it registers with this job.
            return subproc.run_noinherit(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout_s=120)

        def toucher(tool, args, ctx):
            for _ in range(12):
                ctx.touch()
                time.sleep(0.3)
            return {"done": True}

        def dispatch(tool, args, ctx):
            return (staller if tool == "stall" else toucher)(tool, args, ctx)

        mgr = self.make_manager(dispatch, max_workers=4,
                                stall_timeout_s=1.2)
        bad = mgr.submit("stall", {}, "wd-bad")["job_id"]
        good = mgr.submit("touch", {}, "wd-good")["job_id"]
        st = wait_state(mgr, bad, ("killed",), timeout=10)
        self.assertEqual(st["classification"], "no-progress")
        self.assertIn("watchdog", st["error"])
        wait_state(mgr, good, ("succeeded",), timeout=10)
        self.assertEqual(mgr.result(good)["result"], {"done": True})
        # the killed job's late executor return must not overwrite
        time.sleep(1.0)
        self.assertEqual(mgr.status(bad)["state"], "killed")


class TestAdmission(ManagerCase):
    def test_queue_full_clean_refusal(self):
        gate = threading.Event()

        def blocker(tool, args, ctx):
            gate.wait(timeout=10)
            return {"ok": True}

        mgr = self.make_manager(blocker, max_workers=1, max_queued=1)
        a = mgr.submit("t", {}, "adm-a")
        wait_state(mgr, a["job_id"], ("running",))
        b = mgr.submit("t", {}, "adm-b")
        self.assertEqual(b["state"], "queued")
        c = mgr.submit("t", {}, "adm-c")
        self.assertEqual(c.get("error"), "queue_full")
        # dedupe is checked before admission: resubmitting A still works
        a2 = mgr.submit("t", {}, "adm-a")
        self.assertEqual(a2["job_id"], a["job_id"])
        self.assertTrue(a2["deduplicated"])
        gate.set()
        wait_state(mgr, a["job_id"], ("succeeded",))
        wait_state(mgr, b["job_id"], ("succeeded",))


class TestCancel(ManagerCase):
    def test_cancel_fences_process_tree(self):
        started = threading.Event()
        holder = {}

        def sleeper(tool, args, ctx):
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"])
            holder["pid"] = proc.pid
            ctx.register_process(proc)
            started.set()
            proc.wait()
            ctx.unregister_process(proc)
            return {"rc": proc.returncode}

        mgr = self.make_manager(sleeper, max_workers=2)
        jid = mgr.submit("sleep", {}, "cx-1")["job_id"]
        wait_state(mgr, jid, ("running",))
        self.assertTrue(started.wait(timeout=5))
        out = mgr.cancel(jid)
        self.assertTrue(out["cancelled"])
        self.assertEqual(mgr.status(jid)["state"], "cancelled")
        # the registered child process is actually dead (fence worked)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(holder["pid"], 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.1)
        else:
            self.fail("cancelled job's process survived the fence")
        # late finish must not overwrite the cancelled state
        time.sleep(0.5)
        self.assertEqual(mgr.status(jid)["state"], "cancelled")


class TestResourceSerialization(ManagerCase):
    def test_desktop_jobs_serialize_others_parallel(self):
        intervals = {}
        lock = threading.Lock()

        def timed(tool, args, ctx):
            t0 = time.monotonic()
            time.sleep(0.6)
            with lock:
                intervals.setdefault(tool, []).append(
                    (t0, time.monotonic()))
            return {"ok": True}

        mgr = self.make_manager(
            timed, max_workers=4,
            resource_map={"desk": "desktop"})
        d1 = mgr.submit("desk", {}, "rs-d1")["job_id"]
        d2 = mgr.submit("desk", {}, "rs-d2")["job_id"]
        f1 = mgr.submit("free", {}, "rs-f1")["job_id"]
        for jid in (d1, d2, f1):
            wait_state(mgr, jid, ("succeeded",), timeout=10)
        (a0, a1), (b0, b1) = sorted(intervals["desk"])
        self.assertGreaterEqual(b0, a1 - 0.05,
                                "desktop jobs overlapped")
        (c0, _c1) = intervals["free"][0]
        self.assertLess(c0, a1 - 0.2,
                        "unrelated job did not run in parallel")


if __name__ == "__main__":
    unittest.main()
