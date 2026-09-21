"""Tests for the Google Photos OAuth bridge (kiosk_py/gphotos.py)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.gphotos import GphotosBridge


def _bridge():
    d = tempfile.mkdtemp()
    return GphotosBridge(d)


def test_trigger_writes_command_file():
    b = _bridge()
    b._trigger()
    assert b._path("gphotos-auth-trigger").is_file()
    assert b._path("gphotos-auth-trigger").read_text().strip() == "start"


def test_start_returns_ok_and_clears_marker():
    b = _bridge()
    b._path("gphotos-configured").write_text("configured")
    res = b.start()
    assert res["ok"] is True
    assert not b._path("gphotos-configured").is_file()


def test_status_not_configured_by_default():
    b = _bridge()
    assert b.status() == {"configured": False}


def test_status_configured_when_marker_present():
    b = _bridge()
    b._path("gphotos-configured").write_text("configured")
    assert b.status() == {"configured": True}