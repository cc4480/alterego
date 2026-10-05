"""Mock network recon tools: canned results.

Design-doc note: §5 recommends REAL network calls here (pure Python).
The mock deliberately uses canned results instead so the pytest suite is
hermetic: no DNS/HTTP dependency, deterministic in CI, no timeouts.
The wire format matches the ToolDef result_keys exactly.
"""
from seams.providers.mock import record


def http_headers(url: str, timeout_s: int = 20) -> dict:
    record("http_headers", {"url": url, "timeout_s": timeout_s})
    return {"url": url, "status": 200,
            "headers": {"content-type": "text/html; charset=utf-8",
                        "server": "mock"}}


def dns_query(domain: str, rtype: str = "A") -> dict:
    record("dns_query", {"domain": domain, "rtype": rtype})
    return {"domain": domain, "type": rtype.upper(),
            "records": ["93.184.216.34"],
            "note": "mock: canned, no real DNS lookup performed"}


def tls_info(host: str, port: int = 443, timeout_s: int = 15) -> dict:
    record("tls_info", {"host": host, "port": port,
                        "timeout_s": timeout_s})
    return {"host": host, "port": port, "tls_version": "TLSv1.3",
            "cipher": "TLS_AES_128_GCM_SHA256",
            "cert_subject": "CN=mock.example",
            "cert_issuer": "CN=Mock CA",
            "cert_not_before": "2026-01-01T00:00:00+00:00",
            "cert_not_after": "2027-01-01T00:00:00+00:00",
            "error": None}


def tcp_check(host: str, ports: list, timeout_s: int = 3) -> dict:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    if not isinstance(ports, list) or not 1 <= len(ports) <= 50:
        raise ValueError("ports must be a list of 1-50 port numbers")
    record("tcp_check", {"host": host, "ports": ports,
                         "timeout_s": timeout_s})
    out = {}
    for p in ports:
        p = int(p)
        out[str(p)] = "invalid" if not 1 <= p <= 65535 else "open"
    return {"host": host, "ports": out}
