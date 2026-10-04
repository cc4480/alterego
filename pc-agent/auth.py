"""Bearer-token management. Token lives only on this PC, never in the repo."""
import os
import secrets
from pathlib import Path

APP_DIR_NAME = "pc-mcp-bridge"
TOKEN_FILE = "token"


def app_dir() -> Path:
    """Per-user app dir: %APPDATA%/pc-mcp-bridge (created on demand)."""
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_or_create_token() -> tuple:
    """Return (token, was_created). 64 hex chars via secrets.token_hex(32)."""
    p = app_dir() / TOKEN_FILE
    if p.exists():
        return p.read_text(encoding="utf-8").strip(), False
    token = secrets.token_hex(32)
    p.write_text(token, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass  # Windows ACLs; the file already lives in the user's own APPDATA
    return token, True
