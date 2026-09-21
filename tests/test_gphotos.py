"""Tests for the Google Photos OAuth bridge (kiosk_py/gphotos.py)."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.gphotos import GphotosBridge


def _bridge():
    d = tempfile.mkdtemp()
    return GphotosBridge(d)


def test_trigger_writes_command_file():
    b = _bridge()
    b._trigger("start")
    assert b._path("gphotos-auth-trigger").is_file()
    assert b._path("gphotos-auth-trigger").read_text().strip() == "start"


def test_read_result_empty_when_missing():
    b = _bridge()
    assert b._read_result() == {}


def test_read_result_parses_json():
    b = _bridge()
    b._path("gphotos-auth-result.json").write_text(json.dumps({"ok": True, "url": "http://x"}))
    assert b._read_result() == {"ok": True, "url": "http://x"}


def test_start_returns_url_when_host_writes_result():
    b = _bridge()
    # Simulate the host writing the result after the trigger.
    def fake_trigger(cmd):
        b._path("gphotos-auth-result.json").write_text(
            json.dumps({"ok": True, "url": "http://127.0.0.1:53682/auth?state=x", "pid": 1}))
    b._trigger = fake_trigger
    res = b.start(timeout=5)
    assert res["ok"] is True
    assert "53682" in res["url"]


def test_start_times_out_when_no_result():
    b = _bridge()
    # Never write a result -> times out.
    b._trigger = lambda cmd: None
    res = b.start(timeout=1)
    assert res["ok"] is False
    assert "timed out" in res["error"]


def test_status_returns_configured():
    b = _bridge()
    def fake_trigger(cmd):
        b._path("gphotos-auth-result.json").write_text(json.dumps({"configured": True}))
    b._trigger = fake_trigger
    res = b.status(timeout=5)
    assert res["configured"] is True


def test_clear_open_removes_file():
    b = _bridge()
    b._path("gphotos-open").write_text("http://x")
    assert b._path("gphotos-open").is_file()
    b.clear_open()
    assert not b._path("gphotos-open").is_file()