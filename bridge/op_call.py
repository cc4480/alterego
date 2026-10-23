#!/usr/bin/env python3
"""Durable operator helper for pc-mcp-bridge (stdlib only).

Reads PC_BRIDGE_URL and PC_BRIDGE_TOKEN from the environment — the token
is never stored in this file. Pairing saves the session token via
token_store.py (~/.config/pc-bridge/token, 0600); it survives VM restarts
and is cleared automatically on 401. Re-pair only when the server rejects
the stored token.

Usage:
  PC_BRIDGE_URL=... python3 op_call.py pair <6-digit-code>
  PC_BRIDGE_URL=... python3 op_call.py health
  PC_BRIDGE_URL=... python3 op_call.py tools
  PC_BRIDGE_URL=... python3 op_call.py call memory_recall '{"query":"x"}'
  PC_BRIDGE_URL=... python3 op_call.py submit shell_pwsh '{"script":"..."}' [--key K]
  PC_BRIDGE_URL=... python3 op_call.py calljob shell_pwsh '{"script":"..."}' [--key K] [--timeout S]
  PC_BRIDGE_URL=... python3 op_call.py job <job_id>          (status)
  PC_BRIDGE_URL=... python3 op_call.py job-result <job_id>
  PC_BRIDGE_URL=... python3 op_call.py job-cancel <job_id>
  PC_BRIDGE_URL=... python3 op_call.py resume [job_id|--key K]
  PC_BRIDGE_URL=... python3 op_call.py logout

Job mode (submit/calljob) is the Hermes-pattern path: the tool is
submitted ONCE under an idempotency key and executes server-side;
the client then only polls job_status/job_result by job_id. A dropped
connection loses nothing — `resume` picks the job back up by id.
"""
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import http.client

import token_store

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = token_store.resolve_token()  # None until paired; pair cmd handles it

# Transient transport failures worth one more try: the Cloudflare tunnel
# flaps for a minute or two at a time (home-network micro-outages), and
# most of these mean the request never completed. NOT retried: HTTP
# errors (the server answered) — including 401, which clears the token.
# Caveat: a retry after a lost *response* can double-execute a write;
# callers driving writes should verify state after a retried call.
_RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError,
              http.client.RemoteDisconnected, http.client.IncompleteRead)
_RETRY_BACKOFF_S = (5, 15)


