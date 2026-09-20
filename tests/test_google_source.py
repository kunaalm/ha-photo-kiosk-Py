"""Tests for the Google Photos Ambient source (mocked — no live credentials).

These pin the Ambient API contract we built against (verified from the live
discovery document https://photosambient.googleapis.com/$discovery/rest):
    - device-code OAuth (device/code + device_code grant) → photosambient scope
    - POST /v1/devices  {displayName} → AmbientDevice
    - GET  /v1/devices/{id} → mediaSourcesSet flag
    - GET  /v1/mediaItems?deviceId=.. (& pageToken/pageSize) → ListMediaItemsResponse
    - mediaFile.baseUrl needs the bearer token in the request HEADER
"""
import os
import sys
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kiosk_py.sources import AmbientSource, AmbientAuth, get_source


def _fake_resp(data):
    class Resp:
        def read(self):
            import json
            return json.dumps(data).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return Resp()


def _router(req, timeout=10):
    """Route urlopen calls based on the URL."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "device/code" in url:
        return _fake_resp({
            "device_code": "DEVICE-CODE-123", "user_code": "ABC-DEF",
            "verification_url": "https://photos.google.com/device_verify",
            "expires_in": 1800, "interval": 5,
        })
    if "oauth2.googleapis.com/token" in url:
        return _fake_resp({"access_token": "fake-access", "expires_in": 3600})
    if url.rstrip("/").endswith("/v1/devices") or "/v1/devices?" in url:
        return _fake_resp({"id": "DEV-1", "displayName": "Photo Frame",
                           "mediaSourcesSet": False, "pollingConfig": {"pollInterval": "5s"}})
    if "/v1/devices/" in url:
        return _fake_resp({"id": "DEV-1", "displayName": "Photo Frame",
                           "mediaSourcesSet": True})
    if "/v1/mediaItems" in url:
        return _fake_resp({"mediaItems": [
            {"id": "M1", "name": "mediaItems/M1",
             "mediaFile": {"baseUrl": "https://lh3.googleusercontent.com/aaa", "mimeType": "image/jpeg"}},
            {"id": "M2", "name": "mediaItems/M2",
             "mediaFile": {"baseUrl": "https://lh3.googleusercontent.com/bbb", "mimeType": "image/jpeg"}},
        ]})
    return _fake_resp({})


def _setup_source(device_id="DEV-1"):
    s = AmbientSource(client_id="id", client_secret="sec",
                      refresh_token="refresh", device_id=device_id)
    return s


def test_not_configured_returns_empty():
    assert AmbientSource().list() == []


def test_device_code_flow_returned_user_code():
    a = AmbientAuth("id", "sec")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_router):
        info = a.start_device_code()
    assert info["user_code"] == "ABC-DEF"
    assert "verification_url" in info
    assert a._device_code == "DEVICE-CODE-123"


def test_device_code_poll_gets_token():
    a = AmbientAuth("id", "sec")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_router):
        a.start_device_code()
        assert a.poll_for_token() == "fake-access"


def _not_ready_device_router(req, timeout=10):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "/v1/devices/" in url and "/v1/mediaItems" not in url:
        return _fake_resp({"id": "DEV-1", "displayName": "Photo Frame",
                           "mediaSourcesSet": False})
    return _router(req, timeout)


def test_list_requires_ready_device():
    # device created but user hasn't picked sources → mediaSourcesSet false
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_not_ready_device_router):
        assert _setup_source().list() == []


def test_list_parses_media_items_as_proxy_urls():
    s = _setup_source()
    # satisfy the mediaSourcesSet check
    with unittest.mock.patch("urllib.request.urlopen", side_effect=_router):
        photos = s.list()
    assert len(photos) == 2
    # url points at the engine proxy route, NOT the raw CDN (header needed)
    assert photos[0].url.startswith("/gimg/")
    assert "lh3.googleusercontent.com%2Faaa" in photos[0].url
    assert "=w1920-h1200" in photos[0].url.replace("%3D", "=")


def test_fetch_image_bytes_adds_bearer_header():
    s = _setup_source()
    captured = {}
    class _CaptureResp:
        def read(self):
            return b"FAKEJPEGDATA"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    def capture(req, timeout=10):
        captured["auth"] = req.headers.get("Authorization")
        captured["url"] = getattr(req, "full_url", str(req))
        return _CaptureResp()
    # give it a cached access token so fetch doesn't need to refresh
    s.auth._access_token = "fake-access"
    import time
    s.auth._expires_at = time.time() + 3000
    import urllib.parse
    encoded = urllib.parse.quote("https://lh3.googleusercontent.com/ccc=w1920-h1200", safe="")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=capture):
        data = s.fetch_image_bytes(encoded)
    assert data == b"FAKEJPEGDATA"
    assert captured["auth"] == "Bearer fake-access"
    assert "ccc=w1920-h1200" in captured["url"]


def test_get_source_routes_google():
    class Cfg:
        photo_source = "google-photos"
        google_client_id = "id"
        google_client_secret = "sec"
        google_refresh_token = "tok"
        google_device_id = "DEV-1"
        http_proxy_timeout = 10
    s = get_source(Cfg())
    assert isinstance(s, AmbientSource)
    assert s.device_id == "DEV-1"


def test_refresh_uses_refresh_token_grant():
    a = AmbientAuth("id", "sec")
    a._refresh_token = "rt"
    calls = []
    def capture(req, timeout=10):
        calls.append({"url": getattr(req, "full_url", str(req)),
                      "data": req.data})
        return _fake_resp({"access_token": "new-token", "expires_in": 3600})
    with unittest.mock.patch("urllib.request.urlopen", side_effect=capture):
        tok = a.refresh_access_token()
    assert tok == "new-token"
    body = calls[0]["data"].decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=rt" in body