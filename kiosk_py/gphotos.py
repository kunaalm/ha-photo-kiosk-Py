"""Google Photos Ambient API — device-code OAuth + device + photo fetch.

The engine talks to Google directly (no rclone, no CLI). The Ambient API is
the successor to the Library API (whose scopes Google removed 2025-04-01) and
is built for ambient display devices like a photo frame.

Flow (all driven from the web config UI):
  1. start()  -> device-code grant: engine asks Google for a device_code +
     user_code + verification_url.
  2. The UI shows the URL + code; the user opens it on any device and approves.
  3. poll()   -> engine exchanges the device_code for a refresh token.
  4. create_device() -> engine creates an Ambient device (displayName).
  5. The user configures media sources for the device in the Google Photos
     app (via settingsUri) — a one-time step.
  6. device_status() -> poll until mediaSourcesSet is true.
  7. list_photos() -> list + download media items into the photo folder.

Credentials (client id/secret) come from config, not the repo.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

# Google's device-code + token endpoints (OAuth 2.0 for TV/limited-input).
DEVICE_ENDPOINT = "https://oauth2.googleapis.com/device/code"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Ambient API base + scope.
AMBIENT_BASE = "https://photosambient.googleapis.com/v1"
SCOPE = "https://www.googleapis.com/auth/photosambient.mediaitems"

# Where the engine stores the refresh token + device id (shared config dir).
TOKEN_FILE = "gphotos-token.json"
DEVICE_FILE = "gphotos-device.json"


class GooglePhotosOAuth:
    """Ambient API device-code OAuth + device + photo fetch, web-driven."""

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

    def _device_path(self) -> Path:
        return self.dir / DEVICE_FILE

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

    def _load_device(self) -> Optional[Dict[str, Any]]:
        p = self._device_path()
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_device(self, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._device_path().write_text(json.dumps(data, indent=2))

    # --- device-code flow ---
    async def start(self) -> Dict[str, Any]:
        """Begin the device flow. Returns {verification_url, user_code, ...}."""
        if not self.client_id:
            return {"ok": False, "error": "Google OAuth client not configured"}
        body = {"client_id": self.client_id, "scope": SCOPE}
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
        err = data.get("error", f"http {r.status}")
        if err in ("authorization_pending", "slow_down"):
            return {"ok": False, "pending": True, "error": err}
        return {"ok": False, "error": err}

    def status(self) -> Dict[str, Any]:
        """Whether OAuth is configured (for the UI)."""
        return {"configured": self.is_configured()}

    def disconnect(self) -> None:
        """Remove the stored token + device (disconnect the account)."""
        for p in (self._token_path(), self._device_path()):
            if p.is_file():
                p.unlink()
        self._pending = None

    # --- Ambient device ---
    async def _headers(self) -> Dict[str, str]:
        tok = self._load_token()
        return {"Authorization": f"Bearer {tok['access_token']}"}

    async def create_device(self, display_name: str = "HA Photo Kiosk") -> Dict[str, Any]:
        """Create an Ambient device. Returns {ok, device, settings_uri}."""
        tok = self._load_token()
        if not tok:
            return {"ok": False, "error": "not authenticated"}
        body = {"displayName": display_name, "requestId": str(uuid.uuid4())}
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{AMBIENT_BASE}/devices", json=body, headers=headers) as r:
                if r.status != 200:
                    return {"ok": False, "error": f"create device failed: {r.status}"}
                data = await r.json()
        self._save_device(data)
        return {
            "ok": True,
            "device": data,
            "settings_uri": data.get("settingsUri", ""),
            "media_sources_set": data.get("mediaSourcesSet", False),
        }

    async def device_status(self) -> Dict[str, Any]:
        """Poll the device until media sources are configured."""
        dev = self._load_device()
        if not dev:
            return {"ok": False, "error": "no device created"}
        headers = await self._headers()
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{AMBIENT_BASE}/devices/{dev['id']}", headers=headers) as r:
                if r.status != 200:
                    return {"ok": False, "error": f"device get failed: {r.status}"}
                data = await r.json()
        self._save_device(data)
        return {
            "ok": True,
            "media_sources_set": data.get("mediaSourcesSet", False),
            "settings_uri": data.get("settingsUri", ""),
        }

    # --- photo fetch (once device has media sources) ---
    async def list_photos(self, limit: int = 50) -> list:
        """List media items for the device (Ambient API)."""
        tok = self._load_token()
        dev = self._load_device()
        if not tok or not dev:
            return []
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{AMBIENT_BASE}/mediaItems",
                headers=headers,
                params={"pageSize": limit},
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data.get("mediaItems", [])
