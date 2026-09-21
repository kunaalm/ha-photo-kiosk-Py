"""Google Photos OAuth bridge — the engine triggers the host rclone via files.

The engine is a container and can't run rclone (which lives on the host). So it
drives the host auth service through the shared config dir:

  engine writes gphotos-auth-trigger  ->  host .path watcher starts the service
  host service runs kiosk-gphotos-sync.sh auth-run (rclone in foreground)
  rclone prints the OAuth URL -> host writes it to gphotos-open
  supervisor points Chromium at gphotos-open (approval on the kiosk screen)
  engine reads gphotos-open to get the URL, and checks rclone.conf for the token
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

    def _trigger(self) -> None:
        """Write the trigger that starts the host auth service."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path("gphotos-auth-trigger").write_text("start\n")

    def _read_url(self) -> str:
        p = self._path("gphotos-open")
        if not p.is_file():
            return ""
        try:
            return p.read_text().strip()
        except OSError:
            return ""

    def _configured(self) -> bool:
        # The host writes gphotos-auth-result.json with {"configured": bool}
        # after auth-status; but simpler: the token lives in rclone.conf on the
        # host, which the engine can't read. So we rely on the host writing a
        # marker when OAuth completes. The auth-run service clears gphotos-open
        # on completion; we treat "open file gone after being present" as done.
        # For a robust signal, the host also writes gphotos-configured when the
        # token lands. Fall back to the open-file heuristic.
        p = self._path("gphotos-configured")
        if p.is_file():
            return True
        return False

    def start(self, timeout: float = 20.0) -> Dict[str, Any]:
        """Trigger the auth service and wait for the OAuth URL."""
        self._path("gphotos-open").unlink(missing_ok=True)
        self._trigger()
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = self._read_url()
            if url:
                return {"ok": True, "url": url}
            time.sleep(0.5)
        return {"ok": False, "error": "timed out waiting for OAuth URL"}

    def status(self) -> Dict[str, Any]:
        """Whether OAuth is configured (host wrote the configured marker)."""
        return {"configured": self._configured()}

    def clear_open(self) -> None:
        """Clear the gphotos-open file (OAuth done; supervisor returns to /frame/)."""
        self._path("gphotos-open").unlink(missing_ok=True)
