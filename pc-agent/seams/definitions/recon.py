"""Service Definitions for passive recon tools (group: recon).

All read-only: no state changes, no payloads. Tier 'ask' reflects the
external network contact (see tool_profiles), not an approval dialog —
the pipeline never pops a dialog for these tools.
"""
from seams.definitions.base import ToolDef

_PURE_PYTHON = ("Pure Python (stdlib) — fully portable, already "
                "platform-independent. No known gaps.")

HTTP_HEADERS = ToolDef(
    name="http_headers",
    group="recon",
    doc="GET a URL, return status + response headers.",
    args={"url": {"type": "str", "required": True},
          "timeout_s": {"type": "int", "default": 20, "required": False}},
    result_keys=["url", "status", "headers"],
    approval_tier="ask",
    side_effects="network",
    platform_notes=_PURE_PYTHON,
)

DNS_QUERY = ToolDef(
    name="dns_query",
    group="recon",
    doc="Resolve DNS records (A AAAA CNAME MX NS TXT DNSKEY SOA).",
    args={"domain": {"type": "str", "required": True},
          "rtype": {"type": "str", "default": "A", "required": False}},
    result_keys=["domain", "type", "records", "note"],
    approval_tier="ask",
    side_effects="network",
    platform_notes="Pure Python (hand-rolled DNS-over-UDP client) — fully "
                   "portable. No known gaps.",
)

TLS_INFO = ToolDef(
    name="tls_info",
    group="recon",
    doc="TLS handshake: version, cipher, cert subject/issuer/expiry.",
    args={"host": {"type": "str", "required": True},
          "port": {"type": "int", "default": 443, "required": False},
          "timeout_s": {"type": "int", "default": 15, "required": False}},
    result_keys=["host", "port", "tls_version", "cipher", "cert_subject",
                 "cert_issuer", "cert_not_before", "cert_not_after",
                 "error"],
    approval_tier="ask",
    side_effects="network",
    platform_notes=_PURE_PYTHON,
)

TCP_CHECK = ToolDef(
    name="tcp_check",
    group="recon",
    doc="TCP connect check across ports (open/closed/filtered).",
    args={"host": {"type": "str", "required": True},
          "ports": {"type": "list", "required": True},
          "timeout_s": {"type": "int", "default": 3, "required": False}},
    result_keys=["host", "ports"],
    approval_tier="ask",
    side_effects="network",
    platform_notes=_PURE_PYTHON,
)


WEB_FETCH = ToolDef(
    name="web_fetch",
    group="recon",
    doc="Fetch a URL and return its readable text (HTML stripped to text). "
        "SSRF-guarded: public IPs only, every redirect hop re-checked, "
        "optional host allow/deny rules from command_rules.json.",
    args={"url": {"type": "str", "required": True},
          "max_bytes": {"type": "int", "default": 100000,
                        "required": False},
          "timeout_s": {"type": "int", "default": 15, "required": False}},
    result_keys=["url", "status_code", "content_type", "content",
                 "truncated"],
    approval_tier="routine",
    side_effects="network",
    platform_notes=_PURE_PYTHON,
)

ALL_RECON_DEFS = [HTTP_HEADERS, DNS_QUERY, TLS_INFO, TCP_CHECK, WEB_FETCH]
