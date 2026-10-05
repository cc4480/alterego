"""Tests for web_fetch SSRF guards and HTML stripping.

Guard tests use IP literals (no DNS) or the pure _is_public_ip helper so
they stay hermetic. Fetch-path tests run against a local HTTP server and
call the internal _fetch_guarded directly (bypassing the guard, which
correctly blocks loopback). The example.com test does real network I/O
and skips when this sandbox's DNS returns non-public IPs.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from seams.providers.windows import webfetch as wf

PAGE = ("<html><head><style>.x{color:red}</style>"
        "<script>alert(1)</script></head>"
        "<body><h1>Local Title</h1><p>Hello <b>local</b> world</p>"
        "</body></html>")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_scheme_blocked():
    with pytest.raises(ValueError, match="only http"):
        wf._assert_fetchable("ftp://example.com/x")
    with pytest.raises(ValueError, match="only http"):
        wf._assert_fetchable("file:///etc/passwd")


def test_loopback_blocked():
    with pytest.raises(ValueError, match="non-public IP"):
        wf._assert_fetchable("http://127.0.0.1/")


def test_private_ranges_blocked():
    for ip in ("10.0.0.1", "10.255.255.255", "192.168.1.1",
               "172.16.0.1", "172.31.255.255"):
        with pytest.raises(ValueError, match="non-public IP"):
            wf._assert_fetchable(f"http://{ip}/")


def test_ipv6_nonpublic_blocked():
    with pytest.raises(ValueError, match="non-public IP"):
        wf._assert_fetchable("http://[::1]/")
    with pytest.raises(ValueError, match="non-public IP"):
        wf._assert_fetchable("http://[fe80::1]/")


def test_public_ips_pass_guard():
    assert wf._is_public_ip("8.8.8.8")
    assert wf._is_public_ip("93.184.216.34")
    assert not wf._is_public_ip("127.0.0.1")
    assert not wf._is_public_ip("10.1.2.3")
    assert not wf._is_public_ip("not-an-ip")


def test_redirect_hop_revalidated():
    # redirect_request validates the target via _assert_fetchable, so a
    # redirect to a private IP raises instead of being followed.
    handler = wf._GuardedRedirect()
    req = type("R", (), {"full_url": "http://example.com/"})()
    with pytest.raises(ValueError, match="non-public IP"):
        handler.redirect_request(req, None, 302, "Found",
                                 {}, "http://127.0.0.1/evil")


def test_max_redirects_capped():
    assert wf._GuardedRedirect.max_redirections == 5


def test_html_stripping():
    text = wf._html_to_text(PAGE)
    assert "Local Title" in text
    assert "Hello local world" in text
    assert "alert" not in text
    assert "color:red" not in text
    assert "<" not in text


def test_host_rules_deny(tmp_path, monkeypatch):
    rules = {"web_fetch": {"deny": ["*.blocked.test"]}}
    appdata = tmp_path / "appdata" / "pc-mcp-bridge"
    appdata.mkdir(parents=True)
    (appdata / "command_rules.json").write_text(json.dumps(rules))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    with pytest.raises(ValueError, match="denied by rule"):
        wf._assert_fetchable("http://evil.blocked.test/")


def test_host_rules_allow_list_default_deny(tmp_path, monkeypatch):
    rules = {"web_fetch": {"allow": ["example.com"]}}
    appdata = tmp_path / "appdata" / "pc-mcp-bridge"
    appdata.mkdir(parents=True)
    (appdata / "command_rules.json").write_text(json.dumps(rules))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    with pytest.raises(ValueError, match="not in web_fetch allow list"):
        wf._assert_fetchable("http://other.example/")


def test_fetch_local_server(local_server):
    r = wf._fetch_guarded(local_server + "/", 100000, 10)
    assert r["status_code"] == 200
    assert r["truncated"] is False
    assert "Local Title" in r["content"]
    assert "Hello local world" in r["content"]
    assert "<" not in r["content"]
    assert set(r.keys()) == {"url", "status_code", "content_type",
                             "content", "truncated"}


def test_fetch_truncation(local_server):
    r = wf._fetch_guarded(local_server + "/", 10, 10)
    assert r["truncated"] is True
    assert len(r["content"]) <= 60


def test_example_com_live():
    # Real network; skip where the sandbox DNS maps public names to
    # non-public (proxy) IPs — the guard is correct to block those.
    try:
        wf._assert_fetchable("https://example.com")
    except ValueError:
        pytest.skip("sandbox DNS returns non-public IP for example.com")
    r = wf.web_fetch("https://example.com", timeout_s=20)
    assert r["status_code"] == 200
    assert r["truncated"] is False
    assert "Example Domain" in r["content"]
