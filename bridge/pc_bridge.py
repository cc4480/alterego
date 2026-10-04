#!/usr/bin/env python3
"""Minimal MCP (Streamable HTTP) client CLI for pc-mcp-bridge.

Env:
  PC_BRIDGE_URL    e.g. https://abc-123.trycloudflare.com   (/mcp is appended)
  PC_BRIDGE_TOKEN  bearer token from the PC's %APPDATA%/pc-mcp-bridge/token

Commands:
  tools                        list remote tools
  call <name> '<json args>'    call a tool
  screenshot --out file.png    save a screenshot as PNG
"""
import argparse
import base64
import json
import os
import sys

import httpx

PROTOCOL_VERSION = "2025-06-18"
PNG_MAGIC = b"\x89PNG"


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, url: str, token: str):
        self.endpoint = url.rstrip("/") + "/mcp"
        self.client = httpx.Client(
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
            timeout=60.0,
            # Don't route the bearer token through any egress proxy, and don't
            # depend on proxy env parsing (httpx chokes on some no_proxy forms).
            trust_env=False,
        )
        self.session_id = None
        self._next_id = 0

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _parse(self, r: httpx.Response):
        if r.status_code == 401:
            raise BridgeError("401 unauthorized — wrong PC_BRIDGE_TOKEN?")
        r.raise_for_status()
        sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if "text/event-stream" in r.headers.get("content-type", ""):
            payload = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
            if payload is None:
                raise BridgeError("empty SSE stream from server")
            msg = json.loads(payload)
        else:
            msg = r.json()
        if "error" in msg:
            raise BridgeError(f"MCP error: {msg['error']}")
        return msg.get("result")

    def _rpc(self, method, params=None):
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        r = self.client.post(self.endpoint, json=body, headers=self._headers())
        return self._parse(r)

    def connect(self):
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pc-bridge-cli", "version": "0.1.0"},
            },
        )
        # fire-and-forget per spec (no id)
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.client.post(self.endpoint, json=body, headers=self._headers())
        return result

    def tools(self):
        return self._rpc("tools/list").get("tools", [])

    def call(self, name, args):
        return self._rpc("tools/call", {"name": name, "arguments": args})


def main():
    ap = argparse.ArgumentParser(prog="pc_bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="list remote tools")
    c = sub.add_parser("call", help="call a tool")
    c.add_argument("name")
    c.add_argument("args_json", nargs="?", default="{}")
    s = sub.add_parser("screenshot", help="save a screenshot as PNG")
    s.add_argument("--out", required=True)
    args = ap.parse_args()

    url = os.environ.get("PC_BRIDGE_URL")
    token = os.environ.get("PC_BRIDGE_TOKEN")
    if not url or not token:
        sys.exit("set PC_BRIDGE_URL and PC_BRIDGE_TOKEN first")

    b = Bridge(url, token)
    try:
        b.connect()
        if args.cmd == "tools":
            for t in b.tools():
                print(f"- {t['name']}: {t.get('description', '')}")
        elif args.cmd == "call":
            res = b.call(args.name, json.loads(args.args_json))
            print(json.dumps(res, indent=2)[:4000])
        elif args.cmd == "screenshot":
            res = b.call("screenshot", {})
            payload = json.loads(res["content"][0]["text"])
            raw = base64.b64decode(payload["png_base64"])
            if not raw.startswith(PNG_MAGIC):
                sys.exit("remote did not return a PNG")
            with open(args.out, "wb") as f:
                f.write(raw)
            print(
                f"saved {args.out} ({len(raw)} bytes, "
                f"{payload.get('width')}x{payload.get('height')})"
            )
    except BridgeError as e:
        sys.exit(f"bridge error: {e}")


if __name__ == "__main__":
    main()
