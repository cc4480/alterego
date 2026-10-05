"""Recon tool wrappers (network reads).

Split from tool_wrappers.py to keep files under 300 lines.
Each wrapper delegates to toolcall.call for audit logging.
"""
import toolcall


def http_headers(url: str, timeout_s: int = 20) -> dict:
    """GET a URL, return status + response headers. No approval (read-only)."""
    return toolcall.call("http_headers", {"url": url, "timeout_s": timeout_s})


def dns_query(domain: str, rtype: str = "A") -> dict:
    """Resolve DNS records (A AAAA CNAME MX NS TXT DNSKEY SOA). No approval."""
    return toolcall.call("dns_query", {"domain": domain, "rtype": rtype})


def tls_info(host: str, port: int = 443, timeout_s: int = 15) -> dict:
    """TLS handshake: version, cipher, cert subject/issuer/expiry. No approval."""
    return toolcall.call("tls_info",
                         {"host": host, "port": port, "timeout_s": timeout_s})


def tcp_check(host: str, ports: list, timeout_s: int = 3) -> dict:
    """TCP connect check across ports (open/closed/filtered). No approval."""
    return toolcall.call("tcp_check",
                         {"host": host, "ports": ports,
                          "timeout_s": timeout_s})


def web_fetch(url: str, max_bytes: int = 100000,
              timeout_s: int = 15) -> dict:
    """Fetch a URL, return its readable text. SSRF-guarded: public IPs only,
    every redirect hop re-checked, optional host rules. Approval in
    default mode (skipped when dontAsk)."""
    return toolcall.call("web_fetch", {"url": url, "max_bytes": max_bytes,
                                       "timeout_s": timeout_s})


def web_search(query: str, max_results: int = 10,
               backend: str = "auto") -> dict:
    """Search the web without an API key (DuckDuckGo/Bing scraping,
    optional self-hosted SearXNG). Returns title/url/snippet per result.
    Approval in default mode (skipped when dontAsk)."""
    return toolcall.call("web_search", {"query": query,
                                        "max_results": max_results,
                                        "backend": backend})
