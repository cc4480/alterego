"""Persistent session token storage for pc-mcp-bridge.

Pair once with a 6-digit code; the session token is saved to disk (0600)
and reused across VM restarts. Only re-pair when the server rejects the
stored token (401 = rotated/revoked).
"""
import os
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "pc-bridge" / "token"


def save_token(token: str) -> Path:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip() + "\n", encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE


def load_token() -> str | None:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        return token or None
    except FileNotFoundError:
        return None


def clear_token() -> None:
    try:
        TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass


def resolve_token() -> str | None:
    """Env override first, then the persisted token file."""
    return os.environ.get("PC_BRIDGE_TOKEN") or load_token()
