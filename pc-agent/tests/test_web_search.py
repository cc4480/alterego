"""Tests for web_search: HTML parsers, backend fallback, arg validation.

Hermetic: parsers run against fixture HTML strings, backend selection is
tested by monkeypatching the _search_* functions. No live network I/O.
"""
import pytest

from seams.providers.windows import websearch as ws

DDG_HTML = """
<html><body>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc">First Title</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First snippet text here.</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" href="https://example.org/b">Second Title</a>
  </h2>
  <a class="result__snippet" href="https://example.org/b">Second snippet.</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=xyz">Dup Title</a>
  </h2>
  <a class="result__snippet" href="x">Dup snippet (same URL as first).</a>
</div>
</body></html>
"""

BING_HTML = """
<html><body><ol>
<li class="b_algo">
  <h2><a href="https://bing-example.com/1">Bing Title One</a></h2>
  <div class="b_caption"><p>Bing snippet one.</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://bing-example.com/2">Bing Title Two</a></h2>
  <div class="b_caption"><p>Bing snippet two.</p></div>
</li>
</ol></body></html>
"""

EMPTY_HTML = "<html><body><p>no results found</p></body></html>"


def test_parse_ddg_extracts_results():
    rs = ws.parse_ddg(DDG_HTML)
    assert len(rs) == 3
    assert rs[0]["title"] == "First Title"
    assert rs[0]["url"] == "https://example.com/a"
    assert rs[0]["snippet"] == "First snippet text here."
    assert rs[1]["url"] == "https://example.org/b"


def test_parse_ddg_unwraps_uddg():
    rs = ws.parse_ddg(DDG_HTML)
    assert not any(r["url"].startswith("//duckduckgo.com")
                   for r in rs)


def test_parse_ddg_empty():
    assert ws.parse_ddg(EMPTY_HTML) == []


def test_parse_bing_extracts_results():
    rs = ws.parse_bing(BING_HTML)
    assert len(rs) == 2
    assert rs[0] == {"title": "Bing Title One",
                     "url": "https://bing-example.com/1",
                     "snippet": "Bing snippet one."}


def test_parse_bing_empty():
    assert ws.parse_bing(EMPTY_HTML) == []


def test_dedup_by_url():
    rs = ws.parse_ddg(DDG_HTML)
    dd = ws._dedup(rs)
    urls = [r["url"] for r in dd]
    assert len(urls) == len(set(urls)) == 2


def test_real_url_passthrough():
    assert ws._real_url("https://example.com/x") == "https://example.com/x"
    assert ws._real_url("//example.com/x") == "https://example.com/x"
    assert ws._real_url("") == ""


def _no_approval(monkeypatch):
    monkeypatch.setattr("approval.request_approval",
                        lambda *a, **k: True)


def test_auto_uses_ddg(monkeypatch):
    _no_approval(monkeypatch)
    monkeypatch.setattr(ws, "_search_ddg",
                        lambda q, t: [{"title": "T", "url": "https://u",
                                       "snippet": "S"}])
    monkeypatch.setattr(ws, "_search_bing",
                        lambda q, t: (_ for _ in ()).throw(
                            AssertionError("bing should not run")))
    r = ws.web_search("hello")
    assert r["backend_used"] == "duckduckgo"
    assert r["total"] == 1
    assert r["query"] == "hello"


def test_ddg_empty_falls_back_to_bing(monkeypatch):
    _no_approval(monkeypatch)
    monkeypatch.setattr(ws, "_search_ddg", lambda q, t: [])
    monkeypatch.setattr(ws, "_search_bing",
                        lambda q, t: [{"title": "B", "url": "https://b",
                                       "snippet": "S"}])
    r = ws.web_search("hello")
    assert r["backend_used"] == "bing"


def test_ddg_error_falls_back_to_bing(monkeypatch):
    _no_approval(monkeypatch)

    def _boom(q, t):
        raise OSError("network down")

    monkeypatch.setattr(ws, "_search_ddg", _boom)
    monkeypatch.setattr(ws, "_search_bing",
                        lambda q, t: [{"title": "B", "url": "https://b",
                                       "snippet": "S"}])
    r = ws.web_search("hello")
    assert r["backend_used"] == "bing"
    assert "notes" in r


def test_pinned_bing_skips_ddg(monkeypatch):
    _no_approval(monkeypatch)
    monkeypatch.setattr(ws, "_search_ddg",
                        lambda q, t: (_ for _ in ()).throw(
                            AssertionError("ddg should not run")))
    monkeypatch.setattr(ws, "_search_bing",
                        lambda q, t: [{"title": "B", "url": "https://b",
                                       "snippet": "S"}])
    r = ws.web_search("hello", backend="bing")
    assert r["backend_used"] == "bing"


def test_searxng_unreachable_falls_back(monkeypatch):
    _no_approval(monkeypatch)
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(ws, "_search_ddg",
                        lambda q, t: [{"title": "D", "url": "https://d",
                                       "snippet": "S"}])
    r = ws.web_search("hello", backend="searxng")
    # searxng fails fast (connection refused) -> DDG fallback with note
    assert r["backend_used"] == "duckduckgo"
    assert "notes" in r and "searxng" in r["notes"]


def test_searxng_json_parsing(monkeypatch):
    _no_approval(monkeypatch)
    payload = {"results": [
        {"title": "SX", "url": "https://sx.example",
         "content": "sx snippet"},
        {"title": "No URL", "url": "", "content": "dropped"},
    ]}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            import json as j
            return j.dumps(payload).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req,
                        timeout: _Resp())
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
    rs = ws._search_searxng("q", 5)
    assert rs == [{"title": "SX", "url": "https://sx.example",
                   "snippet": "sx snippet"}]


def test_max_results_clamped_and_sliced(monkeypatch):
    _no_approval(monkeypatch)
    many = [{"title": str(i), "url": f"https://u/{i}",
             "snippet": "s"} for i in range(30)]
    monkeypatch.setattr(ws, "_search_ddg", lambda q, t: many)
    r = ws.web_search("hello", max_results=25)
    assert r["total"] == 20  # clamped to 20


def test_arg_validation(monkeypatch):
    _no_approval(monkeypatch)
    with pytest.raises(ValueError, match="non-empty"):
        ws.web_search("")
    with pytest.raises(ValueError, match="non-empty"):
        ws.web_search("   ")
    with pytest.raises(ValueError, match="backend must be"):
        ws.web_search("hello", backend="yahoo")


def test_approval_denied(monkeypatch):
    monkeypatch.setattr("approval.request_approval",
                        lambda *a, **k: False)
    with pytest.raises(PermissionError):
        ws.web_search("hello")
