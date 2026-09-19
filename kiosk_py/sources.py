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


def get_source(config) -> Source:
    """Factory: maps config.photo_source to a Source instance."""
    if config.photo_source == "http":
        return HttpSource(config.photo_catalog_url, config.http_proxy_timeout)
    # default: local
    return LocalSource(config.photo_dir)