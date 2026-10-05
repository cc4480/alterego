"""web_search: search the web without an API key.

Backends (stdlib only):
1. searxng — self-hosted metasearch; used when backend="searxng" or the
   SEARXNG_URL env var is set. GET <base>/search?q=..&format=json.
2. duckduckgo — default. Scrapes https://html.duckduckgo.com/html/.
3. bing — fallback when DDG yields zero results. Scrapes bing.com/search.

Total budget 30s across backends; results deduplicated by URL.
Queries are always urlencoded into the engine URL, so a "://" inside the
query is just search text — there is no direct-fetch smuggling vector.
"""
import html.parser
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 pc-mcp-bridge")
_TOTAL_BUDGET_S = 30.0
_PER_BACKEND_S = 12


def _fetch_html(url: str, timeout_s: int) -> str:
    """GET a search page, return decoded HTML. Raise on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        raw = r.read(500_000)
    return raw.decode("utf-8", errors="replace")


def _real_url(href: str) -> str:
    """Unwrap DDG's /l/?uddg=<urlencoded> redirect links."""
    if not href:
        return ""
    parts = urllib.parse.urlparse(href)
    if parts.path.startswith("/l/"):
        q = urllib.parse.parse_qs(parts.query)
        uddg = q.get("uddg")
        if uddg:
            return uddg[0]
    if href.startswith("//"):
        return "https:" + href
    return href


class _DDGParser(html.parser.HTMLParser):
    """Parse DuckDuckGo html endpoint: a.result__a + a.result__snippet."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._in_title = False
        self._in_snippet = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        cls = dict(attrs).get("class", "")
        classes = cls.split()
        if "result__a" in classes:
            self._cur = {"title": "", "url": "",
                         "snippet": ""}
            self._cur["url"] = _real_url(dict(attrs).get("href", ""))
            self._in_title = True
            self._buf = []
        elif "result__snippet" in classes and self._cur is not None:
            self._in_snippet = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag != "a":
            return
        if self._in_title:
            self._cur["title"] = "".join(self._buf).strip()
            self._in_title = False
        elif self._in_snippet and self._cur is not None:
            self._cur["snippet"] = "".join(self._buf).strip()
            self._in_snippet = False
            if self._cur["url"]:
                self.results.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._in_title or self._in_snippet:
            self._buf.append(data)


class _BingParser(html.parser.HTMLParser):
    """Parse Bing: li.b_algo > h2 > a, snippet in div.b_caption p."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._in_h2 = False
        self._in_a = False
        self._in_caption = False
        self._in_p = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get("class", "").split()
        if tag == "li" and "b_algo" in cls:
            self._cur = {"title": "", "url": "", "snippet": ""}
        elif tag == "h2" and self._cur is not None:
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and self._cur is not None:
            self._in_a = True
            self._cur["url"] = d.get("href", "")
            self._buf = []
        elif tag == "div" and "b_caption" in cls \
                and self._cur is not None:
            self._in_caption = True
        elif tag == "p" and self._in_caption and self._cur is not None:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a and self._cur is not None:
            self._cur["title"] = "".join(self._buf).strip()
            self._in_a = False
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p" and self._in_p and self._cur is not None:
            self._cur["snippet"] = "".join(self._buf).strip()
            self._in_p = False
        elif tag == "div" and self._in_caption:
            self._in_caption = False
        elif tag == "li" and self._cur is not None:
            if self._cur["url"] and self._cur["title"]:
                self.results.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._in_a or self._in_p:
            self._buf.append(data)


def parse_ddg(html: str) -> list[dict]:
    """Pure: extract results from DDG html. No network."""
    p = _DDGParser()
    p.feed(html)
    p.close()
    return p.results


def parse_bing(html: str) -> list[dict]:
    """Pure: extract results from Bing html. No network."""
    p = _BingParser()
    p.feed(html)
    p.close()
    return p.results


def _search_ddg(query: str, timeout_s: int) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    html = _fetch_html(f"https://html.duckduckgo.com/html/?q={q}",
                       timeout_s)
    return parse_ddg(html)


def _search_bing(query: str, timeout_s: int) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    html = _fetch_html(f"https://www.bing.com/search?q={q}", timeout_s)
    return parse_bing(html)


def _searxng_base() -> str | None:
    base = (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/")
    return base or None


def _search_searxng(query: str, timeout_s: int) -> list[dict]:
    """Pure-ish: query SearXNG JSON API. Raise on any failure."""
    base = _searxng_base()
    if not base:
        raise ValueError("SEARXNG_URL not set")
    q = urllib.parse.quote_plus(query)
    req = urllib.request.Request(f"{base}/search?q={q}&format=json",
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        data = json.loads(r.read(500_000).decode("utf-8",
                                                 errors="replace"))
    out = []
    for item in data.get("results", []):
        url = item.get("url", "")
        if not url:
            continue
        out.append({"title": item.get("title", ""),
                    "url": url,
                    "snippet": item.get("content", "")})
    return out


def _dedup(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in results:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            out.append(r)
    return out


def web_search(query: str, max_results: int = 10,
               backend: str = "auto") -> dict:
    """Search the web. Backends: auto (DDG then Bing), duckduckgo, bing,
    searxng (needs SEARXNG_URL). Falls back down the chain on failure.
    """
    from approval import request_approval
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query = query.strip()[:500]
    max_results = max(1, min(int(max_results), 20))
    backend = (backend or "auto").lower()
    if backend not in ("auto", "duckduckgo", "bing", "searxng"):
        raise ValueError("backend must be auto, duckduckgo, bing, "
                         "or searxng")
    if not request_approval(f"[web_search]\n{query[:200]}",
                           tier="routine", tool_name="web_search"):
        raise PermissionError("denied by local approval (or timed out)")

    deadline = time.time() + _TOTAL_BUDGET_S
    used = "none"
    results: list[dict] = []
    notes: list[str] = []

    def _budget() -> int:
        return max(1, min(_PER_BACKEND_S,
                          int(deadline - time.time())))

    # SearXNG first when pinned or configured.
    searxng_failed = False
    if backend == "searxng" or (backend == "auto" and _searxng_base()):
        try:
            results = _search_searxng(query, _budget())
            used = "searxng"
        except Exception as e:  # noqa: BLE001 - fall through to DDG
            notes.append(f"searxng failed ({type(e).__name__}), "
                         "falling back")
            searxng_failed = True

    if not results and (backend in ("auto", "duckduckgo")
                        or searxng_failed):
        try:
            results = _search_ddg(query, _budget())
            used = "duckduckgo" if results else used
        except Exception as e:  # noqa: BLE001 - fall through to Bing
            notes.append(f"duckduckgo failed ({type(e).__name__}), "
                         "falling back")

    if not results and (backend in ("auto", "bing", "duckduckgo")
                        or searxng_failed):
        # Bing is the fallback for auto and for a DDG that returned
        # nothing (or errored); a pinned bing just runs directly.
        try:
            results = _search_bing(query, _budget())
            used = "bing" if results else used
        except Exception as e:  # noqa: BLE001 - out of backends
            notes.append(f"bing failed ({type(e).__name__})")

    results = _dedup(results)[:max_results]
    out: dict = {"query": query, "backend_used": used,
                 "results": results, "total": len(results)}
    if notes:
        out["notes"] = "; ".join(notes)
    return out
