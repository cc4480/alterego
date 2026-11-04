"""Bearer-token store + app dir. Tokens live only on this PC, never in the repo.

Store format (session_token): a JSON list. Entries are bare token
strings (legacy) or {"token", "name", "paired_at"} records. Reads
tolerate both plus missing/corrupt files (fail closed to no
sessions); writes normalize everything to records.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

APP_DIR_NAME = "pc-mcp-bridge"
SESSION_TOKEN_FILE = "session_token"

_NAME_RX = re.compile(r"^[a-z0-9_-]{1,32}$")


def app_dir() -> Path:
    """Per-user app dir: %APPDATA%/pc-mcp-bridge (created on demand)."""
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_token_path() -> Path:
    """Single source for the persisted-session path."""
    return app_dir() / SESSION_TOKEN_FILE


def fingerprint_token(token: str) -> str:
    """Session fingerprint for one bearer token (never the token)."""
    return "sess_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def normalize_name(name) -> str | None:
    """Lowercased valid room name, or None. `guest-` is reserved for
    derived guest names so a paired name can never impersonate one."""
    if not isinstance(name, str):
        return None
    clean = name.strip().lower()
    if not clean or not _NAME_RX.match(clean):
        return None
    if clean.startswith("guest-"):
        return None
    return clean


def guest_name(token: str) -> str:
    """Deterministic room name for a token with no paired name."""
    return "guest-" + hashlib.sha256(token.encode()).hexdigest()[:6]


def load_clients() -> list:
    """Paired clients as [{token, name|None, paired_at|None}]. Never
    raises: missing, corrupt, or malformed content yields []."""
    try:
        data = json.loads(session_token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, str):
        data = [data]  # ancient bare-token format
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if isinstance(entry, str) and entry:
            out.append({"token": entry, "name": None, "paired_at": None})
        elif isinstance(entry, dict) and isinstance(entry.get("token"), str) \
                and entry["token"]:
            out.append({"token": entry["token"],
                        "name": normalize_name(entry.get("name")),
                        "paired_at": entry.get("paired_at")})
    return out


def load_tokens() -> list:
    """Persisted session tokens (tolerant form of load_clients)."""
    return [c["token"] for c in load_clients()]


def _write_clients(clients) -> None:
    session_token_path().parent.mkdir(parents=True, exist_ok=True)
    payload = [{"token": c["token"], "name": c["name"],
                "paired_at": c["paired_at"]} for c in clients]
    session_token_path().write_text(json.dumps(payload) + "\n",
                                    encoding="utf-8")
    try:
        os.chmod(session_token_path(), 0o600)
    except OSError:
        pass  # Windows ACLs; the file already lives in the user's own APPDATA


def save_client(token: str, name=None) -> None:
    """Append a pairing (normalizing legacy entries to records).
    Existing tokens stay valid; re-saving a known token keeps the
    first record (names are bound at first pairing)."""
    clients = load_clients()
    if token not in [c["token"] for c in clients]:
        clients.append({"token": token, "name": normalize_name(name),
                        "paired_at": time.time()})
    _write_clients(clients)


def revoke_client(token: str) -> bool:
    """Remove one token. Returns True if it was present."""
    clients = load_clients()
    kept = [c for c in clients if c["token"] != token]
    if len(kept) == len(clients):
        return False
    _write_clients(kept)
    return True


def clear_clients() -> None:
    """Revoke ALL tokens."""
    try:
        session_token_path().unlink()
    except OSError:
        pass


def name_for_token(token: str) -> str | None:
    """A token's room name: paired name, else derived guest name.
    None when the token is not paired at all."""
    for c in load_clients():
        if c["token"] == token:
            return c["name"] or guest_name(token)
    return None
