"""Per-client session attribution (multi-client operation).

Two paired clients must get distinct session ids in the event log, and
the journal must remember who submitted each job so background
execution attributes to the submitter. Mock provider, no server, no
network — agent-agnostic.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import events
import jobs
import toolcall


def _fp(token):
    return "sess_" + hashlib.sha256(token.encode()).hexdigest()[:12]


def _write_tokens(tmp_path, tokens):
    """Seed the session store. conftest already redirects APPDATA at a
    fresh tmp dir per test, and the store resolves it on every call."""
    p = Path(os.environ["APPDATA"]) / "pc-mcp-bridge" / "session_token"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tokens) + "\n", encoding="utf-8")
    return p


def test_presenting_token_preferred_over_shared_file(tmp_path):
    p = _write_tokens(tmp_path, ["tok-A", "tok-B"])
    raw = p.read_text(encoding="utf-8").strip()
    legacy = "sess_" + hashlib.sha256(raw.encode()).hexdigest()[:12]
    # Guard: the test is vacuous if the legacy hash collides.
    assert legacy != _fp("tok-A") and legacy != _fp("tok-B")
    t1 = toolcall.set_request_session_token("tok-A")
    try:
        assert toolcall._session_id() == _fp("tok-A")
        t2 = toolcall.set_request_session_token("tok-B")
        try:
            assert toolcall._session_id() == _fp("tok-B")
        finally:
            toolcall.reset_request_session(t2)
        assert toolcall._session_id() == _fp("tok-A")
    finally:
        toolcall.reset_request_session(t1)
    assert toolcall._session_id() == legacy  # unset -> legacy fallback


def test_fallback_none_when_unpaired(tmp_path):
    assert toolcall.get_request_session() is None
    assert toolcall._session_id() is None


def test_fingerprint_chains_to_pairing_event():
    token = "abc123-token"
    sid, data = events.new_session(token)
    assert sid == toolcall.fingerprint_token(token)
    assert data["token_fingerprint"] == \
        "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def test_submit_records_submitter_and_worker_restores_it(tmp_path):
    seen = []

    def rec(tool, args, ctx):
        seen.append(toolcall.get_request_session())
        return {"ok": True}

    mgr = jobs.JobManager(db_path=str(tmp_path / "jobs.db"),
                          executor=rec, watchdog_interval_s=0.1)
    try:
        tok = toolcall.set_request_session_token("tok-A")
        try:
            r = mgr.submit("system_info", {}, "attr-key-1")
        finally:
            toolcall.reset_request_session(tok)
        row = mgr.journal.get(r["job_id"])
        assert row["submitted_by"] == _fp("tok-A")
        deadline = time.time() + 15
        st = mgr.status(r["job_id"])
        while st["state"] not in ("succeeded", "failed", "killed",
                                  "cancelled") \
                and time.time() < deadline:
            time.sleep(0.05)
            st = mgr.status(r["job_id"])
        assert st["state"] == "succeeded", st
        assert st["submitted_by"] == _fp("tok-A")
        assert mgr.result(r["job_id"])["submitted_by"] == _fp("tok-A")
        assert seen == [_fp("tok-A")]
    finally:
        mgr.shutdown()


def test_pre_migration_journal_gains_column(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY,"
        " idem_key TEXT UNIQUE, tool TEXT NOT NULL,"
        " args_json TEXT NOT NULL, args_hash TEXT NOT NULL,"
        " state TEXT NOT NULL,"
        " lineage TEXT NOT NULL DEFAULT 'unattributed',"
        " submitted_ts REAL NOT NULL, started_ts REAL,"
        " finished_ts REAL, progress_ts REAL,"
        " output_bytes INTEGER NOT NULL DEFAULT 0,"
        " classification TEXT, error TEXT, result_json TEXT,"
        " result_ref TEXT)")
    conn.commit()
    conn.close()
    journal = jobs.JobJournal(db)
    row = journal.insert_new("job_old", "k-old", "system_info", {},
                             submitted_by=None)
    assert row["job_id"] == "job_old"
    assert row.get("submitted_by") is None