def post(path, body, session_id=None):
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # The named tunnel sits behind the user's own Cloudflare zone,
            # whose firewall 403s non-browser User-Agents (urllib included).
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0.0.0 Safari/537.36",
            "Authorization": f"Bearer {TOKEN}",
            **({"Mcp-Session-Id": session_id} if session_id else {}),
        },
        method="POST",
    )
    last = None
    for attempt in range(1 + len(_RETRY_BACKOFF_S)):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode()
                sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
            break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                token_store.clear_token()
                raise SystemExit(
                    "session token rejected (401) — cleared; re-pair: "
                    "python3 op_call.py pair <6-digit-code>")
            raise
        except _RETRYABLE as e:
            last = e
            if attempt < len(_RETRY_BACKOFF_S):
                wait = _RETRY_BACKOFF_S[attempt]
                print(f"[retry {attempt + 1}] {type(e).__name__} — "
                      f"waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    else:
        raise last  # pragma: no cover - loop always breaks or raises
    payload = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            break
    msg = json.loads(payload) if payload else {}
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg.get("result"), sid


# ---- job client: submit+poll with resume-by-job_id -------------------
# Per-class transport policy for the poll loop (classify-before-retry,
# never blind resubmission): the submission itself is idempotent by
# key, so retrying the submit_job op is safe; after that, every retry
# is a READ against the journal. The tool script is never re-sent.

JOBS_FILE = token_store.TOKEN_FILE.parent / "jobs.json"
_TERMINAL = ("succeeded", "failed", "killed", "cancelled")
_POLL_S = 2.0


def classify_transport_error(e):
    """Bucket a transport failure: timeout | refused | truncated |
    server | http | unknown. Drives the poll loop's wait strategy."""
    if isinstance(e, urllib.error.HTTPError):
        return "server" if e.code >= 500 else "http"
    if isinstance(e, (http.client.IncompleteRead,
                      http.client.RemoteDisconnected)):
        return "truncated"
    reason = getattr(e, "reason", None) or e
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, (TimeoutError, socket.timeout)) or \
            isinstance(e, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(e, urllib.error.URLError):
        return "refused" if "refused" in str(e).lower() else "unknown"
    return "unknown"


def _tool_call(name, args, sid):
    res, _ = post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": name, "arguments": args}}, sid)
    text = res["content"][0]["text"]
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


def _default_key(tool, args):
    """Content-derived idempotency key: re-running the identical
    calljob returns the existing job instead of executing twice.
    Pass --key with a fresh value for an intentional re-run."""
    canon = json.dumps({"tool": tool, "args": args},
                       sort_keys=True, default=str)
    return "op-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _record_job(key, job_id, tool):
    """Local resume record (~/.config/pc-bridge/jobs.json). A crashed
    or disconnected client finds its jobs here. Best-effort only."""
    try:
        data = {}
        if JOBS_FILE.exists():
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        entry = {"job_id": job_id, "tool": tool}
        if key:
            data[key] = entry
        data["_last"] = dict(entry, key=key)
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOBS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def _lookup_recorded(key=None, job_id=None):
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if job_id:
        return {"job_id": job_id}
    if key:
        return data.get(key)
    return data.get("_last")


def _submit(tool, args, key, sid):
    out = _tool_call("submit_job",
                     {"tool": tool, "args": args,
                      "idempotency_key": key}, sid)
    if "error" in out:
        raise SystemExit(f"submit refused: {out['error']} — "
                           f"{out.get('detail', '')}")
    _record_job(key, out["job_id"], tool)
    return out


def _poll_until_done(job_id, sid, timeout_s):
    """Poll job_status until terminal, then fetch job_result. Every
    poll is a fresh short request — the polling activity itself keeps
    the tunnel warm, so no long-held connection exists to be reaped.
    Transport failures are classified; the wait always resumes by
    job_id and never resubmits the tool."""
    deadline = time.time() + timeout_s
    while True:
        try:
            st = _tool_call("job_status", {"job_id": job_id}, sid)
        except Exception as e:
            cls = classify_transport_error(e)
            wait = 30 if cls in ("refused", "server") else 10
            print(f"[poll] transport {cls} ({type(e).__name__}) — job "
                  f"{job_id} is server-side and unaffected; resuming "
                  f"by job_id in {wait}s", file=sys.stderr)
            if time.time() + wait > deadline:
                raise SystemExit(
                    f"job {job_id}: poll budget exhausted during a "
                    f"{cls} outage — the job keeps running server-side; "
                    f"resume with: op_call.py resume {job_id}")
            time.sleep(wait)
            continue
        if st.get("error"):
            raise SystemExit(f"job_status failed: {st['error']}")
        if st["state"] in _TERMINAL:
            return _tool_call("job_result", {"job_id": job_id}, sid)
        if time.time() > deadline:
            raise SystemExit(
                f"job {job_id} still {st['state']} after {timeout_s}s — "
                f"it keeps running server-side; resume with: "
                f"op_call.py resume {job_id}")
        time.sleep(_POLL_S)


def _print_job_result(out):
    """Print a job_result payload like `call` prints a tool result.
    Returns True iff the job succeeded."""
    state = out.get("state")
    if state == "succeeded":
        text = json.dumps(out.get("result"), indent=1, default=str)
        with open("/tmp/op-full.json", "w", encoding="utf-8") as f:
            f.write(text)
        print(text[:4000])
        return True
    print(json.dumps(out, indent=1, default=str)[:4000])
    return False


def _parse_flags(argv, allowed):
    """Split trailing --flag value pairs off argv for job commands."""
    flags, rest = {}, []
    i = 0
    while i < len(argv):
        if argv[i] in allowed and i + 1 < len(argv):
            flags[argv[i]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return flags, rest


def main():
    cmd = sys.argv[1]
    if cmd == "pair":
        code = sys.argv[2] if len(sys.argv) > 2 else ""
        if not code.isdigit():
            raise SystemExit("usage: op_call.py pair <6-digit-code>")
        req = urllib.request.Request(
            URL + "/pair", data=json.dumps({"code": code}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0.0.0 Safari/537.36"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                token = json.load(r)["token"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            raise SystemExit(f"pairing failed ({e.code}): {body}")
        path = token_store.save_token(token)
        print(f"paired — token saved to {path}")
        return
    if cmd == "health":
        import time
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"}
        # Layer 1: tunnel — can we reach the PC at all?
        t0 = time.time()
        try:
            req = urllib.request.Request(URL + "/health", headers=ua)
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.load(r)
            ms = int((time.time() - t0) * 1000)
            assert body.get("ok") and body.get("service") == "pc-bridge"
            print(f"tunnel: UP ({ms}ms) — reached pc-bridge via {URL}")
        except Exception as e:
            print(f"tunnel: DOWN — {type(e).__name__}: {e}")
            print("diagnosis: PC off, tunnel not running, or network issue. "
                  "Server/token checks skipped.")
            return
        # Layer 2: token — is our session still valid?
        if not TOKEN:
            print("token: NONE — pair first: python3 op_call.py pair <code>")
            return
        try:
            post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "op-call", "version": "0.2.0"}}})
            print("server: UP — MCP initialize ok")
            print("token: VALID")
        except SystemExit as e:
            print(f"token: {e}")
        except Exception as e:
            print(f"server: ERROR — {type(e).__name__}: {e}")
        return
    if cmd == "logout":
        if not TOKEN:
            raise SystemExit("no session token — nothing to revoke")
        req = urllib.request.Request(
            URL + "/logout", data=b"",
            headers={"Authorization": f"Bearer {TOKEN}",
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0.0.0 Safari/537.36"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(r.read().decode()[:200])
        except urllib.error.HTTPError as e:
            raise SystemExit(f"logout failed ({e.code}): "
                             f"{e.read().decode()[:200]}")
        token_store.clear_token()
        print("local token cleared")
        return
    if not TOKEN:
        raise SystemExit(
            "no session token: pair first — "
            "python3 op_call.py pair <6-digit-code>")
    res, sid = post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "op-call", "version": "0.2.0"}}})
    try:
        post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except Exception:
        pass
    if cmd == "tools":
        res, _ = post("/mcp", {"jsonrpc": "2.0", "id": 2,
                               "method": "tools/list", "params": {}}, sid)
        names = [t["name"] for t in res["tools"]]
        print(f"{len(names)} tools")
        print(" ".join(names))
    elif cmd == "call":
        name = sys.argv[2]
        args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        res, _ = post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": name, "arguments": args}}, sid)
        text = res["content"][0]["text"]
        # Always stash the full response; print is capped for readability.
        with open("/tmp/op-full.json", "w", encoding="utf-8") as f:
            f.write(text)
        if name == "screenshot":
            # Screenshots are huge base64 — never truncate, dump to file instead.
            with open("/tmp/shot-full.json", "w") as f:
                f.write(text)
            print("screenshot saved to /tmp/shot-full.json (%d bytes)" % len(text))
            return
        try:
            print(json.dumps(json.loads(text), indent=1)[:4000])
        except Exception:
            print(text[:4000])
    elif cmd == "submit":
        flags, rest = _parse_flags(sys.argv[2:], ("--key",))
        name = rest[0]
        args = json.loads(rest[1]) if len(rest) > 1 else {}
        key = flags.get("--key") or _default_key(name, args)
        out = _submit(name, args, key, sid)
        print(json.dumps(out))
    elif cmd == "calljob":
        flags, rest = _parse_flags(sys.argv[2:], ("--key", "--timeout"))
        name = rest[0]
        args = json.loads(rest[1]) if len(rest) > 1 else {}
        key = flags.get("--key") or _default_key(name, args)
        timeout_s = int(flags.get(
            "--timeout", os.environ.get("PC_BRIDGE_JOB_TIMEOUT", "3600")))
        out = _submit(name, args, key, sid)
        if out.get("deduplicated"):
            print(f"[job] resubmission deduplicated — existing job "
                  f"{out['job_id']} ({out['state']})", file=sys.stderr)
        result = _poll_until_done(out["job_id"], sid, timeout_s)
        if not _print_job_result(result):
            raise SystemExit(1)
    elif cmd == "job":
        print(json.dumps(
            _tool_call("job_status", {"job_id": sys.argv[2]}, sid),
            indent=1))
    elif cmd == "job-result":
        out = _tool_call("job_result", {"job_id": sys.argv[2]}, sid)
        print(json.dumps(out, indent=1, default=str)[:4000])
    elif cmd == "job-cancel":
        print(json.dumps(
            _tool_call("job_cancel", {"job_id": sys.argv[2]}, sid),
            indent=1))
    elif cmd == "resume":
        flags, rest = _parse_flags(sys.argv[2:], ("--key", "--timeout"))
        rec = _lookup_recorded(key=flags.get("--key"),
                               job_id=rest[0] if rest else None)
        if not rec:
            raise SystemExit("nothing to resume: pass a job_id or --key, "
                             "or submit a job first (no local record)")
        timeout_s = int(flags.get(
            "--timeout", os.environ.get("PC_BRIDGE_JOB_TIMEOUT", "3600")))
        result = _poll_until_done(rec["job_id"], sid, timeout_s)
        if not _print_job_result(result):
            raise SystemExit(1)
    else:
        raise SystemExit("usage: op_call.py tools | call <name> '<json>' "
                         "| submit|calljob <name> '<json>' [--key K] "
                         "| job|job-result|job-cancel <job_id> "
                         "| resume [job_id|--key K]")


if __name__ == "__main__":
    main()
