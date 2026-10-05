"""web_fetch: fetch a URL's readable text with SSRF guards.

Security boundary (fail closed):
- only http/https schemes
- hostname resolved via socket.getaddrinfo; EVERY resolved IP must be
  globally routable (ipaddress: blocks private, loopback, link-local,
  multicast, reserved, unspecified)
- redirects re-validated on every hop (custom redirect handler), max 5
- optional host allow/deny rules from the "web_fetch" section of
  command_rules.json (fnmatch style, deny wins, same as command rules)

Known gap (documented, not silently ignored): DNS is resolved once for
the guard and again by urllib at connect time, so a DNS-rebinding
attacker racing the TTL could slip a private IP past the check. Closing
that needs IP-pinned connections with manual Host/SNI handling.
"""
import fnmatch
import html.parser
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) pc-mcp-bridge-webfetch")
_MAX_REDIRECTS = 5


def _is_public_ip(ip_str: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        return ipaddress.ip_address(ip_str).is_global
    except ValueError:
        return False


def _host_rules() -> tuple[list, list]:
    """(allow, deny) host patterns from command_rules.json's web_fetch key.

    Missing file, missing section, or bad JSON -> ([], []) = no host
    restriction (the SSRF IP guards still apply).
    """
    try:
        from command_rules import rules_path
        data = json.loads(rules_path().read_text(encoding="utf-8"))
        section = (data.get("web_fetch") or {}) if isinstance(data, dict) \
            else {}
        allow = section.get("allow", []) or []
        deny = section.get("deny", []) or []
        return [str(p) for p in allow], [str(p) for p in deny]
    except (OSError, ValueError, ImportError):
        return [], []


def _check_host_rules(host: str) -> None:
    """Raise ValueError if host rules deny this host."""
    allow, deny = _host_rules()
    if not allow and not deny:
        return
    h = host.lower()
    for pattern in deny:
        if fnmatch.fnmatch(h, pattern.lower()):
            raise ValueError(
                f"web_fetch: host {host!r} denied by rule {pattern!r}")
    if allow and not any(fnmatch.fnmatch(h, p.lower()) for p in allow):
        raise ValueError(
            f"web_fetch: host {host!r} not in web_fetch allow list")


def _check_resolved_ips(host: str, port: int) -> None:
    """Resolve host and require every resulting IP to be public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"web_fetch: DNS resolution failed for "
                         f"{host!r}: {e}")
    if not infos:
        raise ValueError(f"web_fetch: no addresses for {host!r}")
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise ValueError(
                f"web_fetch: blocked non-public IP {ip} for host {host!r}")


def _assert_fetchable(url: str) -> urllib.parse.ParseResult:
    """Validate scheme, host rules, and resolved IPs. Raise on any block."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("web_fetch: url must be a non-empty string")
    parts = urllib.parse.urlparse(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("web_fetch: only http:// and https:// allowed")
    host = parts.hostname
    if not host:
        raise ValueError("web_fetch: URL has no host")
    _check_host_rules(host)
    _check_resolved_ips(host, parts.port or
                        (443 if parts.scheme.lower() == "https" else 80))
    return parts


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only after re-validating each hop's target."""
    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_fetchable(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _TextExtractor(html.parser.HTMLParser):
    """Strip tags/scripts/styles; keep block structure as newlines."""

    _BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
               "section", "article", "header", "footer", "br", "hr"}
    _SKIP = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCKS and not self._skip_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCKS and not self._skip_depth:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]*\n+", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> str:
    ext = _TextExtractor()
    ext.feed(html)
    ext.close()
    return ext.text()


def _fetch_guarded(url: str, max_bytes: int, timeout_s: int) -> dict:
    """HTTP fetch + text extraction. Call only after _assert_fetchable."""
    opener = urllib.request.build_opener(_GuardedRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        resp = opener.open(req, timeout=timeout_s)
        status, ctype = resp.status, resp.headers.get_content_type()
        raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        return {"url": url, "status_code": e.code,
                "content_type": e.headers.get_content_type(),
                "content": f"[HTTP error {e.code}]", "truncated": False}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return {"url": url, "status_code": 0, "content_type": "",
                "content": f"[fetch failed: {type(e).__name__}: {e}]",
                "truncated": False}
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    if ctype == "text/html" or "html" in ctype:
        content = _html_to_text(text)
    elif ctype.startswith("text/") or ctype in (
            "application/json", "application/xml", "application/javascript"):
        content = text
    else:
        content = f"[non-text content: {ctype}, {len(raw)} bytes]"
    return {"url": url, "status_code": status, "content_type": ctype,
            "content": content, "truncated": truncated}


def web_fetch(url: str, max_bytes: int = 100000,
              timeout_s: int = 15) -> dict:
    """Fetch a URL, return status + readable text. SSRF-guarded.

    Raises ValueError for blocked URLs (bad scheme, private IP, redirect
    to private IP, host-rule deny). Operational HTTP errors return a
    result with the error in content and status_code set.
    """
    from approval import request_approval
    _assert_fetchable(url)
    max_bytes = max(1, min(int(max_bytes), 1000000))
    timeout_s = max(1, min(int(timeout_s), 60))
    if not request_approval(f"[web_fetch]\nGET {url[:200]}",
                           tier="routine", tool_name="web_fetch"):
        raise PermissionError("denied by local approval (or timed out)")
    return _fetch_guarded(url, max_bytes, timeout_s)
