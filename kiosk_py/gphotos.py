"""Google Photos OAuth bridge — the engine triggers the host rclone via files.

The engine is a container and can't run rclone (which lives on the host). So it
drives the host auth service through the shared config dir:

  engine writes gphotos-auth-trigger  ->  host .path watcher starts the service
  host service runs kiosk-gphotos-sync.sh auth-run
  auth-run launches rclone + a SEPARATE browser window on the kiosk display
  user approves in that window -> rclone stores the token
  auth-run writes gphotos-configured when done
  engine reads gphotos-configured to report status
"""
from __future__ import annotations

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

    def _configured(self) -> bool:
        p = self._path("gphotos-configured")
        return p.is_file()

    def start(self) -> Dict[str, Any]:
        """Trigger the auth service (it opens the login window on the kiosk)."""
        self._path("gphotos-configured").unlink(missing_ok=True)
        self._trigger()
        return {"ok": True, "message": "login window opened on the kiosk"}

    def status(self) -> Dict[str, Any]:
        """Whether OAuth is configured (host wrote the configured marker)."""
        return {"configured": self._configured()}
