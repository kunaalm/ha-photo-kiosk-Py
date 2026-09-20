"""Pluggable photo sources.

Every source yields an ordered list of Photo objects (URL + optional
bytes/last-modified) that the frame page cycles through. The interface is
deliberately tiny so new sources are mechanical additions: implement
``Source``, register it in ``get_source()``.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


@dataclass
class Photo:
    """A single frame image. url is what the frame page loads."""
    url: str
    caption: str = ""


class Source(Protocol):
    """The pluggable photo-source interface.

    A source returns an ordered list of ``Photo`` objects (URL + optional
    caption) that the frame page cycles through. Everything the engine needs
    to know about "where photos come from" is this one method.
    """
    name: str

    def list(self) -> List[Photo]:
        """Return currently available photos (ordered)."""
        ...


def _http_json(url: str, headers: Optional[dict] = None, data: Optional[bytes] = None,
               timeout: float = 10.0, method: Optional[str] = None) -> Optional[dict]:
    """Thin synchronous JSON GET/POST helper (stdlib only)."""
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


class LocalSource:
    """Serves every image file under photo_dir via /images/* URL space.

    The engine maps /images/<relpath> to a file under photo_dir, so the frame
    page loads images through the same localhost origin as everything else
    (no CORS, no file:// restrictions in an iframe-less slideshow).
    """
    name = "local"

    def __init__(self, photo_dir: str, url_prefix: str = "/images"):
        self.photo_dir = Path(photo_dir)
        self.url_prefix = url_prefix

    def list(self) -> List[Photo]:
        if not self.photo_dir.is_dir():
            return []
        photos: List[Photo] = []
        for root, _dirs, files in os.walk(self.photo_dir):
            for fname in sorted(files):
                ext = Path(fname).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    rel = Path(root) / fname
                    # URL-encode the path so spaces/unicode survive into src=
                    encoded = urllib.parse.quote(str(rel.relative_to(self.photo_dir)), safe="/")
                    photos.append(Photo(url=f"{self.url_prefix}/{encoded}", caption=fname))
        return photos


class HttpSource:
    """Fetches a JSON catalog from a remote URL and yields the images it lists.

    The catalog shape is deliberately simple and source-agnostic:
        { "photos": [ {"url": "https://.../a.jpg", "caption": "..."}, ... ] }
    """
    name = "http"

    def __init__(self, catalog_url: str, timeout: float = 10.0):
        self.catalog_url = catalog_url
        self.timeout = timeout

    def list(self) -> List[Photo]:
        if not self.catalog_url:
            return []
        data = _http_json(self.catalog_url, timeout=self.timeout)
        if not data:
            return []
        photos: List[Photo] = []
        for item in data.get("photos", []):
            url = item.get("url") or item.get("src")
            if url:
                photos.append(Photo(url=url, caption=item.get("caption", "")))
        return photos


# ---------------------------------------------------------------------------
# Google Photos — Ambient API
#
# The Google Photos *Ambient API* is the official successor to the Library
# API for connected-device displays ("view photos from your library on
# connected devices") — i.e. exactly a photo frame. The Library API's
# photoslibrary.readonly scope was removed after March 31, 2025, so any
# integration on the old path is dead-on-arrival for personal libraries.
#
# Two things make Ambient different from the old approach, verified against
# the live discovery document (https://photosambient.googleapis.com/$discovery/rest):
#
#   1. AUTHORIZATION is OAuth 2.0 for *TVs and Limited-Input Device*
#      applications (device-code flow): the user sees a user_code +
#      verification_url, approves from their phone/laptop, and the app polls
#      the token endpoint. Scope: photosambient.mediaitems. The app must also
#      create a "device" in the user's photos account and poll until the user
#      selects which sources (albums) to share.
#
#   2. RENDERING needs the token in the request header. mediaFile.baseUrl is
#      on Google's CDN but a request to it without `Authorization: Bearer`
#      fails. A bare <img src="lh3..."> in the frame page cannot attach that
#      header, so the ENGINE must proxy each image: it fetches the CDN URL
#      with the bearer token and streams the bytes back to the frame.
#      (Base URLs support size params: baseUrl =d (metadata), =wW-hH (fit),
#      =c (crop to aspect ratio), and there's a backgroundBaseUrl blurred
#      render for filling mismatched display ratios.)
# ---------------------------------------------------------------------------


class AmbientAuth:
    """Device-code OAuth2 for the Ambient API (TV / limited-input flow).

    Not intended as a long-lived request-time auth — it drives the
    interactive authorization the user completes ONCE from a phone/laptop.
    After the first exchange it stores a refresh token and refreshes
    access tokens as needed.
    """

    SCOPE = "https://www.googleapis.com/auth/photosambient.mediaitems"
    DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, client_id: str, client_secret: str = "", timeout: float = 10.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._device_code: Optional[str] = None

    # --- device-code flow (interactive, one-time) ---
    def start_device_code(self) -> Optional[dict]:
        """Request a device+user code pair. Returns {user_code, verification_url, ...}."""
        params = urllib.parse.urlencode(
            {"client_id": self.client_id, "scope": self.SCOPE}
        ).encode()
        data = _http_json(
            self.DEVICE_CODE_URL, data=params, timeout=self.timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if data and "device_code" in data:
            self._device_code = data["device_code"]
        return data

    def poll_for_token(self) -> Optional[str]:
        """Poll the token endpoint until the user authorizes (or timeout).

        Returns an access token on success, None on failure/pending-expiry.
        On first success a refresh_token is captured for later use.
        """
        if not self._device_code:
            return None
        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "device_code": self._device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()
        data = _http_json(
            self.TOKEN_URL, data=params, timeout=self.timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not data or "access_token" not in data:
            return None
        import time
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        self._expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._access_token

    # --- token refresh (later, non-interactive) ---
    def refresh_access_token(self) -> Optional[str]:
        if not self._refresh_token:
            return None
        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        data = _http_json(
            self.TOKEN_URL, data=params, timeout=self.timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not data or "access_token" not in data:
            return None
        import time
        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        return self._access_token

    def access_token(self) -> Optional[str]:
        import time
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        return self.refresh_access_token()


class AmbientSource:
    """Google Photos via the Ambient API (device-code OAuth, read-only).

    Secrets are REAL credentials — provide them by file/env, never baked in,
    never into the vault/notes.

    Flow:
      1. User authorizes via device-code flow (docs/google-photos.md).
      2. create() a "device"; user picks sources (albums) in the Photos app.
      3. Poll device until mediaSourcesSet == True.
      4. list() calls mediaItems.list (paginated), returns Photo entries whose
         url points at an ENGINE route (/gimg/<encoded CDN url>) that proxies
         the bytes with the bearer token added server-side.
    """

    name = "google-photos"
    BASE_URL = "https://photosambient.googleapis.com/v1"
    MAX_WIDTH = 1920
    MAX_HEIGHT = 1200

    def __init__(self, client_id: str = "", client_secret: str = "",
                 refresh_token: str = "", device_id: str = "",
                 timeout: float = 10.0):
        self.auth = AmbientAuth(client_id, client_secret, timeout)
        self.auth._refresh_token = refresh_token or None
        self.device_id = device_id
        self.timeout = timeout

    # ---- device management ---------------------------------------------
    def is_configured(self) -> bool:
        return bool(self.auth.client_id and self.auth._refresh_token and self.device_id)

    def create_device(self, display_name: str = "Photo Frame",
                      request_id: Optional[str] = None) -> Optional[dict]:
        """Create an ambient device in the user's account.

        Returns the AmbientDevice dict (contains id + pollingConfig), or None
        on failure. After creation the user must select media sources in the
        Google Photos app (or via devices.patch); mediaSourcesSet flips true
        when they have.
        """
        token = self.auth.access_token()
        if not token:
            return None
        body = json.dumps({"displayName": display_name}).encode()
        url = f"{self.BASE_URL}/devices"
        if request_id:
            url += f"?requestId={urllib.parse.quote(request_id)}"
        return _http_json(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, data=body, timeout=self.timeout, method="POST")

    def get_device(self, device_id: Optional[str] = None) -> Optional[dict]:
        token = self.auth.access_token()
        if not token:
            return None
        did = device_id or self.device_id
        if not did:
            return None
        return _http_json(f"{self.BASE_URL}/devices/{urllib.parse.quote(did)}",
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=self.timeout)

    def device_ready(self) -> bool:
        dev = self.get_device()
        return bool(dev and dev.get("mediaSourcesSet"))

    # ---- media items ---------------------------------------------------
    def _list_page(self, page_token: Optional[str] = None,
                   page_size: int = 50) -> Optional[dict]:
        token = self.auth.access_token()
        if not token or not self.device_id:
            return None
        params = [("deviceId", self.device_id)]
        if page_token:
            params.append(("pageToken", page_token))
        params.append(("pageSize", str(page_size)))
        q = urllib.parse.urlencode(params)
        return _http_json(f"{self.BASE_URL}/mediaItems?{q}",
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=self.timeout)

    def list(self) -> List[Photo]:
        if not self.is_configured():
            return []
        if not self.device_ready():
            return []
        photos: List[Photo] = []
        page_token = None
        while True:
            page = self._list_page(page_token)
            if not page:
                break
            for it in page.get("mediaItems", []):
                base = (it.get("mediaFile") or {}).get("baseUrl")
                if base:
                    # Point at the engine proxy route; server streams the
                    # bytes with the token in the request header.
                    encoded = urllib.parse.quote(f"{base}=w{self.MAX_WIDTH}-h{self.MAX_HEIGHT}", safe="")
                    photos.append(Photo(url=f"/gimg/{encoded}", caption=it.get("id", "")))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return photos

    def fetch_image_bytes(self, encoded_url: str, timeout: Optional[float] = None) -> Optional[bytes]:
        """Fetch one ambient image with the bearer token attached (for /gimg proxying)."""
        token = self.auth.access_token()
        if not token:
            return None
        url = urllib.parse.unquote(encoded_url)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except Exception:
            return None


def get_source(config) -> Source:
    """Factory: maps config.photo_source to a Source instance."""
    if config.photo_source == "http":
        return HttpSource(config.photo_catalog_url, config.http_proxy_timeout)
    if config.photo_source == "google-photos":
        return AmbientSource(
            client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            refresh_token=config.google_refresh_token,
            device_id=config.google_device_id,
            timeout=config.http_proxy_timeout,
        )
    # default: local
    return LocalSource(config.photo_dir)