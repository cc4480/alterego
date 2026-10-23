#!/usr/bin/env python3
"""Minimal mock of the pc-agent MCP surface (stdlib only, for bridge testing).

Speaks just enough MCP Streamable HTTP:
  initialize                 -> protocolVersion + serverInfo (+ Mcp-Session-Id)
  notifications/initialized  -> 202
  tools/list                 -> echo, screenshot, system_info
  tools/call                 -> echo returns args; screenshot returns a
                                generated PNG as base64 JSON text content

Run:  python3 mock_mcp_server.py [port]   (default 8765)
"""
import base64
import json
import os
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

SESSION = "mock-session-1"
PROTO = "2025-06-18"
# Bearer token the mock expects (default "secret"); wrong token -> 401.
MOCK_TOKEN = os.environ.get("MOCK_TOKEN", "secret")


def make_png(w=96, h=64) -> bytes:
    rows = b"".join(
        b"\x00" + b"".join(bytes([(x * 4) % 256, (y * 4) % 256, 128]) for x in range(w))
        for y in range(h)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PNG_B64 = base64.b64encode(make_png()).decode("ascii")

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back arguments",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "screenshot",
        "description": "Fake screenshot (generated PNG)",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "system_info",
        "description": "Fake system info",
        "inputSchema": {"type": "object"},
    },
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, status=200, session=False):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("Mcp-Session-Id", SESSION)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - http.server naming
        if self.path != "/mcp":
            self._send({"error": "not found"}, 404)
            return
        if self.headers.get("Authorization", "") != f"Bearer {MOCK_TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(length) or b"{}")
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": PROTO,
                        "serverInfo": {"name": "mock-pc-agent", "version": "0.1.0"},
                    },
                },
                session=True,
            )
        elif method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
        elif method == "tools/list":
            self._send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments", {})
            if name == "screenshot":
                text = json.dumps(
                    {"png_base64": PNG_B64, "width": 96, "height": 64}
                )
            elif name == "system_info":
                text = json.dumps({"system": "Windows", "mock": True})
            else:
                text = json.dumps({"echo": args})
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            )
        else:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {"code": -32601, "message": "unknown method"},
                }
            )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock pc-agent on http://127.0.0.1:{port}/mcp")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
