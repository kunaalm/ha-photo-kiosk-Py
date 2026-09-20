"""Tests for the Google Photos sync store (kiosk_py/sync.py)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.sync import SyncStore


def _store():
    d = tempfile.mkdtemp()
    return SyncStore(d)


def test_defaults_ok():
    s = _store()
    cfg = s.load_config()
    assert cfg["enabled"] is False
    assert cfg["remote"] == "gphotos"
    assert cfg["source_path"] == "media/by-month"


def test_save_and_load_config_merges():
    s = _store()
    s.save_config({"enabled": True, "remote": "myphotos", "source_path": "media/all"})
    cfg = s.load_config()
    assert cfg["enabled"] is True
    assert cfg["remote"] == "myphotos"
    assert cfg["source_path"] == "media/all"


def test_save_only_allows_known_fields():
    s = _store()
    s.save_config({"enabled": True, "evil": "x"})
    cfg = s.load_config()
    assert "evil" not in cfg


def test_status_missing_is_idle():
    s = _store()
    st = s.status()
    assert st.get("state") == "idle"


def test_request_sync_writes_trigger():
    s = _store()
    s.request_sync()
    assert s._path("sync-trigger").is_file()
    assert s._path("sync-trigger").read_text().strip() == "sync-now"


def test_public_state_combines_config_and_status():
    s = _store()
    s.save_config({"enabled": True})
    ps = s.public_state()
    assert ps["enabled"] is True
    assert ps["remote"] == "gphotos"
    assert ps["photos_dir"] == "/photos"