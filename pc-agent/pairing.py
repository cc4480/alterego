"""Pairing code management with brute-force protection.

Pairing codes are 6-digit, single-use, 30-minute expiry. After 5 failed
attempts, pairing locks and does NOT auto-refresh — this prevents
brute-forcing. The operator must manually delete the lock file or
restart the server to re-arm pairing.
"""
import secrets
import time
from pathlib import Path

PAIRING_TTL_S = 30 * 60
MAX_PAIRING_ATTEMPTS = 5


class PairingManager:
    def __init__(self, appdata_dir: Path):
        self._appdata = appdata_dir
        self._code = ""
        self._expires = 0.0
        self._consumed = False
        self._failures = 0

    @property
    def lock_path(self) -> Path:
        return self._appdata / "pc-mcp-bridge" / "pairing.lock"

    def new_code(self) -> str:
        """Mint a fresh pairing code."""
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = time.time() + PAIRING_TTL_S
        self._consumed = False
        self._failures = 0
        return self._code

    def is_locked(self) -> bool:
        return self._failures >= MAX_PAIRING_ATTEMPTS

    def check(self, code: str) -> tuple[bool, str]:
        """Validate a pairing code. Returns (ok, reason)."""
        if self.is_locked() or self._consumed:
            return False, "pairing unavailable"
        if time.time() > self._expires:
            return False, "pairing code expired"
        if not secrets.compare_digest(code.strip(), self._code):
            self._failures += 1
            remaining = MAX_PAIRING_ATTEMPTS - self._failures
            return False, f"wrong code ({remaining} attempts remaining)"
        self._consumed = True
        return True, "ok"

    def write_lock(self):
        """Write lock file after brute-force lockout."""
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.write_text(
                f"Locked at {time.time()}: {self._failures} failed attempts. "
                f"Delete this file to re-arm pairing.",
                encoding="utf-8",
            )
        except OSError:
            pass

    def try_rearm(self) -> bool:
        """Check if lock file was deleted (manual re-arm)."""
        try:
            if not self.lock_path.exists():
                self._failures = 0
                return True
        except OSError:
            pass
        return False

    def needs_refresh(self) -> bool:
        """True if code was consumed or expired (not locked)."""
        return (self._consumed or time.time() > self._expires
                and not self.is_locked())
