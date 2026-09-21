"""Application configuration from environment variables.

All config is env-driven so the same engine binary runs as a bare process or
inside a container with identical behavior (KISS 12-factor).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass
class Config:
    """Runtime configuration.

    All fields have built-in defaults so the engine starts with zero config;
    every value is overridable by an environment variable (see ``from_env``),
    and a JSON file can override env via ``ConfigStore``.
    """
    # --- Home Assistant ---
    ha_url: str = "http://localhost:8123"
    strip_x_frame_options: bool = True     # required for cross-origin iframe

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080

    # --- Photo source ---
    photo_source: str = "local"            # local | http
    photo_dir: str = "/photos"             # source=local
    photo_catalog_url: str = ""            # source=http (URL to a JSON catalog)
    cache_dir: str = "/cache"
    slide_interval_seconds: int = 10

    # --- Idle / state machine ---
    idle_timeout_seconds: int = 120        # no input for this long → idle
    idle_fade_seconds: int = 1             # crossfade between layers

    # --- Misc ---
    http_proxy_timeout: float = 10.0

    # --- Google Photos (device-code OAuth) ---
    google_client_id: str = ""
    google_client_secret: str = ""

    source_specific: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables (defaults when unset)."""
        return cls(
            ha_url=os.getenv("HA_URL", "http://localhost:8123").rstrip("/"),
            strip_x_frame_options=_env_bool("STRIP_X_FRAME_OPTIONS", True),
            host=os.getenv("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8080),
            photo_source=os.getenv("PHOTO_SOURCE", "local"),
            photo_dir=os.getenv("PHOTO_DIR", "/photos"),
            photo_catalog_url=os.getenv("PHOTO_CATALOG_URL", ""),
            cache_dir=os.getenv("CACHE_DIR", "/cache"),
            slide_interval_seconds=_env_int("SLIDE_INTERVAL_SECONDS", 10),
            idle_timeout_seconds=_env_int("IDLE_TIMEOUT_SECONDS", 120),
            idle_fade_seconds=_env_int("IDLE_FADE_SECONDS", 1),
            http_proxy_timeout=float(os.getenv("HTTP_PROXY_TIMEOUT", "10")),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        )