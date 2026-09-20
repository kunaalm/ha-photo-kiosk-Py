"""Tests that the config/upload endpoints require basic auth."""
import asyncio
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.server import KioskServer
from kiosk_py import config as cfgmod
from kiosk_py.auth import AuthStore


def _server():
    d = tempfile.mkdtemp()
    auth = AuthStore(os.path.join(d, "auth.json"))
    auth.set_password("kiosk", "secret", must_change=False)
    cfg = cfgmod.Config()
    s = KioskServer(cfg, auth_store=auth)
    return s


def _auth_header(user="kiosk", pw="secret"):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.match_info = {}
        self.query_string = ""
        self.path = "/config/"


def test_config_page_requires_auth():
    s = _server()
    async def run():
        r = await s.serve_config_page(_Req())
        return r.status
    assert asyncio.run(run()) == 401


def test_config_page_ok_with_auth():
    s = _server()
    async def run():
        r = await s.serve_config_page(_Req({"Authorization": _auth_header()}))
        return r.status
    assert asyncio.run(run()) == 200


def test_config_page_rejects_wrong_password():
    s = _server()
    async def run():
        r = await s.serve_config_page(_Req({"Authorization": _auth_header(pw="nope")}))
        return r.status
    assert asyncio.run(run()) == 401


def test_photos_list_requires_auth():
    s = _server()
    async def run():
        r = await s.serve_photos_list(_Req())
        return r.status
    assert asyncio.run(run()) == 401


def test_photos_list_ok_with_auth():
    s = _server()
    async def run():
        r = await s.serve_photos_list(_Req({"Authorization": _auth_header()}))
        return r.status
    assert asyncio.run(run()) == 200