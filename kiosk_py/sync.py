"""Google Photos sync status + control, read by the config UI.

The kiosk shows cloud photos by syncing them into the local photo folder with
an external tool (rclone) on the host. The engine doesn't talk to Google —
it only reads/writes a small state file in the shared config dir, which a
host-side sync service owns.

Files (all under CONFIG_DIR, mounted into the engine at /config):
  sync.json          — config: enabled, remote, source path (UI-editable)
  sync-status.json   — status the sync service writes (last run, error, count)
  sync-trigger       — marker the UI writes to request an immediate sync

The engine never runs the sync itself; the host service (kiosk-gphotos-sync)
does. This keeps the container non-privileged and free of any cloud API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_SYNC_CONFIG = {
    "enabled": False,
    "remote": "gphotos",          # rclone remote name
    "source_path": "media/by-month",
}


class SyncStore:
    """Reads/writes the sync config + status files in the shared config dir."""

    def __init__(self, config_dir: str = "/config"):
        self.dir = Path(config_dir)

    def _path(self, name: str) -> Path:
        return self.dir / name

    # --- config (UI-editable) ---
    def load_config(self) -> Dict[str, Any]:
        p = self._path("sync.json")
        if not p.is_file():
            return dict(DEFAULT_SYNC_CONFIG)
        try:
            return {**DEFAULT_SYNC_CONFIG, **json.loads(p.read_text())}
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_SYNC_CONFIG)

    def save_config(self, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        merged = self.load_config()
        # Only allow known fields.
        for k in DEFAULT_SYNC_CONFIG:
            if k in data:
                merged[k] = data[k]
        self._path("sync.json").write_text(json.dumps(merged, indent=2))

    # --- status (sync service writes) ---
    def status(self) -> Dict[str, Any]:
        p = self._path("sync-status.json")
        if not p.is_file():
            return {"state": "idle", "last_run": None, "last_error": None,
                    "photo_count": 0, "configured": False}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {"state": "unknown"}

    # --- trigger (UI -> host) ---
    def request_sync(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path("sync-trigger").write_text("sync-now\n")

    # --- combined view for the UI ---
    def public_state(self) -> Dict[str, Any]:
        cfg = self.load_config()
        st = self.status()
        st.update({
            "enabled": bool(cfg["enabled"]),
            "remote": cfg["remote"],
            "source_path": cfg["source_path"],
            "photos_dir": "/photos",
        })
        return st
