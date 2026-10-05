"""Thin re-export shim: implementation lives in
seams.providers.windows.recon. Kept so existing importers keep working.
"""
from seams.providers.windows.recon import (
    _decode_name,
    _dns_query_raw,
    _rdata_text,
    dns_query,
    http_headers,
    tcp_check,
    tls_info,
)
