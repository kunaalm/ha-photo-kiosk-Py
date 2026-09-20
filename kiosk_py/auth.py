"""Basic-auth for the web config/upload service.

The config service is LAN-reachable so a laptop can configure the kiosk. It
must not be open to anyone on the network, so it's protected by HTTP Basic
auth. The installer generates a random temporary password and stores it in a
kiosk-owned JSON file — never in the repo or vault.

Password handling:
- The installer writes a plaintext `password` field + `must_change: true`.
- On the FIRST successful login the engine hashes it (PBKDF2) and clears the
  plaintext, keeping `must_change: true` so the operator is forced to set a
  real password.
- `change_password()` sets a new hash and clears `must_change`.

So the plaintext temp password exists only until first login, then only the
hash is stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Optional, Tuple

# Default location; the installer writes here and the engine reads it.
DEFAULT_AUTH_FILE = "/config/auth.json"

_ITERATIONS = 200_000


def _hash_password(password: str, salt: bytes) -> str:
    """PBKDF2-HMAC-SHA256 hash of a password, hex-encoded."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return dk.hex()


def generate_password(length: int = 20) -> str:
    """A random URL-safe password (no ambiguous chars)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class AuthStore:
    """Reads/writes the auth file: username, password hash, must_change flag."""

    def __init__(self, path: str = DEFAULT_AUTH_FILE):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def username(self) -> str:
        return self.load().get("username", "kiosk")

    def must_change(self) -> bool:
        return bool(self.load().get("must_change", True))

    def verify(self, username: str, password: str) -> bool:
        """True if the credentials match. Also finalizes a temp password.

        If the file still holds a plaintext `password` (the installer's
        temporary one) and it matches, hash it and clear the plaintext so the
        temp secret isn't left on disk.
        """
        data = self.load()
        if data.get("username") != username:
            return False
        # Temporary plaintext password (pre-first-login).
        temp = data.get("password")
        if temp:
            if hmac.compare_digest(temp, password):
                self._finalize_temp(data, password)
                return True
            return False
        # Hashed password.
        stored = data.get("password_hash")
        salt = data.get("salt")
        if not stored or not salt:
            return False
        candidate = _hash_password(password, bytes.fromhex(salt))
        return hmac.compare_digest(candidate, stored)

    def _finalize_temp(self, data: dict, password: str) -> None:
        """Replace the plaintext temp password with its hash."""
        salt = secrets.token_bytes(16)
        data["salt"] = salt.hex()
        data["password_hash"] = _hash_password(password, salt)
        data.pop("password", None)
        # must_change stays true: the operator must set a real password.
        self.save(data)

    def set_password(self, username: str, password: str, must_change: bool = False) -> None:
        """Store a new password (and clear the must-change flag)."""
        salt = secrets.token_bytes(16)
        data = self.load()
        data["username"] = username
        data["salt"] = salt.hex()
        data["password_hash"] = _hash_password(password, salt)
        data.pop("password", None)
        data["must_change"] = must_change
        self.save(data)


def parse_basic_auth(header: Optional[str]) -> Optional[Tuple[str, str]]:
    """Parse an 'Authorization: Basic <b64>' header into (user, pass)."""
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    user, _, pw = decoded.partition(":")
    return user, pw
