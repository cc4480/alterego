"""Passive recon tools: HTTP headers, DNS records, TLS info, TCP ports.

All read-only: no state changes, no payloads, no intrusive probes.
No approval required.
"""
import socket
import ssl
import struct
import urllib.error
import urllib.request

_DNS_TYPES = {"A": 1, "AAAA": 28, "CNAME": 5, "MX": 15, "NS": 2, "TXT": 16,
              "DNSKEY": 48, "SOA": 6}
_DNS_SERVER = "8.8.8.8"


def http_headers(url: str, timeout_s: int = 20) -> dict:
    """GET a URL and return the status plus all response headers."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    timeout_s = max(1, min(int(timeout_s), 60))
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) pc-mcp-bridge-recon"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return {"url": r.url, "status": r.status,
                    "headers": dict(r.headers.items())}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code,
                "headers": dict(e.headers.items())}


def _dns_query_raw(domain: str, qtype: int, timeout_s: int = 5) -> list:
    """Minimal DNS-over-UDP client. Returns raw answer rdata lists."""
    import random
    txid = random.randint(0, 65535)
    flags = 0x0100  # recursion desired
    header = struct.pack(">HHHHHH", txid, flags, 1, 0, 0, 0)
    qname = b"".join(struct.pack("B", len(p)) + p.encode()
                     for p in domain.rstrip(".").split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", qtype, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(packet, (_DNS_SERVER, 53))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    if len(data) < 12:
        raise RuntimeError("short DNS reply")
    rtxid, rflags, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
    if rtxid != txid:
        raise RuntimeError("DNS transaction id mismatch")
    if rflags & 0x000F:
        raise RuntimeError(f"DNS error code {rflags & 0x000F}")
    off = 12
    for _ in range(qd):  # skip questions
        while data[off] != 0:
            off += 1 + data[off]
        off += 5
    answers = []
    for _ in range(an):
        if data[off] & 0xC0 == 0xC0:  # compressed name pointer
            off += 2
        else:
            while data[off] != 0:
                off += 1 + data[off]
            off += 1
        atype, aclass, ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        rdata_off = off + 10
        off += 10 + rdlen
        if atype == qtype:
            # keep the full packet: embedded names may use compression
            # pointers back into it
            answers.append((atype, data, rdata_off, rdlen, ttl))
    return answers


def _rdata_text(atype: int, data: bytes, rdata_off: int, rdlen: int) -> str:
    seg = data[rdata_off:rdata_off + rdlen]
    if atype == 1:
        return socket.inet_ntoa(seg)
    if atype == 28:
        return socket.inet_ntop(socket.AF_INET6, seg)
    if atype == 16:  # TXT: length-prefixed strings
        parts, i = [], 0
        while i < len(seg):
            n = seg[i]
            parts.append(seg[i + 1:i + 1 + n].decode("utf-8", errors="replace"))
            i += 1 + n
        return "".join(parts)
    if atype in (2, 5):  # NS, CNAME: name, possibly compressed into packet
        return _decode_name(data, rdata_off)[0]
    if atype == 15:  # MX: preference + name
        pref = struct.unpack(">H", seg[:2])[0]
        return f"{pref} {_decode_name(data, rdata_off + 2)[0]}"
    if atype == 48:  # DNSKEY: flags(2) proto(1) alg(1) + key
        flags, proto, alg = struct.unpack(">HBB", seg[:4])
        return f"flags={flags} proto={proto} alg={alg} key_len={len(seg) - 4}"
    return seg.hex()


def _decode_name(data: bytes, off: int) -> tuple:
    labels = []
    while True:
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[off:off + 2])[0] & 0x3FFF
            sub, _ = _decode_name(data, ptr)
            labels.append(sub)
            off += 2
            break
        labels.append(data[off + 1:off + 1 + n].decode("ascii", errors="replace"))
        off += 1 + n
    return ".".join(labels), off


def dns_query(domain: str, rtype: str = "A") -> dict:
    """Resolve DNS records for a domain. Types: A AAAA CNAME MX NS TXT DNSKEY SOA."""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be a non-empty string")
    rtype = rtype.upper()
    if rtype not in _DNS_TYPES:
        raise ValueError(f"unsupported type; use one of {sorted(_DNS_TYPES)}")
    domain = domain.strip().rstrip(".").lower()
    try:
        answers = _dns_query_raw(domain, _DNS_TYPES[rtype])
    except RuntimeError as e:
        return {"domain": domain, "type": rtype, "records": [],
                "note": str(e)}
    return {"domain": domain, "type": rtype,
            "records": [_rdata_text(t, pkt, off, ln) for t, pkt, off, ln, _ in answers]}


def tls_info(host: str, port: int = 443, timeout_s: int = 15) -> dict:
    """TLS handshake summary: version, cipher, certificate subject/issuer/expiry."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    port = int(port)
    timeout_s = max(1, min(int(timeout_s), 60))
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert() or {}
                tls_version = ss.version()
                cipher = (ss.cipher() or [None])[0]
    except ssl.SSLCertVerificationError as e:
        return {"host": host, "port": port, "error": f"cert verification failed: {e.verify_message}"}
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return {"host": host, "port": port, "error": f"{type(e).__name__}: {e}"}

    def _name(seq):
        return ", ".join(f"{k}={v}" for tup in seq for k, v in tup)

    return {"host": host, "port": port,
            "tls_version": tls_version, "cipher": cipher,
            "cert_subject": _name(cert.get("subject", [])),
            "cert_issuer": _name(cert.get("issuer", [])),
            "cert_not_before": cert.get("notBefore"),
            "cert_not_after": cert.get("notAfter")}


def tcp_check(host: str, ports: list, timeout_s: int = 3) -> dict:
    """TCP connect check across ports. Each port: open | closed | filtered."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    if not isinstance(ports, list) or not 1 <= len(ports) <= 50:
        raise ValueError("ports must be a list of 1-50 port numbers")
    timeout_s = max(1, min(int(timeout_s), 10))
    out = {}
    for p in ports:
        p = int(p)
        if not 1 <= p <= 65535:
            out[str(p)] = "invalid"
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        try:
            s.connect((host, p))
            out[str(p)] = "open"
        except socket.timeout:
            out[str(p)] = "filtered"
        except (ConnectionRefusedError, OSError):
            out[str(p)] = "closed"
        finally:
            s.close()
    return {"host": host, "ports": out}


def web_fetch(url: str, max_bytes: int = 100000,
              timeout_s: int = 15) -> dict:
    """Fetch a URL's readable text. SSRF-guarded (see webfetch.py)."""
    from seams.providers.windows.webfetch import web_fetch as _impl
    return _impl(url=url, max_bytes=max_bytes, timeout_s=timeout_s)


def web_search(query: str, max_results: int = 10,
               backend: str = "auto") -> dict:
    """Search the web (DDG/Bing scrape, optional SearXNG). See websearch.py."""
    from seams.providers.windows.websearch import web_search as _impl
    return _impl(query=query, max_results=max_results, backend=backend)
