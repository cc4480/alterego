"""Hermes-pattern job layer for the PC bridge (Phases 1+2, 2026-10-23).

Why this exists (measured 2026-10-23, see
goals/windows-pc-mcp-bridge/files/bridge-upgrade-hermes-2026-10-23.md):
legacy sync calls serialize behind each other, die at the ~62s transport
cap, and a transport retry can replay a whole script with no way to ask
"did it run?". The job layer fixes that structurally:

- submit_job returns a job_id immediately; execution happens here,
  whether or not the caller stays connected (journal-ACK intake).
- Every submission carries an idempotency key. A resubmission with the
  same key returns the existing job — never a second execution (durable
  per-input ownership marker, Hermes study 04).
- The journal (SQLite, next to the event log) holds started/finished
  receipts: "did it run?" is a lookup, not a guess.
- A worker pool (default 8) runs jobs concurrently; serialization is
  per shared resource only (interactive desktop, the single browser
  session) via a resource-lock map (per-session lease, study 01).
- A watchdog supervises PROGRESS (output growth / heartbeat touch),
  never wall-clock alone. A stalled job is classified (no-progress vs
  mid-work) and fenced: only its own registered process tree is killed
  (studies 02/03). Slowness is never treated as death without evidence.
- Admission is bounded: a full queue refuses new submissions cleanly
  and immediately instead of piling up (bounded spool, study 01).

Legacy sync ops are untouched: jobs execute the very same tool wrappers
(same toolcall pipeline, same approval dialogs) — just from a worker
thread instead of the caller's request.

Fencing note: Python threads cannot be force-killed, so the fence is
(a) the journal state transition (conditional — a fenced job's late
return can never overwrite its terminal state), (b) a cooperative
cancel event, and (c) killing every subprocess the job registered
(subproc.run_noinherit registers its Popen with the running job and
reports output growth while it waits). Pure-Python tool code that
hangs without subprocesses is marked killed in the journal and its
pool slot released; its daemon thread is abandoned, never joined.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import blob_store
import events

DEFAULT_MAX_WORKERS = 8
DEFAULT_MAX_QUEUED = 32
DEFAULT_STALL_TIMEOUT_S = 120.0
WATCHDOG_INTERVAL_S = 1.0

TERMINAL_STATES = ("succeeded", "failed", "killed", "cancelled")

# Tools that share one physical resource and must serialize. Everything
# not listed runs fully parallel. "desktop": the interactive desktop —
# screen, UIA tree, injected input, clipboard, window state are all one
# shared surface. "browser": the single Edge CDP session.
RESOURCE_MAP = {
    "screenshot": "desktop", "list_windows": "desktop",
    "active_window": "desktop", "uia_find": "desktop",
    "focus_window": "desktop", "close_window": "desktop",
    "minimize_window": "desktop", "maximize_window": "desktop",
    "mouse_move": "desktop", "mouse_click": "desktop",
    "mouse_scroll": "desktop", "hotkey": "desktop",
    "type_text": "desktop", "paste_text": "desktop",
    "uia_click": "desktop", "uia_set_text": "desktop",
    "clipboard_get": "desktop", "clipboard_set": "desktop",
    "notify": "desktop", "speak": "desktop",
    "browser_snapshot": "browser", "browser_navigate": "browser",
    "browser_click": "browser", "browser_fill": "browser",
    "browser_eval": "browser",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    idem_key       TEXT UNIQUE,
    tool           TEXT NOT NULL,
    args_json      TEXT NOT NULL,
    args_hash      TEXT NOT NULL,
    state          TEXT NOT NULL,
    submitted_ts   REAL NOT NULL,
    started_ts     REAL,
    finished_ts    REAL,
    progress_ts    REAL,
    output_bytes   INTEGER NOT NULL DEFAULT 0,
    classification TEXT,
    error          TEXT,
    result_json    TEXT,
    result_ref     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, submitted_ts);
"""


def _args_hash(args):
    return "sha256:" + hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _iso(ts):
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# ---------------------------------------------------------------------------
# Per-job runtime context (the fence + progress surface for executors)
# ---------------------------------------------------------------------------

_thread_ctx = threading.local()


def current_context():
    """The JobContext of the job running on THIS thread, or None.
    subproc.run_noinherit uses it to register its child process and to
    report output growth; tools may also call ctx.touch() directly."""
    return getattr(_thread_ctx, "ctx", None)


