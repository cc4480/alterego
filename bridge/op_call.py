#!/usr/bin/env python3
"""Durable operator helper for pc-mcp-bridge (stdlib only).

Reads PC_BRIDGE_URL and PC_BRIDGE_TOKEN from the environment — the token
is never stored in this file. Session-scoped: re-pair after a VM restart.

Usage:
  PC_BRIDGE_URL=... PC_BRIDGE_TOKEN=... python3 op_call.py tools
  PC_BRIDGE_URL=... PC_BRIDGE_TOKEN=... python3 op_call.py call memory_recall '{"query":"x"}'
"""
import json
import os
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
    else:
        raise SystemExit("usage: op_call.py tools | call <name> '<json>'")


if __name__ == "__main__":
    main()
