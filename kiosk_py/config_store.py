"""Persistent config store.

The engine reads config from env (12-factor) but also supports a JSON config
file that overrides env defaults, editable via the web config service. This
lets a kiosk be configured from a browser instead of only env vars.

Resolution order (highest wins):
  1. config file (written by the web service)
  2. env vars
  3. built-in defaults
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Config

# Fields that are safe to expose/edit via the web service (no secrets).
EDITABLE_FIELDS = {
    "ha_url": "str",
    "photo_source": "str",
    "photo_dir": "str",
    "photo_catalog_url": "str",
    "idle_timeout_seconds": "int",
    "slide_interval_seconds": "int",
    "idle_fade_seconds": "int",
    "strip_x_frame_options": "bool",
}


class ConfigStore:
    """Persists a JSON config file (web-editable) on top of env defaults.

    ``save`` merges with the existing file so a partial POST (e.g. only
    idle_timeout) doesn't clobber fields that weren't posted (like ha_url).
    Only non-secret fields (EDITABLE_FIELDS) are ever read/written.
    """

    def __init__(self, path: str = "/config/kiosk.json"):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        """Read the config file as a dict ({} if missing or corrupt)."""
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        """Merge ``data`` (whitelisted fields only) into the config file.

        The merge (not replace) is what makes the web UI safe to use with
        partial saves.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with the existing file so a partial POST (e.g. only
        # idle_timeout) doesn't clobber unposted fields like ha_url. Only
        # editable fields are persisted.
        clean = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
        merged = self.load()
        merged.update(clean)
        self.path.write_text(json.dumps(merged, indent=2))

    def effective_config(self) -> Config:
        """Config with file overrides applied on top of env defaults."""
        cfg = Config.from_env()
        overrides = self.load()
        for k, v in overrides.items():
            if k in EDITABLE_FIELDS and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    def public_state(self) -> Dict[str, Any]:
        """Current effective values for the editable fields (for the web UI)."""
        cfg = self.effective_config()
        return {k: getattr(cfg, k) for k in EDITABLE_FIELDS}