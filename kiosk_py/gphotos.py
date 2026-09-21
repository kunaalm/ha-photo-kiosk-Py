"""Google Photos OAuth bridge — the engine triggers the host rclone via files.

The engine is a container and can't run rclone (which lives on the host). So
it drives the host sync script through the shared config dir:

  engine writes gphotos-auth-trigger  ->  host .path watcher fires
  host runs kiosk-gphotos-sync.sh auth-bridge
  host writes gphotos-auth-result.json -> engine reads it

The host also writes gphotos-open (the OAuth URL) so the supervisor points
Chromium at it for approval on the kiosk screen.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


class GphotosBridge:
    """File-based bridge to the host rclone OAuth flow."""

    def __init__(self, config_dir: str = "/config"):
        self.dir = Path(config_dir)

    def _path(self, name: str) -> Path:
        return self.dir / name

    def _trigger(self, command: str) -> None:
        """Write a command for the host auth-bridge to run."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path("gphotos-auth-trigger").write_text(command + "\n")

    def _read_result(self) -> Dict[str, Any]:
        p = self._path("gphotos-auth-result.json")
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def start(self, timeout: float = 15.0) -> Dict[str, Any]:
        """Trigger auth-start and wait for the OAuth URL."""
        self._path("gphotos-auth-result.json").unlink(missing_ok=True)
        self._trigger("start")
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = self._read_result()
            if res.get("ok") and res.get("url"):
                return res
            time.sleep(0.5)
        return {"ok": False, "error": "timed out waiting for OAuth URL"}

    def status(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Trigger auth-status and return whether OAuth is configured."""
        self._path("gphotos-auth-result.json").unlink(missing_ok=True)
        self._trigger("status")
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = self._read_result()
            if "configured" in res:
                return res
            time.sleep(0.5)
        return {"configured": False}

    def clear_open(self) -> None:
        """Clear the gphotos-open file (OAuth done; supervisor returns to /frame/)."""
        self._path("gphotos-open").unlink(missing_ok=True)
