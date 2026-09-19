"""Tests for the config store (web-service persistence)."""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from kiosk_py.config_store import ConfigStore, EDITABLE_FIELDS


def test_empty_store_returns_defaults():
    with tempfile.TemporaryDirectory() as td:
        store = ConfigStore(str(Path(td) / "kiosk.json"))
        assert store.load() == {}
        state = store.public_state()
        assert state["ha_url"] == "http://localhost:8123"
        assert state["idle_timeout_seconds"] == 120


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        store = ConfigStore(str(Path(td) / "kiosk.json"))
        store.save({"ha_url": "http://192.168.20.12:8123", "idle_timeout_seconds": 30})
        assert store.load()["ha_url"] == "http://192.168.20.12:8123"
        assert store.load()["idle_timeout_seconds"] == 30


def test_save_filters_unknown_keys():
    with tempfile.TemporaryDirectory() as td:
        store = ConfigStore(str(Path(td) / "kiosk.json"))
        store.save({"ha_url": "http://x", "not_a_field": "should-be-dropped"})
        assert "not_a_field" not in store.load()


def test_effective_config_applies_overrides():
    with tempfile.TemporaryDirectory() as td:
        store = ConfigStore(str(Path(td) / "kiosk.json"))
        store.save({"idle_timeout_seconds": 45})
        cfg = store.effective_config()
        assert cfg.idle_timeout_seconds == 45
        # non-overridden field keeps env default
        assert cfg.ha_url == "http://localhost:8123"


def test_editable_fields_are_whitelisted():
    # No secrets should ever be editable via the web service.
    assert "ha_url" in EDITABLE_FIELDS
    assert "idle_timeout_seconds" in EDITABLE_FIELDS
    for k in EDITABLE_FIELDS:
        assert "token" not in k and "key" not in k and "secret" not in k and "password" not in k