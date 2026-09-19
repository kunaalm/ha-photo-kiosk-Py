"""Pluggable photo sources.

Every source yields an ordered list of Photo objects (URL + optional
bytes/last-modified) that the frame page cycles through. The interface is
deliberately tiny so new sources (e.g. Google Photos OAuth) are mechanical
additions: implement ``Source``, register it in ``get_source()``.
"""
from __future__ import annotations

import json
import os
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
    name: str

    def list(self) -> List[Photo]:
        """Return currently available photos (ordered)."""
        ...


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
                    from urllib.parse import quote
                    encoded = quote(str(rel.relative_to(self.photo_dir)), safe="/")
                    photos.append(Photo(url=f"{self.url_prefix}/{encoded}", caption=fname))
        return photos


class HttpSource:
    """Fetches a JSON catalog from a remote URL and yields the images it lists.

    The catalog shape is deliberately simple and source-agnostic:
        { "photos": [ {"url": "https://.../a.jpg", "caption": "..."}, ... ] }

    Point this at a self-hosted Immich/PhotoPrism album proxy or any endpoint
    that responds with that shape. This is how "the cloud" plugs in without a
    vendor OAuth dance in the engine.
    """
    name = "http"

    def __init__(self, catalog_url: str, timeout: float = 10.0):
        self.catalog_url = catalog_url
        self.timeout = timeout

    def list(self) -> List[Photo]:
        if not self.catalog_url:
            return []
        try:
            with urllib.request.urlopen(self.catalog_url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
        photos: List[Photo] = []
        for item in data.get("photos", []):
            url = item.get("url") or item.get("src")
            if url:
                photos.append(Photo(url=url, caption=item.get("caption", "")))
        return photos


class GooglePhotosSource:
    """Google Photos via the Library API (OAuth2, read-only).

    Auth: a Google Cloud OAuth2 client (client_id + client_secret) plus a
    refresh token that grants access to YOUR OWN Photos library. These are
    real secrets — provide them by file/env, not baked into the source, and
    never into the vault/notes.

    Rendering: the Library API returns a per-media ``baseUrl`` on Google's CDN
    (lh3.googleusercontent.com). The frame page loads those directly in an
    <img>, which works cross-origin (no proxy, no CORS needed for images). We
    append ``=w<width>-h<height>`` to get a downscaled render.

    Since photos come from your own authenticated library the URLs are
    credentialed; a ``+h<height>`` suffix is the standard way to request a
    servable variant.
    """

    name = "google-photos"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    MEDIA_URL = "https://photoslibrary.googleapis.com/v1/mediaItems"
    ALBUM_URL = "https://photoslibrary.googleapis.com/v1/albums"
    MAX_WIDTH = 1920
    MAX_HEIGHT = 1200

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
        album_id: str = "",
        timeout: float = 10.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.album_id = album_id
        self.timeout = timeout
        import time
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    def _authorized(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _refresh_token(self) -> Optional[str]:
        import json as _json
        import urllib.parse
        import urllib.request
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        req = urllib.request.Request(
            self.TOKEN_URL,
            data=urllib.parse.urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        token = data.get("access_token")
        if token:
            import time
            self._access_token = token
            # Refresh tokens are usually valid 3600s; be conservative.
            self._expires_at = time.time() + int(data.get("expires_in", 3600)) - 120
        return token

    def _headers(self):
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get_json(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        import json as _json
        import time
        import urllib.parse
        import urllib.request
        if not self._access_token or time.time() >= self._expires_at:
            if not self._refresh_token():
                return None
        q = urllib.parse.urlencode(params or {})
        full = url if not q else f"{url}?{q}"
        req = urllib.request.Request(full, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _media_url(self) -> str:
        # Media items for the whole library, or a specific album.
        if self.album_id:
            return f"{self.ALBUM_URL}/{self.album_id}"
        return self.MEDIA_URL

    def list(self) -> List[Photo]:
        if not self._authorized():
            return []
        # _get_json refreshes the access token only when needed (cached
        # otherwise), so no unconditional refresh here.
        items = self._get_json(self._media_url(), {"pageSize": "100"})
        if not items:
            return []
        photos: List[Photo] = []
        media = items.get("mediaItems", [])
        for it in media:
            url = it.get("baseUrl")
            if not url:
                continue
            render = f"{url}=w{self.MAX_WIDTH}-h{self.MAX_HEIGHT}"
            photos.append(Photo(url=render, caption=it.get("filename", "")))
        return photos


def get_source(config) -> Source:
    """Factory: maps config.photo_source to a Source instance."""
    if config.photo_source == "http":
        return HttpSource(config.photo_catalog_url, config.http_proxy_timeout)
    if config.photo_source == "google-photos":
        return GooglePhotosSource(
            client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            refresh_token=config.google_refresh_token,
            album_id=config.google_album_id,
            timeout=config.http_proxy_timeout,
        )
    # default: local
    return LocalSource(config.photo_dir)