class JobContext:
    """Runtime state for one running job: progress signals + fencing."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._last_progress = time.monotonic()
        self._output_bytes = 0
        self._touches = 0
        self._procs = set()

    # -- progress signals (called by executors / subproc hook) --------
    def touch(self):
        with self._lock:
            self._touches += 1
            self._last_progress = time.monotonic()

    def report_output(self, total_bytes):
        """Report cumulative output size; growth counts as progress."""
        with self._lock:
            if total_bytes > self._output_bytes:
                self._output_bytes = total_bytes
                self._last_progress = time.monotonic()

    # -- process registry (the kill fence) -----------------------------
    def register_process(self, proc):
        with self._lock:
            self._procs.add(proc)

    def unregister_process(self, proc):
        with self._lock:
            self._procs.discard(proc)

    def kill_processes(self):
        """Kill every registered process tree. Fences ONLY this job."""
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _kill_tree(proc)

    # -- watchdog reads -------------------------------------------------
    def idle_s(self):
        with self._lock:
            return time.monotonic() - self._last_progress

    def snapshot(self):
        with self._lock:
            return {"output_bytes": self._output_bytes,
                    "touches": self._touches,
                    "last_progress_age_s": round(
                        time.monotonic() - self._last_progress, 3)}


def _kill_tree(proc):
    """Kill a Popen and its descendants. psutil (a pc-agent requirement)
    does the tree walk; without it, kill the direct child only."""
    try:
        import psutil
        try:
            parent = psutil.Process(proc.pid)
            victims = parent.children(recursive=True) + [parent]
        except psutil.NoSuchProcess:
            return
        for p in victims:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        return
    except ImportError:
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


# ---------------------------------------------------------------------------
# Journal (SQLite — the durable record with started/finished receipts)
# ---------------------------------------------------------------------------

class JobJournal:
    def __init__(self, db_path):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def insert_new(self, job_id, idem_key, tool, args):
        """Insert a queued job. Returns the row, or None if idem_key is
        already taken (the caller then returns the existing job)."""
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO jobs (job_id, idem_key, tool, args_json,"
                    " args_hash, state, submitted_ts, progress_ts)"
                    " VALUES (?,?,?,?,?,'queued',?,?)",
                    (job_id, idem_key, tool,
                     json.dumps(args, default=str), _args_hash(args),
                     now, now))
                self._conn.commit()
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return None
        return self.get(job_id)

    def get(self, job_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_by_key(self, idem_key):
        if not idem_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE idem_key=?", (idem_key,)).fetchone()
        return dict(row) if row else None

    def active_count(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM jobs"
                " WHERE state IN ('queued','running')").fetchone()
        return row["n"]

    def queued_jobs(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE state='queued'"
                " ORDER BY submitted_ts").fetchall()
        return [dict(r) for r in rows]

    def transition(self, job_id, from_state, to_state, **fields):
        """Conditional state transition — the fencing primitive. A late
        finisher can never overwrite a terminal state written by the
        watchdog or a cancel. Returns True if the transition happened."""
        sets = ", ".join(f"{k}=?" for k in fields)
        sql = f"UPDATE jobs SET state=?{', ' + sets if sets else ''}" \
              " WHERE job_id=? AND state=?"
        params = [to_state, *fields.values(), job_id, from_state]
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount == 1

    def set_progress(self, job_id, output_bytes):
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET progress_ts=?, output_bytes=?"
                " WHERE job_id=? AND state='running'",
                (time.time(), output_bytes, job_id))
            self._conn.commit()


# ---------------------------------------------------------------------------
# Manager: admission + dispatcher + watchdog
# ---------------------------------------------------------------------------

def _default_executor(tool, args, ctx):
    """Production executor: run the tool's own MCP wrapper — the same
    function a sync tools/call invokes, so the toolcall pipeline,
    approval dialogs, and semantics are identical. Resolved lazily so
    importing jobs never drags the tool layer in (tests inject fakes)."""
    import tool_wrappers
    fn = {f.__name__: f for f in tool_wrappers.ALL_TOOLS}.get(tool)
    if fn is None:
        raise KeyError(f"unknown tool: {tool}")
    return fn(**(args or {}))


class JobManager:
    def __init__(self, db_path=None, executor=None,
                 max_workers=DEFAULT_MAX_WORKERS,
                 max_queued=DEFAULT_MAX_QUEUED,
                 stall_timeout_s=DEFAULT_STALL_TIMEOUT_S,
                 watchdog_interval_s=WATCHDOG_INTERVAL_S,
                 resource_map=None):
        if db_path is None:
            from auth import app_dir
            db_path = app_dir() / "jobs.db"
        self.journal = JobJournal(db_path)
        self._executor = executor or _default_executor
        self.max_workers = max_workers
        self.max_queued = max_queued
        self.stall_timeout_s = stall_timeout_s
        self._watchdog_interval = watchdog_interval_s
        self.resource_map = dict(RESOURCE_MAP if resource_map is None
                                 else resource_map)
        self._resource_locks = {}
        self._resource_locks_guard = threading.Lock()
        self._contexts = {}          # job_id -> JobContext (running only)
        self._workers = {}           # job_id -> Thread
        self._guard = threading.Lock()
        self._stop = threading.Event()
        # Jobs left 'queued'/'running' by a dead server process are
        # orphaned: their executor died with it. Fail them honestly so
        # the journal never claims work is running when it is not.
        self._reap_orphans()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop, name="jobs-scheduler", daemon=True)
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="jobs-watchdog", daemon=True)
        self._scheduler.start()
        self._watchdog.start()

    # -- lifecycle ------------------------------------------------------
    def _reap_orphans(self):
        now = time.time()
        for row in self.journal.queued_jobs():
            self.journal.transition(
                row["job_id"], "queued", "failed", finished_ts=now,
                error="orphaned: server restarted before execution")
        with self.journal._lock:
            rows = self.journal._conn.execute(
                "SELECT job_id FROM jobs WHERE state='running'").fetchall()
        for r in rows:
            self.journal.transition(
                r["job_id"], "running", "failed", finished_ts=now,
                error="orphaned: server restarted during execution")

    def shutdown(self):
        self._stop.set()

    # -- submit ---------------------------------------------------------
    def submit(self, tool, args=None, idem_key=None):
        """Journal-ACK intake. Dedupe first (a resubmission is never
        refused and never re-executes), then the admission bound."""
        args = args or {}
        existing = self.journal.get_by_key(idem_key)
        if existing:
            return {"job_id": existing["job_id"], "state": existing["state"],
                    "deduplicated": True}
        if self.journal.active_count() >= self.max_workers + self.max_queued:
            return {"error": "queue_full",
                    "detail": f"admission bound reached "
                              f"({self.max_workers} running + "
                              f"{self.max_queued} queued); retry later",
                    "tool": tool}
        job_id = events.new_event_id("job_")
        row = self.journal.insert_new(job_id, idem_key, tool, args)
        if row is None:  # lost an insert race on the idempotency key
            existing = self.journal.get_by_key(idem_key)
            return {"job_id": existing["job_id"], "state": existing["state"],
                    "deduplicated": True}
        return {"job_id": job_id, "state": "queued", "deduplicated": False}

    # -- reads ------------------------------------------------------------
    def status(self, job_id):
        row = self.journal.get(job_id)
        if row is None:
            return {"error": "unknown_job", "job_id": job_id}
        out = {"job_id": row["job_id"], "tool": row["tool"],
               "state": row["state"],
               "args_hash": row["args_hash"],
               "submitted_at": _iso(row["submitted_ts"]),
               "started_at": _iso(row["started_ts"]),
               "finished_at": _iso(row["finished_ts"]),
               "output_bytes": row["output_bytes"],
               "classification": row["classification"],
               "error": row["error"]}
        ctx = self._contexts.get(job_id)
        if row["state"] == "running" and ctx is not None:
            out["progress"] = ctx.snapshot()
        return out

    def result(self, job_id):
        row = self.journal.get(job_id)
        if row is None:
            return {"error": "unknown_job", "job_id": job_id}
        out = {"job_id": row["job_id"], "tool": row["tool"],
               "state": row["state"], "error": row["error"],
               "classification": row["classification"]}
        if row["result_ref"]:
            raw = blob_store.get(row["result_ref"])
            if raw is None:
                out["result"] = None
                out["result_missing"] = row["result_ref"]
            else:
                try:
                    out["result"] = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    out["result"] = {"blob_ref": row["result_ref"],
                                     "size": len(raw)}
        elif row["result_json"] is not None:
            out["result"] = json.loads(row["result_json"])
        else:
            out["result"] = None
        return out

    # -- cancel -----------------------------------------------------------
    def cancel(self, job_id):
        row = self.journal.get(job_id)
        if row is None:
            return {"error": "unknown_job", "job_id": job_id}
        if row["state"] in TERMINAL_STATES:
            return {"job_id": job_id, "state": row["state"],
                    "cancelled": False}
        if row["state"] == "queued":
            done = self.journal.transition(
                job_id, "queued", "cancelled", finished_ts=time.time())
            return {"job_id": job_id, "state": "cancelled",
                    "cancelled": done}
        # running: cooperative cancel + fence its process tree
        ctx = self._contexts.get(job_id)
        if ctx is not None:
            ctx.cancel_event.set()
            ctx.kill_processes()
        done = self.journal.transition(
            job_id, "running", "cancelled", finished_ts=time.time())
        return {"job_id": job_id,
                "state": "cancelled" if done else row["state"],
                "cancelled": done}

    # -- dispatcher ---------------------------------------------------------
    def _resource_lock(self, resource):
        with self._resource_locks_guard:
            return self._resource_locks.setdefault(resource, threading.Lock())

    def _scheduler_loop(self):
        while not self._stop.is_set():
            try:
                self._dispatch()
            except Exception:  # noqa: BLE001 - scheduler must never die
                pass
            self._stop.wait(0.1)

    def _dispatch(self):
        with self._guard:
            self._workers = {j: t for j, t in self._workers.items()
                             if t.is_alive()}
            free = self.max_workers - len(self._workers)
            if free <= 0:
                return
            for row in self.journal.queued_jobs():
                if free <= 0:
                    break
                job_id = row["job_id"]
                if job_id in self._workers:
                    continue
                t = threading.Thread(
                    target=self._run_job, args=(row,),
                    name=f"job-{job_id[-6:]}", daemon=True)
                self._workers[job_id] = t
                t.start()
                free -= 1

    def _run_job(self, row):
        job_id = row["job_id"]
        tool = row["tool"]
        args = json.loads(row["args_json"])
        ctx = JobContext(job_id)
        resource = self.resource_map.get(tool)
        lock = self._resource_lock(resource) if resource else None
        acquired = False
        if lock is not None:
            # Wait for the shared resource, but stay cancellable.
            while not lock.acquire(timeout=0.2):
                cur = self.journal.get(job_id)
                if cur is None or cur["state"] != "queued":
                    return  # cancelled while waiting for the resource
            acquired = True
        try:
            if not self.journal.transition(
                    job_id, "queued", "running", started_ts=time.time()):
                return  # cancelled between dispatch and start
            with self._guard:
                self._contexts[job_id] = ctx
            _thread_ctx.ctx = ctx
            try:
                result = self._executor(tool, args, ctx)
                state, error = "succeeded", None
            except Exception as e:  # noqa: BLE001 - job carries the failure
                result, state, error = None, "failed", f"{type(e).__name__}: {e}"
            finally:
                _thread_ctx.ctx = None
            fields = {"finished_ts": time.time(),
                      "output_bytes": ctx.snapshot()["output_bytes"]}
            if state == "succeeded":
                payload = json.dumps(result, default=str)
                if len(payload) > blob_store.BLOB_THRESHOLD:
                    fields["result_ref"] = blob_store.put(payload)
                else:
                    fields["result_json"] = payload
            else:
                fields["error"] = error
            # Conditional: a watchdog kill / cancel already wrote the
            # terminal state — this late finish must not overwrite it.
            self.journal.transition(job_id, "running", state, **fields)
        finally:
            with self._guard:
                self._contexts.pop(job_id, None)
            if acquired:
                lock.release()

    # -- watchdog -----------------------------------------------------------
    def _watchdog_loop(self):
        while not self._stop.is_set():
            try:
                self._watchdog_tick()
            except Exception:  # noqa: BLE001 - watchdog must never die
                pass
            self._stop.wait(self._watchdog_interval)

    def _watchdog_tick(self):
        with self._guard:
            running = list(self._contexts.items())
        for job_id, ctx in running:
            # Progress is journaled too, so receipts survive restarts.
            self.journal.set_progress(job_id, ctx.snapshot()["output_bytes"])
            if ctx.idle_s() <= self.stall_timeout_s:
                continue
            snap = ctx.snapshot()
            classification = ("mid-work" if snap["output_bytes"] > 0
                              or snap["touches"] > 0 else "no-progress")
            fenced = self.journal.transition(
                job_id, "running", "killed",
                finished_ts=time.time(), classification=classification,
                error=f"watchdog: no progress for "
                      f"{int(ctx.idle_s())}s (stall budget "
                      f"{int(self.stall_timeout_s)}s)")
            if fenced:
                ctx.cancel_event.set()
                ctx.kill_processes()


# ---------------------------------------------------------------------------
# Module singleton + the four ops (server.py registers these as MCP tools)
# ---------------------------------------------------------------------------

_manager = None
_manager_lock = threading.Lock()


def get_manager():
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                db = os.environ.get("PC_BRIDGE_JOBS_DB")
                _manager = JobManager(db_path=db or None)
    return _manager


def submit_job_op(tool, args=None, idempotency_key=None):
    """submit_job: journal-ACK intake. Returns job_id immediately;
    a resubmission with the same idempotency_key returns the existing
    job and never executes twice."""
    return get_manager().submit(tool, args or {}, idempotency_key or None)


def job_status_op(job_id):
    return get_manager().status(job_id)


def job_result_op(job_id):
    return get_manager().result(job_id)


def job_cancel_op(job_id):
    return get_manager().cancel(job_id)
