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
import urllib.request

URL = os.environ["PC_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["PC_BRIDGE_TOKEN"]


def post(path, body, session_id=None):
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {TOKEN}",
            **({"Mcp-Session-Id": session_id} if session_id else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
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
    res, sid = post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "op-call", "version": "0.2.0"}}})
    try:
        post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except Exception:
        pass
    cmd = sys.argv[1]
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
        try:
            print(json.dumps(json.loads(text), indent=1)[:4000])
        except Exception:
            print(text[:4000])
    else:
        raise SystemExit("usage: op_call.py tools | call <name> '<json>'")


if __name__ == "__main__":
    main()
