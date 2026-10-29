"""Server response-path battery (2026-10-29, flawless slice B):
Q4a json_response mode, Q4b pure-ASGI bearer auth, Q4d gzip,
S1 GET /blobs/<sha256>, S3 DPI awareness surfacing.

Builds the exact production app via server.build_app() and drives it
with Starlette's TestClient on Linux (mock provider, per conftest).
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

import blob_store
import server

TOKEN = "test-token-abc"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ACCEPT = "application/json, text/event-stream"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Production app + a persisted session token, per test."""
    token_file = tmp_path / "session_token"
    token_file.write_text(json.dumps([TOKEN]) + "\n", encoding="utf-8")
    monkeypatch.setattr(server, "SESSION_TOKEN_FILE", token_file)
    with TestClient(server.build_app()) as c:
        yield c


def _handshake(client):
    """MCP initialize + initialized notification; returns headers
    carrying the issued mcp-session-id (json_response mode keeps
    stateful sessions — only the POST body shape changed)."""
    r = client.post("/mcp", headers={**AUTH, "Accept": ACCEPT}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}})
    assert r.status_code == 200, r.text
    headers = {**AUTH, "Accept": ACCEPT,
               "mcp-session-id": r.headers["mcp-session-id"]}
    r = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202, r.text
    return headers


def _rpc(client, headers, method, params=None, req_id=2, **kw):
    return client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": req_id, "method": method,
        "params": params or {}}, **kw)


# ---------------------------------------------------------------- Q4a

def test_tools_call_is_plain_json_with_content_length(client):
    headers = _handshake(client)
    r = _rpc(client, {**headers, "Accept-Encoding": "identity"},
             "tools/call", {"name": "system_info", "arguments": {}}, 3)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # One body, known length — not an SSE stream.
    assert int(r.headers["content-length"]) == len(r.content)
    assert not r.content.startswith(b"data:")
    assert b"\nevent:" not in r.content
    body = r.json()
    assert body["id"] == 3 and "result" in body
    assert "MockOS" in body["result"]["content"][0]["text"]


def test_tools_list_is_plain_json_with_content_length(client):
    headers = _handshake(client)
    r = _rpc(client, {**headers, "Accept-Encoding": "identity"},
             "tools/list")
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == len(r.content)
    assert not r.content.startswith(b"data:")
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "system_info" in names and "doctor" in names


# ---------------------------------------------------------------- Q4b

def test_auth_middleware_is_pure_asgi_not_basehttp():
    assert not issubclass(server.BearerAuth, BaseHTTPMiddleware)


def test_health_exempt_and_reports_dpi(client):
    r = client.get("/health")  # no Authorization header at all
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["service"] == "pc-bridge"
    assert "effective" in body["dpi_awareness"]


