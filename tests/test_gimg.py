"""Tests for the /gimg/ Google-photo proxy endpoint (Ambient API rendering).

Ambient mediaFile.baseUrl values need the bearer token in the request
header — a bare <img> can't attach it, so the engine fetches the bytes
authenticated and streams them back under /gimg/<encoded URL>. These tests
pin: 404 when the source isn't google-photos, and a streamed body + fetched
URL when it is.
"""
import asyncio
import sys
import os
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.server import KioskServer
from kiosk_py import config as cfgmod


class _FakeMatchInfo(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class _FakeRequest:
    def __init__(self, path):
        self.match_info = _FakeMatchInfo(path=path)


def _server(source):
    cfg = cfgmod.Config()
    s = KioskServer(cfg)
    s.source = source
    return s


class _Localish:
    name = "local"


class _Ambient:
    name = "google-photos"
    def __init__(self): self._data = None
    def fetch_image_bytes(self, encoded_url):
        return b"HELLO-GOOGLE-IMAGE"


def test_gimg_404_when_not_google_source():
    from aiohttp import web
    s = _server(_Localish())
    async def run():
        try:
            await s.serve_google_image(_FakeRequest("x"))
        except web.HTTPNotFound:
            return 404
        return 200
    assert asyncio.run(run()) == 404


def test_gimg_streams_bytes_for_google_source():
    src = _Ambient()
    s = _server(src)
    async def run():
        resp = await s.serve_google_image(_FakeRequest("encoded"))
        return resp.status, resp.body, resp.content_type
    status, body, ctype = asyncio.run(run())
    assert status == 200
    assert body == b"HELLO-GOOGLE-IMAGE"
    assert ctype.startswith("image/")


def test_gimg_502_when_fetch_fails():
    from aiohttp import web
    class _Fail:
        name = "google-photos"
        def fetch_image_bytes(self, encoded_url):
            return None
    s = _server(_Fail())
    async def run():
        try:
            await s.serve_google_image(_FakeRequest("encoded"))
        except web.HTTPBadGateway:
            return 502
        return 200
    assert asyncio.run(run()) == 502