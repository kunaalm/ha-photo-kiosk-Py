"""Tests for the Google Photos device-code OAuth (kiosk_py/gphotos.py)."""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.gphotos import GooglePhotosOAuth


def _oauth():
    d = tempfile.mkdtemp()
    return GooglePhotosOAuth(config_dir=d, client_id="test-client", client_secret="test-secret")


def test_not_configured_by_default():
    o = _oauth()
    assert o.is_configured() is False
    assert o.status() == {"configured": False}


def test_start_requires_client_id():
    o = GooglePhotosOAuth(config_dir=tempfile.mkdtemp(), client_id="", client_secret="")
    async def run():
        return await o.start()
    res = asyncio.run(run())
    assert res["ok"] is False
    assert "client" in res["error"]


def test_start_calls_device_endpoint(monkeypatch):
    o = _oauth()
    captured = {}

    class FakeResp:
        status = 200
        async def json(self):
            return {"device_code": "dc", "user_code": "UC-123",
                    "verification_url": "https://google.com/device",
                    "expires_in": 1800, "interval": 5}

    class FakeSession:
        def __init__(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None):
            captured["url"] = url
            captured["body"] = json
            return _Ctx(FakeResp())

    class _Ctx:
        def __init__(self, resp): self._r = resp
        async def __aenter__(self): return self._r
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("kiosk_py.gphotos.aiohttp.ClientSession", FakeSession)
    async def run():
        return await o.start()
    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["user_code"] == "UC-123"
    assert res["verification_url"] == "https://google.com/device"
    assert captured["url"] == "https://oauth2.googleapis.com/device/code"
    assert captured["body"]["client_id"] == "test-client"
    assert "photosambient.mediaitems" in captured["body"]["scope"]


def test_poll_no_flow_in_progress():
    o = _oauth()
    async def run():
        return await o.poll()
    res = asyncio.run(run())
    assert res["ok"] is False
    assert "no device flow" in res["error"]


def test_poll_pending(monkeypatch):
    o = _oauth()
    o._pending = {"device_code": "dc"}

    class FakeResp:
        status = 400
        async def json(self):
            return {"error": "authorization_pending"}

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None):
            return _Ctx(FakeResp())

    class _Ctx:
        def __init__(self, resp): self._r = resp
        async def __aenter__(self): return self._r
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("kiosk_py.gphotos.aiohttp.ClientSession", FakeSession)
    async def run():
        return await o.poll()
    res = asyncio.run(run())
    assert res["ok"] is False
    assert res["pending"] is True


def test_poll_success_stores_token(monkeypatch):
    o = _oauth()
    o._pending = {"device_code": "dc"}

    class FakeResp:
        status = 200
        async def json(self):
            return {"refresh_token": "rt", "access_token": "at", "expires_in": 3600, "scope": "s"}

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None):
            return _Ctx(FakeResp())

    class _Ctx:
        def __init__(self, resp): self._r = resp
        async def __aenter__(self): return self._r
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("kiosk_py.gphotos.aiohttp.ClientSession", FakeSession)
    async def run():
        return await o.poll()
    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["configured"] is True
    assert o.is_configured() is True
    tok = o._load_token()
    assert tok["refresh_token"] == "rt"


def test_create_device_requires_auth():
    o = _oauth()
    async def run():
        return await o.create_device()
    res = asyncio.run(run())
    assert res["ok"] is False
    assert "not authenticated" in res["error"]


def test_create_device_saves(monkeypatch):
    o = _oauth()
    o._save_token({"access_token": "at", "refresh_token": "rt"})

    class FakeResp:
        status = 200
        async def json(self):
            return {"id": "dev-1", "displayName": "HA Photo Kiosk",
                    "mediaSourcesSet": False, "settingsUri": "https://photos.google.com/dev"}

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None, headers=None):
            return _Ctx(FakeResp())

    class _Ctx:
        def __init__(self, resp): self._r = resp
        async def __aenter__(self): return self._r
        async def __aexit__(self, *a): return False

    monkeypatch.setattr("kiosk_py.gphotos.aiohttp.ClientSession", FakeSession)
    async def run():
        return await o.create_device()
    res = asyncio.run(run())
    assert res["ok"] is True
    assert res["device"]["id"] == "dev-1"
    assert o._load_device()["id"] == "dev-1"


def test_disconnect_removes_token_and_device():
    o = _oauth()
    o._save_token({"refresh_token": "rt"})
    o._save_device({"id": "dev-1"})
    assert o.is_configured() is True
    o.disconnect()
    assert o.is_configured() is False
    assert o._load_device() is None