def test_chat_exempt(client):
    r = client.get("/chat")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_pair_exempt_reaches_handler(client):
    # A non-JSON body proves the request reached _pair (400 bad
    # request) instead of dying at the auth layer (401 unauthorized).
    r = client.post("/pair", content=b"not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"error": "bad request"}


def test_pair_full_flow_still_works(client):
    code = server._pairing.new_code()
    r = client.post("/pair", json={"code": code})  # exempt route
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # The freshly paired token authenticates protected routes.
    unknown = hashlib.sha256(b"never stored").hexdigest()
    r = client.get(f"/blobs/{unknown}",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_protected_routes_401_shape(client):
    unknown = "0" * 64
    for headers in ({}, {"Authorization": "Bearer wrong"},
                    {"Authorization": TOKEN}):  # missing Bearer prefix
        r = client.get(f"/blobs/{unknown}", headers=headers)
        assert r.status_code == 401, headers
        assert r.json() == {"error": "unauthorized"}
    r = client.post("/mcp", headers={"Accept": ACCEPT}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


# ---------------------------------------------------------------- Q4d

def test_gzip_applied_to_json_when_accepted(client):
    headers = _handshake(client)
    r = client.post(
        "/mcp", headers={**headers, "Accept-Encoding": "gzip"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
              "params": {}})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert len(r.json()["result"]["tools"]) > 10  # decoded body intact


def test_no_gzip_without_accept_encoding(client):
    headers = _handshake(client)
    r = client.post(
        "/mcp", headers={**headers, "Accept-Encoding": "identity"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
              "params": {}})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


# ------------------------------------------------------------------ S1

def _store(data: bytes) -> str:
    return blob_store.put(data).split(":", 1)[1]


def test_blob_round_trip_full(client):
    data = bytes(range(256)) * 40  # 10240 bytes, incompressible-ish
    digest = _store(data)
    r = client.get(f"/blobs/{digest}", headers={
        **AUTH, "Accept-Encoding": "gzip"})  # gzip offered, must refuse
    assert r.status_code == 200
    assert r.content == data
    assert r.headers["content-type"] == "application/octet-stream"
    assert int(r.headers["content-length"]) == len(data)
    assert r.headers.get("accept-ranges") == "bytes"
    assert "content-encoding" not in r.headers  # blobs never gzipped


def test_blob_range_requests(client):
    data = b"0123456789" * 100
    digest = _store(data)
    r = client.get(f"/blobs/{digest}", headers={
        **AUTH, "Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == data[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(data)}"
    assert int(r.headers["content-length"]) == 10

    r = client.get(f"/blobs/{digest}", headers={
        **AUTH, "Range": "bytes=990-"})
    assert r.status_code == 206
    assert r.content == data[990:]
    assert r.headers["content-range"] == \
        f"bytes 990-{len(data) - 1}/{len(data)}"

    r = client.get(f"/blobs/{digest}", headers={
        **AUTH, "Range": "bytes=-25"})
    assert r.status_code == 206
    assert r.content == data[-25:]
    assert r.headers["content-range"] == \
        f"bytes {len(data) - 25}-{len(data) - 1}/{len(data)}"


def test_blob_range_unsatisfiable_416(client):
    data = b"x" * 100
    digest = _store(data)
    r = client.get(f"/blobs/{digest}", headers={
        **AUTH, "Range": "bytes=500-600"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(data)}"


def test_blob_unknown_and_malformed_404(client):
    unknown = hashlib.sha256(b"definitely not stored").hexdigest()
    r = client.get(f"/blobs/{unknown}", headers=AUTH)
    assert r.status_code == 404
    r = client.get("/blobs/not-a-hash", headers=AUTH)
    assert r.status_code == 404
    # Path traversal attempt stays a plain 404, never a file read.
    r = client.get("/blobs/..%2f..%2fsecret", headers=AUTH)
    assert r.status_code == 404


def test_blob_requires_auth(client):
    digest = _store(b"classified" * 100)
    r = client.get(f"/blobs/{digest}")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


# ------------------------------------------------------------------ S3

@pytest.mark.skipif(os.name == "nt", reason="Linux expectation")
def test_dpi_awareness_not_applicable_off_windows():
    info = server.dpi_awareness_info()
    assert info["effective"] == "not_applicable"
    applied = server._apply_dpi_awareness()  # must not raise off Win
    assert applied["effective"] == "not_applicable"
    assert applied["set_ok"] is None


def test_doctor_report_carries_dpi_awareness():
    rep = server.doctor()
    assert "dpi_awareness" in rep
    assert "effective" in rep["dpi_awareness"]
    if os.name != "nt":
        assert rep["dpi_awareness"]["effective"] == "not_applicable"


def test_main_applies_dpi_before_anything_else():
    src = Path(server.__file__).read_text(encoding="utf-8")
    main_src = src[src.index("def main():"):]
    assert main_src.index("_apply_dpi_awareness()") < \
        main_src.index("_refresh_pairing_code()")
    assert main_src.index("_apply_dpi_awareness()") < \
        main_src.index("build_app()")
