"""Tests for the Google Photos sync endpoints (auth-gated)."""
import asyncio
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.server import KioskServer
from kiosk_py import config as cfgmod
from kiosk_py.auth import AuthStore
from kiosk_py.sync import SyncStore


def _server():
    d = tempfile.mkdtemp()
    auth = AuthStore(os.path.join(d, "auth.json"))
    auth.set_password("kiosk", "secret", must_change=False)
    sync = SyncStore(os.path.join(d, "config"))
    cfg = cfgmod.Config()
    return KioskServer(cfg, auth_store=auth, sync_store=sync)


def _auth_header(pw="secret"):
    return "Basic " + base64.b64encode(f"kiosk:{pw}".encode()).decode()


class _Req:
    def __init__(self, url="", headers=None, json_body=None):
        self.url = url
        self.headers = headers or {}
        self.match_info = {}
        self.query_string = ""
        self.path = url or "/"
        self._json = json_body

    async def json(self):
        return self._json or {}


def test_get_sync_requires_auth():
    s = _server()
    async def run():
        r = await s.get_sync(_Req())
        return r.status
    assert asyncio.run(run()) == 401


def test_get_sync_ok_with_auth():
    import json as _json
    s = _server()
    async def run():
        r = await s.get_sync(_Req(headers={"Authorization": _auth_header()}))
        return r.status, _json.loads(r.body or b"{}")
    status, body = asyncio.run(run())
    assert status == 200
    assert body["enabled"] is False
    assert body["remote"] == "gphotos"


def test_post_sync_requires_auth():
    s = _server()
    async def run():
        r = await s.post_sync(_Req())
        return r.status
    assert asyncio.run(run()) == 401


def test_post_sync_saves():
    s = _server()
    async def run():
        r = await s.post_sync(_Req(headers={"Authorization": _auth_header()},
                                  json_body={"enabled": True, "remote": "ph"}))
        return r.status
    assert asyncio.run(run()) == 200
    assert s.sync.load_config()["enabled"] is True


def test_trigger_sync_requires_auth():
    s = _server()
    async def run():
        r = await s.trigger_sync(_Req())
        return r.status
    assert asyncio.run(run()) == 401


def test_trigger_sync_writes_trigger():
    s = _server()
    async def run():
        r = await s.trigger_sync(_Req(headers={"Authorization": _auth_header()}))
        return r.status
    assert asyncio.run(run()) == 200
    assert s.sync._path("sync-trigger").is_file()