"""Google Photos device-code OAuth + photo fetch, driven by the web config UI.

The engine talks to Google directly (no rclone, no CLI). The flow is Google's
device-code grant, which works from any device with no localhost redirect:

  1. The web UI calls start() -> engine asks Google for a device_code +
     user_code + verification_url.
  2. The UI shows the URL + code; the user opens it on any device and approves.
  3. The UI polls poll() until the engine has exchanged the device_code for a
     refresh token (Google's device flow auto-completes once the user approves).
  4. The engine stores the refresh token in the shared config dir and can then
     list + download photos.

Credentials (client id/secret) come from config, not the repo.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

# Google's device-code + token endpoints (OAuth 2.0 for TV/limited-input).
DEVICE_ENDPOINT = "https://oauth2.googleapis.com/device/code"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Photos Library API (read-only) — the scope for listing/downloading photos.
SCOPE = "https://www.googleapis.com/auth/photoslibrary.readonly"

# Where the engine stores the refresh token (shared config dir, kiosk-owned).
TOKEN_FILE = "gphotos-token.json"


class GooglePhotosOAuth:
    """Device-code OAuth for Google Photos, driven by the web UI."""

    def __init__(self, config_dir: str = "/config",
                 client_id: str = "", client_secret: str = ""):
        self.dir = Path(config_dir)
        self.client_id = client_id
        self.client_secret = client_secret
        self._pending: Optional[Dict[str, Any]] = None  # in-flight device flow

    # --- config ---
    def is_configured(self) -> bool:
        """True if we have a stored refresh token (OAuth completed)."""
        return self._token_path().is_file()

    def _token_path(self) -> Path:
        return self.dir / TOKEN_FILE

    def _load_token(self) -> Optional[Dict[str, Any]]:
        p = self._token_path()
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_token(self, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._token_path().write_text(json.dumps(data, indent=2))

    # --- device-code flow ---
    async def start(self) -> Dict[str, Any]:
        """Begin the device flow. Returns {verification_url, user_code, ...}."""
        if not self.client_id:
            return {"ok": False, "error": "Google OAuth client not configured"}
        body = {
            "client_id": self.client_id,
            "scope": SCOPE,
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(DEVICE_ENDPOINT, json=body) as r:
                if r.status != 200:
                    return {"ok": False, "error": f"device/code failed: {r.status}"}
                data = await r.json()
        self._pending = data
        return {
            "ok": True,
            "verification_url": data.get("verification_url", ""),
            "user_code": data.get("user_code", ""),
            "expires_in": data.get("expires_in", 0),
            "interval": data.get("interval", 5),
        }

    async def poll(self) -> Dict[str, Any]:
        """Poll Google until the user approves. Returns ok + token on success."""
        if not self._pending:
            return {"ok": False, "error": "no device flow in progress"}
        body = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "device_code": self._pending["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(TOKEN_ENDPOINT, json=body) as r:
                data = await r.json()
        if r.status == 200 and "refresh_token" in data:
            self._save_token({
                "refresh_token": data["refresh_token"],
                "access_token": data.get("access_token", ""),
                "expires_in": data.get("expires_in", 0),
                "scope": data.get("scope", ""),
            })
            self._pending = None
            return {"ok": True, "configured": True}
        # authorization_pending / slow_down / expired are normal during polling.
        err = data.get("error", f"http {r.status}")
        if err in ("authorization_pending", "slow_down"):
            return {"ok": False, "pending": True, "error": err}
        return {"ok": False, "error": err}

    def status(self) -> Dict[str, Any]:
        """Whether OAuth is configured (for the UI)."""
        return {"configured": self.is_configured()}

    def disconnect(self) -> None:
        """Remove the stored token (disconnect the account)."""
        p = self._token_path()
        if p.is_file():
            p.unlink()
        self._pending = None

    # --- photo fetch (once configured) ---
    async def list_photos(self, limit: int = 50) -> list:
        """List recent photos from the user's library (read-only scope)."""
        tok = self._load_token()
        if not tok:
            return []
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://photoslibrary.googleapis.com/v1/mediaItems",
                headers=headers,
                params={"pageSize": limit},
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data.get("mediaItems", [])
