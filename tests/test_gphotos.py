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


def test_read_url_empty_when_missing():
    b = _bridge()
    assert b._read_url() == ""


def test_read_url_returns_content():
    b = _bridge()
    b._path("gphotos-open").write_text("http://127.0.0.1:53682/auth?state=x")
    assert b._read_url() == "http://127.0.0.1:53682/auth?state=x"


def test_start_returns_url_when_host_writes_open():
    b = _bridge()
    # Simulate the host writing gphotos-open after the trigger.
    def fake_trigger():
        b._path("gphotos-open").write_text("http://127.0.0.1:53682/auth?state=x")
    b._trigger = fake_trigger
    res = b.start(timeout=5)
    assert res["ok"] is True
    assert "53682" in res["url"]


def test_start_times_out_when_no_url():
    b = _bridge()
    b._trigger = lambda: None
    res = b.start(timeout=1)
    assert res["ok"] is False
    assert "timed out" in res["error"]


def test_status_not_configured_by_default():
    b = _bridge()
    assert b.status() == {"configured": False}


def test_status_configured_when_marker_present():
    b = _bridge()
    b._path("gphotos-configured").write_text("configured")
    assert b.status() == {"configured": True}


def test_clear_open_removes_file():
    b = _bridge()
    b._path("gphotos-open").write_text("http://x")
    assert b._path("gphotos-open").is_file()
    b.clear_open()
    assert not b._path("gphotos-open").is_file()